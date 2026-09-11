"""Tests for the historical-import parsing and validation engine (Step 5C).

Pure-function tests: no database, no HTTP. The point of this layer is that it refuses to
guess when a real export is genuinely ambiguous, so most of these assert a *refusal*.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.history.fields import CANONICAL_FIELDS, REQUIRED_FIELDS, SENSITIVE_FIELDS
from app.history.mapping import inspect_ambiguity, normalize_header, suggest_mapping
from app.history.parsing import (
    AMBIGUOUS,
    DateOrder,
    DecimalStyle,
    ParseError,
    detect_date_order,
    detect_decimal_style,
    parse_bool,
    parse_date,
    parse_float,
    parse_int,
    parse_list,
    parse_money_cents,
)
from app.history.rows import ParseOptions, RowStatus, fingerprint, validate_row, validate_sheet
from app.history.tabular import Sheet

DOT = DecimalStyle.DOT
ISO = DateOrder.ISO


def sheet(headers, *rows) -> Sheet:
    return Sheet(
        headers=tuple(headers), rows=tuple(dict(zip(headers, r, strict=True)) for r in rows)
    )


class TestDateAmbiguity:
    def test_a_day_above_twelve_settles_the_column(self) -> None:
        assert detect_date_order(["03/04/2025", "15/04/2025"]) is DateOrder.DMY
        assert detect_date_order(["03/04/2025", "04/15/2025"]) is DateOrder.MDY

    def test_the_settling_row_may_be_anywhere(self) -> None:
        """Sampling the first rows would miss it; the detector scans the whole column."""
        values = ["01/02/2025"] * 400 + ["25/02/2025"]
        assert detect_date_order(values) is DateOrder.DMY

    def test_a_fully_ambiguous_column_is_refused(self) -> None:
        # 03/04 is March 4th or 4 March and nothing in the column decides.
        assert detect_date_order(["03/04/2025", "05/06/2025", "01/02/2024"]) == AMBIGUOUS

    def test_iso_and_named_months_are_never_ambiguous(self) -> None:
        assert detect_date_order(["2025-03-04"]) is DateOrder.ISO
        assert detect_date_order(["Mar 4, 2025"]) is DateOrder.ISO

    def test_real_date_objects_are_never_ambiguous(self) -> None:
        """A spreadsheet storing typed dates has already answered the question."""
        assert detect_date_order([date(2025, 3, 4), date(2025, 4, 11)]) is DateOrder.ISO
        assert detect_date_order([datetime(2025, 3, 4, 9, 0)]) is DateOrder.ISO

    def test_typed_dates_parse_straight_through(self) -> None:
        assert parse_date(date(2025, 3, 4), order=ISO) == date(2025, 3, 4)
        assert parse_date(datetime(2025, 3, 4, 9, 0), order=ISO) == date(2025, 3, 4)

    def test_parsing_honours_the_decided_order(self) -> None:
        assert parse_date("03/04/2025", order=DateOrder.MDY) == date(2025, 3, 4)
        assert parse_date("03/04/2025", order=DateOrder.DMY) == date(2025, 4, 3)

    def test_named_and_iso_formats_parse(self) -> None:
        for value in ("2025-03-04", "Mar 4, 2025", "4 March 2025", "March 4 2025"):
            assert parse_date(value, order=ISO) == date(2025, 3, 4)

    def test_two_digit_years_expand_to_this_century(self) -> None:
        assert parse_date("04/03/24", order=DateOrder.MDY) == date(2024, 4, 3)

    def test_impossible_dates_are_rejected(self) -> None:
        for value in ("2025-02-30", "13/13/2025", "not a date"):
            with pytest.raises(ParseError):
                parse_date(value, order=ISO)

    def test_blank_is_absent_not_an_error(self) -> None:
        assert parse_date("", order=ISO) is None
        assert parse_date(None, order=ISO) is None


class TestMoneyAmbiguity:
    def test_both_separators_settle_the_style(self) -> None:
        assert detect_decimal_style(["1,234.56"]) is DecimalStyle.DOT
        assert detect_decimal_style(["1.234,56"]) is DecimalStyle.COMMA

    def test_a_lone_three_digit_group_is_refused(self) -> None:
        """'1.234' is 1234 euros or 1.234 dollars — a 1000x error if guessed wrong."""
        assert detect_decimal_style(["1.234"]) == AMBIGUOUS
        assert detect_decimal_style(["1,234"]) == AMBIGUOUS

    def test_a_decisive_value_elsewhere_resolves_the_column(self) -> None:
        assert detect_decimal_style(["1.234", "99.50"]) is DecimalStyle.DOT

    def test_plain_integers_need_no_decision(self) -> None:
        assert detect_decimal_style(["1200", "980"]) is DecimalStyle.DOT

    def test_parsing_produces_cents(self) -> None:
        assert parse_money_cents("$1,234.56", style=DOT) == 123_456
        assert parse_money_cents("1.234,56", style=DecimalStyle.COMMA) == 123_456
        assert parse_money_cents("980", style=DOT) == 98_000

    def test_currency_symbols_and_spaces_are_tolerated(self) -> None:
        assert parse_money_cents("  $ 1,200.00 ", style=DOT) == 120_000

    def test_negative_money_is_rejected(self) -> None:
        with pytest.raises(ParseError, match="negative"):
            parse_money_cents("-50.00", style=DOT)

    def test_blank_is_absent(self) -> None:
        assert parse_money_cents("", style=DOT) is None


class TestOtherParsers:
    @pytest.mark.parametrize("value", ["yes", "Y", "TRUE", "1", "x", "✓"])
    def test_truthy_spellings(self, value: str) -> None:
        assert parse_bool(value) is True

    @pytest.mark.parametrize("value", ["no", "N", "FALSE", "0", "-"])
    def test_falsy_spellings(self, value: str) -> None:
        assert parse_bool(value) is False

    def test_blank_boolean_is_unknown_not_false(self) -> None:
        """Unknown and False are different facts; conflating them fakes evidence."""
        assert parse_bool("") is None

    def test_unparseable_boolean_raises(self) -> None:
        with pytest.raises(ParseError):
            parse_bool("maybe")

    def test_durations_in_real_formats(self) -> None:
        assert parse_float("7:30", field="h") == 7.5
        assert parse_float("7.5 hrs", field="h") == 7.5
        assert parse_float("7", field="h") == 7.0

    def test_spreadsheet_floats_become_integers(self) -> None:
        assert parse_int("3.0", field="crew") == 3

    def test_range_bounds_reject_the_impossible(self) -> None:
        with pytest.raises(ParseError, match="above the maximum"):
            parse_int("90", field="crew", maximum=20)
        with pytest.raises(ParseError, match="below the minimum"):
            parse_float("-4", field="hours", minimum=0)

    def test_lists_split_on_common_separators(self) -> None:
        assert parse_list("piano; gun safe, pool table") == ["piano", "gun safe", "pool table"]
        assert parse_list("") is None


class TestFieldRegistry:
    def test_there_is_nowhere_to_put_pii(self) -> None:
        """The privacy boundary is structural: no destination field exists."""
        names = {f.name for f in CANONICAL_FIELDS}
        for banned in ("name", "customer_name", "email", "phone", "card", "payment_method"):
            assert banned not in names

    def test_there_is_no_company_id_field(self) -> None:
        assert "company_id" not in {f.name for f in CANONICAL_FIELDS}

    def test_only_street_addresses_are_sensitive(self) -> None:
        assert {"origin_line1", "destination_line1"} == SENSITIVE_FIELDS

    def test_required_minimum_is_small(self) -> None:
        assert REQUIRED_FIELDS == ("move_date", "home_size")


class TestMappingSuggestions:
    def test_first_company_export_style(self) -> None:
        mapping = suggest_mapping(("Move Date", "Men", "Hours", "Total", "From Zip", "To Zip"))
        assert mapping["move_date"] == "Move Date"
        assert mapping["actual_crew_size"] == "Men"
        assert mapping["actual_hours"] == "Hours"
        assert mapping["actual_total_cents"] == "Total"
        assert mapping["origin_zip"] == "From Zip"
        assert mapping["destination_zip"] == "To Zip"

    def test_second_company_export_style(self) -> None:
        mapping = suggest_mapping(("JobDate", "CrewSize", "ActualDuration", "FinalAmount"))
        assert mapping["move_date"] == "JobDate"
        assert mapping["actual_crew_size"] == "CrewSize"
        assert mapping["actual_hours"] == "ActualDuration"
        assert mapping["actual_total_cents"] == "FinalAmount"

    def test_street_addresses_are_never_suggested(self) -> None:
        """Opt-in means a person has to choose them; a header match is not consent."""
        mapping = suggest_mapping(("Origin Line1", "origin_line1", "Street Address"))
        assert "origin_line1" not in mapping

    def test_unknown_headers_are_left_unmapped(self) -> None:
        mapping = suggest_mapping(("Customer Name", "Email", "Credit Card", "Salesperson"))
        assert mapping == {}

    def test_one_header_is_claimed_by_one_field(self) -> None:
        mapping = suggest_mapping(("Date",))
        assert list(mapping.values()).count("Date") == 1

    def test_headers_normalize_punctuation_and_case(self) -> None:
        assert normalize_header("  From ZIP. ") == "from zip"


class TestAmbiguityInspection:
    def test_clean_file_needs_no_input(self) -> None:
        data = sheet(["Move Date", "Total"], ["2025-03-04", "1,234.56"])
        report = inspect_ambiguity(data, {"move_date": "Move Date", "actual_total_cents": "Total"})
        assert report.needs_input is False
        assert report.date_order is DateOrder.ISO
        assert report.decimal_style is DecimalStyle.DOT

    def test_ambiguous_dates_are_reported(self) -> None:
        data = sheet(["Move Date"], ["03/04/2025"], ["05/06/2025"])
        report = inspect_ambiguity(data, {"move_date": "Move Date"})
        assert report.date_ambiguous is True
        assert report.needs_input is True

    def test_an_explicit_choice_overrides_detection(self) -> None:
        data = sheet(["Move Date"], ["03/04/2025"])
        report = inspect_ambiguity(data, {"move_date": "Move Date"}, date_order=DateOrder.DMY)
        assert report.date_ambiguous is False
        assert report.date_order is DateOrder.DMY

    def test_ambiguous_money_is_reported(self) -> None:
        data = sheet(["Total"], ["1.234"], ["2.500"])
        report = inspect_ambiguity(data, {"actual_total_cents": "Total"})
        assert report.money_ambiguous is True
        assert "actual_total_cents" in report.ambiguous_money_fields


MAPPING = {
    "move_date": "Move Date",
    "home_size": "Size",
    "actual_hours": "Hours",
    "actual_crew_size": "Men",
    "actual_total_cents": "Total",
}
HEADERS = ["Move Date", "Size", "Hours", "Men", "Total"]
PAST = (date.today() - timedelta(days=30)).isoformat()


def row(**over):
    base = {"Move Date": PAST, "Size": "2 bedroom", "Hours": "6.5", "Men": "3", "Total": "1,450.00"}
    base.update(over)
    return base


class TestRowValidation:
    def test_a_clean_row_parses(self) -> None:
        verdict = validate_row(row(), MAPPING, ParseOptions(), row_number=1)
        assert verdict.status is RowStatus.OK
        assert verdict.values["home_size"].value == "2br"
        assert verdict.values["actual_hours"] == 6.5
        assert verdict.values["actual_total_cents"] == 145_000

    def test_a_missing_required_field_rejects_the_row(self) -> None:
        verdict = validate_row(row(**{"Size": ""}), MAPPING, ParseOptions(), row_number=1)
        assert verdict.status is RowStatus.ERROR
        assert verdict.values == {}
        assert any(e.field == "home_size" for e in verdict.errors)

    def test_a_row_with_no_outcome_is_rejected(self) -> None:
        """Neither hours nor money: it records nothing we could learn from."""
        verdict = validate_row(
            row(**{"Hours": "", "Total": ""}), MAPPING, ParseOptions(), row_number=1
        )
        assert verdict.status is RowStatus.ERROR
        assert any("at least actual hours" in e.message for e in verdict.errors)

    def test_either_outcome_alone_is_enough(self) -> None:
        assert validate_row(row(**{"Total": ""}), MAPPING, ParseOptions(), row_number=1).importable
        assert validate_row(row(**{"Hours": ""}), MAPPING, ParseOptions(), row_number=1).importable

    def test_an_unreadable_optional_field_warns_and_still_imports(self) -> None:
        mapping = {**MAPPING, "origin_has_elevator": "Lift"}
        data = {**row(), "Lift": "sometimes"}
        verdict = validate_row(data, mapping, ParseOptions(), row_number=1)
        assert verdict.status is RowStatus.WARNING
        assert verdict.importable is True
        assert verdict.values["actual_hours"] == 6.5
        assert "origin_has_elevator" not in verdict.values

    def test_an_unreadable_required_field_rejects(self) -> None:
        verdict = validate_row(row(**{"Hours": "ages"}), MAPPING, ParseOptions(), row_number=1)
        assert verdict.status is RowStatus.ERROR

    @pytest.mark.parametrize("bad", [{"Hours": "-4"}, {"Total": "-100"}, {"Hours": "900"}])
    def test_impossible_outcomes_reject_the_row(self, bad) -> None:
        """Hours and money are the evidence; a nonsense value there poisons the row."""
        verdict = validate_row(row(**bad), MAPPING, ParseOptions(), row_number=1)
        assert verdict.status is RowStatus.ERROR
        assert verdict.values == {}

    @pytest.mark.parametrize("bad", [{"Men": "0"}, {"Men": "90"}, {"Men": "-3"}])
    def test_impossible_optional_values_are_dropped_not_stored(self, bad) -> None:
        """A 90-person crew usually means a mis-mapped column.

        The row still carries real hours and money, so it imports — but the nonsense is
        discarded rather than stored, and the warning is what tells the company their
        mapping is wrong. Rejecting the row instead would throw away good evidence over
        a column they may not even care about.
        """
        verdict = validate_row(row(**bad), MAPPING, ParseOptions(), row_number=1)
        assert verdict.status is RowStatus.WARNING
        assert verdict.importable is True
        assert "actual_crew_size" not in verdict.values
        assert verdict.values["actual_hours"] == 6.5
        assert any(w.field == "actual_crew_size" for w in verdict.warnings)

    def test_future_dates_are_rejected(self) -> None:
        future = (date.today() + timedelta(days=5)).isoformat()
        verdict = validate_row(row(**{"Move Date": future}), MAPPING, ParseOptions(), row_number=1)
        assert verdict.status is RowStatus.ERROR
        assert any("future" in e.message for e in verdict.errors)

    def test_unmapped_columns_are_ignored_entirely(self) -> None:
        """A company can upload their whole spreadsheet; only movement data enters."""
        data = {
            **row(),
            "Customer Name": "Jane Doe",
            "Email": "jane@example.com",
            "Phone": "555-0100",
            "Card": "4242424242424242",
            "company_id": "11111111-1111-1111-1111-111111111111",
        }
        verdict = validate_row(data, MAPPING, ParseOptions(), row_number=1)
        assert verdict.status is RowStatus.OK
        rendered = str(verdict.values)
        for leak in ("Jane", "jane@example.com", "555-0100", "4242", "11111111"):
            assert leak not in rendered
        assert "company_id" not in verdict.values

    def test_building_key_is_derived_when_an_address_is_mapped(self) -> None:
        mapping = {**MAPPING, "origin_line1": "Addr", "origin_zip": "Zip"}
        data = {**row(), "Addr": " 123 Main St ", "Zip": "62701"}
        verdict = validate_row(data, mapping, ParseOptions(), row_number=1)
        assert verdict.values["origin_building_key"] == "123 main st|62701"

    def test_no_building_key_without_an_address(self) -> None:
        verdict = validate_row(row(), MAPPING, ParseOptions(), row_number=1)
        assert "origin_building_key" not in verdict.values


class TestFingerprint:
    def test_identical_moves_share_a_fingerprint(self) -> None:
        a = validate_row(row(), MAPPING, ParseOptions(), row_number=1)
        b = validate_row(row(), MAPPING, ParseOptions(), row_number=9)
        assert a.fingerprint == b.fingerprint

    def test_a_different_move_differs(self) -> None:
        a = validate_row(row(), MAPPING, ParseOptions(), row_number=1)
        b = validate_row(row(**{"Hours": "9"}), MAPPING, ParseOptions(), row_number=1)
        assert a.fingerprint != b.fingerprint

    def test_extra_descriptive_columns_do_not_change_identity(self) -> None:
        """Re-exporting with a notes column must not double a company's history."""
        plain = validate_row(row(), MAPPING, ParseOptions(), row_number=1)
        with_notes = validate_row(
            {**row(), "Notes": "went fine"},
            {**MAPPING, "notes": "Notes"},
            ParseOptions(),
            row_number=1,
        )
        assert plain.fingerprint == with_notes.fingerprint

    def test_fingerprints_are_stable_across_runs(self) -> None:
        values = {"move_date": date(2025, 3, 4), "actual_hours": 6.5}
        assert fingerprint(values) == fingerprint(dict(values))


