"""Real files for the document-ingestion tests.

The point of these helpers is that nothing is mocked. ``make_pdf`` emits a structurally
valid PDF that ``pypdf`` genuinely parses, ``make_scanned_pdf`` emits one whose pages
carry no text layer — the shape of an actual scan — and ``make_docx`` is built by
``python-docx`` itself. Testing extraction against hand-made strings would prove only
that our own stubs round-trip.
"""

from __future__ import annotations

import io

#: A short company policy, written the way an owner would: headings, prose, a table.
POLICY_PAGES: list[list[str]] = [
    [
        "Cancellation Policy",
        "You may cancel a booked move at no charge up to 72 hours before the",
        "scheduled start time. Inside 72 hours we retain the deposit, because the",
        "crew and truck have already been committed to your date.",
        "Rescheduling is free once, at any notice, subject to availability.",
    ],
    [
        "Certificate of Insurance",
        "We provide Certificates of Insurance for buildings that require one. Send",
        "us the building management contact and the exact wording they need at",
        "least five business days before the move and we will arrange it.",
        "There is no charge for a standard COI.",
    ],
]


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(lines: list[str]) -> bytes:
    parts = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for line in lines:
        parts.append(f"({_escape(line)}) Tj")
        parts.append("T*")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1")


def _pdf(page_streams: list[bytes]) -> bytes:
    """Assemble a minimal but valid PDF with a correct cross-reference table."""
    count = len(page_streams)
    page_ids = [4 + 2 * i for i in range(count)]
    stream_ids = [5 + 2 * i for i in range(count)]

    bodies: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] "
            f"/Count {count} >>"
        ).encode("latin-1"),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page_id, stream_id, stream in zip(page_ids, stream_ids, page_streams, strict=True):
        bodies[page_id] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>"
        ).encode("latin-1")
        bodies[stream_id] = (
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(bodies):
        offsets[number] = out.tell()
        out.write(b"%d 0 obj\n" % number)
        out.write(bodies[number])
        out.write(b"\nendobj\n")

    xref_at = out.tell()
    total = max(bodies) + 1
    out.write(b"xref\n0 %d\n" % total)
    out.write(b"0000000000 65535 f \n")
    for number in range(1, total):
        out.write(b"%010d 00000 n \n" % offsets.get(number, 0))
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n" % (total, xref_at))
    out.write(b"%%EOF\n")
    return out.getvalue()


def make_pdf(pages: list[list[str]] | None = None) -> bytes:
    """A text-based PDF, one content stream per page."""
    return _pdf([_content_stream(lines) for lines in (pages or POLICY_PAGES)])


def make_scanned_pdf(page_count: int = 3) -> bytes:
    """Pages with no text layer — what a scan-to-PDF actually produces."""
    return _pdf([b"" for _ in range(page_count)])


def make_encrypted_pdf() -> bytes:
    """A password-protected PDF, produced by pypdf's own encryption."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(make_pdf())).pages:
        writer.add_page(page)
    writer.encrypt("a-password-we-will-never-have")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def make_docx(
    *, heading: str = "Cancellation Policy", table: bool = True, body: str | None = None
) -> bytes:
    """A Word document with a styled heading, prose, and optionally a rate table."""
    import docx

    document = docx.Document()
    document.add_heading(heading, level=1)
    document.add_paragraph(
        body
        or (
            "You may cancel a booked move at no charge up to 72 hours before the "
            "scheduled start time. Inside 72 hours we retain the deposit."
        )
    )
    if table:
        document.add_paragraph("Special item surcharges apply as follows.")
        grid = document.add_table(rows=3, cols=2)
        for row, (item, price) in enumerate(
            [("Item", "Surcharge"), ("Upright piano", "$250"), ("Gun safe", "$400")]
        ):
            grid.cell(row, 0).text = item
            grid.cell(row, 1).text = price
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


POLICY_TEXT = (
    "Cancellation Policy\n\n"
    "You may cancel a booked move at no charge up to 72 hours before the scheduled "
    "start time. Inside 72 hours we retain the deposit, because the crew and truck "
    "have already been committed to your date.\n\n"
    "Certificate of Insurance\n\n"
    "We provide Certificates of Insurance for buildings that require one. There is no "
    "charge for a standard COI.\n"
)
