"""Build the semantic chunk index for manual knowledge entries.

Entries written before Step 7 have no chunks, because nothing was indexing them. This
script creates them. It is the one step between deploying Step 7 and being able to turn
shadow retrieval on: until it has run, the hybrid retriever can only see uploaded
documents, and a shadow comparison would blame it for missing answers it was never shown.

Usage (from backend/, venv active):

    python -m scripts.backfill_knowledge_index --all --dry-run
    python -m scripts.backfill_knowledge_index --all
    python -m scripts.backfill_knowledge_index --company acme-movers --force

Safe to re-run. An entry whose chunks already match its text under the current embedding
model is skipped without an API call, so a second run after an interruption costs one
cheap query per entry and finishes the job. ``--force`` re-indexes regardless, which is
what a chunking change or a model change needs.

Each entry's swap is atomic on its own and the script does not wrap them in one
transaction: an interrupted run leaves every entry either wholly on its old index or
wholly on its new one, never half-way.

It prints the database host it is about to write to and asks before doing so, because
the environment it is most often pointed at is production.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from sqlalchemy import inspect, make_url, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import Company, CompanyKnowledge
from app.providers.embeddings import resolve_embedding_provider
from app.services.indexing import BackfillReport, backfill_company, entry_is_current


def describe_target() -> str:
    """Host and database name only — never the user, never the password."""
    url = make_url(get_settings().database_url)
    return f"{url.get_backend_name()}://{url.host or 'local'}/{url.database or ''}"


def schema_is_ready(db: Session) -> bool:
    """Whether migration 0014 has been applied to this database.

    Checked before anything else touches the schema. Without it, running this against a
    database that has not been migrated yet produces an undefined-table error from deep
    inside a query — which is a confusing way to learn that the deploy order was wrong.
    """
    return inspect(db.get_bind()).has_table("knowledge_chunks")


def resolve_companies(db: Session, selector: str | None) -> list[Company]:
    """Every company, or the one named by slug or id."""
    statement = select(Company).order_by(Company.slug)
    if selector is None:
        return list(db.scalars(statement))

    try:
        company_id = uuid.UUID(selector)
    except ValueError:
        company = db.scalar(select(Company).where(Company.slug == selector))
    else:
        company = db.scalar(select(Company).where(Company.id == company_id))
    if company is None:
        raise LookupError(f"No company matching {selector!r}")
    return [company]


def plan(db: Session, company: Company, *, model: str, force: bool) -> tuple[int, int]:
    """What a run would do, without doing it: ``(to_index, already_current)``."""
    entries = list(
        db.scalars(
            select(CompanyKnowledge).where(CompanyKnowledge.company_id == company.id)
        )
    )
    if force:
        return len(entries), 0
    current = sum(1 for entry in entries if entry_is_current(db, entry, model=model))
    return len(entries) - current, current


def _report_line(company: Company, report: BackfillReport) -> str:
    status = "ok" if report.complete else "INCOMPLETE"
    return (
        f"  {company.slug:<24} entries={report.entries:<4} indexed={report.indexed:<4} "
        f"skipped={report.skipped:<4} chunks={report.chunks_written:<5} "
        f"embedded={report.embedded:<5} {status}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true", help="every company")
    target.add_argument("--company", help="one company, by slug or id")
    parser.add_argument(
        "--force", action="store_true", help="re-index even entries that are current"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change, write nothing"
    )
    parser.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    provider = resolve_embedding_provider(settings)
    model = getattr(provider, "model", None)

    print(f"database  : {describe_target()}")
    print(f"embeddings: {settings.embedding_provider} ({model or 'unavailable'})")
    if provider is None:
        # Chunks would be written with no vectors, and a later run would have to redo
        # them all. Better to stop than to half-build an index and call it done.
        print(
            "\nERROR: no embedding provider is configured, so this run would index "
            "text without vectors.\nSet EMBEDDING_PROVIDER and the API key first.",
            file=sys.stderr,
        )
        return 2
    if settings.embedding_provider.lower() == "fake" and not args.dry_run:
        print(
            "\nERROR: EMBEDDING_PROVIDER=fake produces placeholder vectors that are "
            "useless for\nsemantic search. Set EMBEDDING_PROVIDER=openai before "
            "backfilling a real database.",
            file=sys.stderr,
        )
        return 2

    session = get_sessionmaker()()
    try:
        if not schema_is_ready(session):
            print(
                "\nERROR: this database has no knowledge_chunks table, so migration 0014 "
                "has not been\napplied to it yet. Migrate first, then run this.",
                file=sys.stderr,
            )
            return 2

        companies = resolve_companies(session, None if args.all else args.company)
        if not companies:
            print("No companies found.")
            return 0

        print(f"\ncompanies : {len(companies)}")
        pending = 0
        for company in companies:
            to_index, current = plan(session, company, model=model or "", force=args.force)
            pending += to_index
            print(f"  {company.slug:<24} to index={to_index:<4} already current={current}")

        if args.dry_run:
            print(f"\nDry run: {pending} entries would be indexed. Nothing was written.")
            return 0
        if pending == 0:
            print("\nNothing to do: every entry is already current.")
            return 0
        if not args.yes:
            answer = input(f"\nIndex {pending} entries against the database above? [y/N] ")
            if answer.strip().lower() not in {"y", "yes"}:
                print("Aborted. Nothing was written.")
                return 1

        print()
        incomplete = 0
        for company in companies:
            report = backfill_company(
                session, company.id, provider=provider, force=args.force
            )
            print(_report_line(company, report))
            incomplete += int(not report.complete)

        if incomplete:
            print(
                f"\n{incomplete} companies did not complete — most likely the embedding "
                "provider errored.\nRe-run this command; finished entries are skipped.",
                file=sys.stderr,
            )
            return 1
        print("\nDone.")
        return 0
    except LookupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        session.close()


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