class TestSheetValidation:
    def test_duplicates_within_one_upload_are_caught(self) -> None:
        data = sheet(HEADERS, *[list(row().values())] * 3)
        verdicts = validate_sheet(data, MAPPING, ParseOptions())
        assert [v.status for v in verdicts] == [
            RowStatus.OK,
            RowStatus.DUPLICATE,
            RowStatus.DUPLICATE,
        ]

    def test_rows_already_in_history_are_skipped(self) -> None:
        data = sheet(HEADERS, list(row().values()))
        first = validate_sheet(data, MAPPING, ParseOptions())[0]
        again = validate_sheet(
            data, MAPPING, ParseOptions(), known_fingerprints=frozenset({first.fingerprint})
        )
        assert again[0].status is RowStatus.DUPLICATE

    def test_a_job_number_identifies_more_strongly_than_content(self) -> None:
        headers = [*HEADERS, "Job #"]
        mapping = {**MAPPING, "external_ref": "Job #"}
        data = sheet(headers, [*row().values(), "A-1"], [*row().values(), "A-2"])
        verdicts = validate_sheet(data, mapping, ParseOptions())
        # Identical content, different job numbers: two real moves, not a duplicate.
        assert [v.status for v in verdicts] == [RowStatus.OK, RowStatus.OK]

    def test_a_repeated_job_number_is_a_duplicate(self) -> None:
        headers = [*HEADERS, "Job #"]
        mapping = {**MAPPING, "external_ref": "Job #"}
        data = sheet(headers, [*row().values(), "A-1"], [*row(**{"Hours": "9"}).values(), "A-1"])
        verdicts = validate_sheet(data, mapping, ParseOptions())
        assert verdicts[1].status is RowStatus.DUPLICATE

    def test_bad_rows_do_not_stop_good_ones(self) -> None:
        data = sheet(
            HEADERS,
            list(row().values()),
            list(row(**{"Size": ""}).values()),
            list(row(**{"Hours": "8"}).values()),
        )
        verdicts = validate_sheet(data, MAPPING, ParseOptions())
        assert [v.status for v in verdicts] == [RowStatus.OK, RowStatus.ERROR, RowStatus.OK]

    def test_row_numbers_are_one_based_and_stable(self) -> None:
        data = sheet(HEADERS, list(row().values()), list(row(**{"Hours": "8"}).values()))
        assert [v.row_number for v in validate_sheet(data, MAPPING, ParseOptions())] == [1, 2]
