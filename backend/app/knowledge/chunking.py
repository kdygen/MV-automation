"""Deterministic chunking. No language model decides where a passage begins.

Chunk boundaries have to be reproducible: re-indexing an unchanged document must produce
byte-identical chunks, or every re-index re-embeds everything and the content hashes that
make updates cheap stop meaning anything.

The shape of the text drives the split. Headings first, then paragraphs, then — only when
a single paragraph exceeds the cap — sentences. Overlap is added **only** where a split
lands mid-paragraph, because blanket overlap inflates both cost and near-duplicate
results for no gain on text that was already well separated.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

#: Target passage size. Large enough to carry a whole policy clause with its context,
#: small enough that five of them fit comfortably in a chat turn's budget.
TARGET_TOKENS = 220
MAX_TOKENS = 320

#: Overlap applied only when a paragraph had to be split mid-thought.
OVERLAP_TOKENS = 40

#: Roughly four characters per token for English prose. Approximate on purpose: an exact
#: tokenizer would tie chunking to a specific model's vocabulary, and re-chunking on a
#: model change is precisely what the generation machinery exists to avoid.
CHARS_PER_TOKEN = 4

MIN_CHUNK_CHARS = 40

#: A heading is a short line that is not a sentence — numbered, titled, or shouted.
_HEADING_RE = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)*[.)]?\s+)"
    r"|(?:[A-Z][A-Z \-/&]{3,}$)"
    r"|(?:#{1,6}\s+))(?P<text>.{0,200}?)\s*$"
)
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE = re.compile(r"[ \t]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

#: Markup is stripped at ingestion. Not as an injection defence — that is structural, see
#: the prompt rules — but because tags are noise in both the vector and the lexical index.
_TAG_RE = re.compile(r"<[^>]{0,200}>")


@dataclass(frozen=True)
class Chunk:
    """One passage, ready to embed and store."""

    index: int
    content: str
    heading: str | None
    token_count: int

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


def normalize(text: str) -> str:
    """Collapse whitespace, drop control characters and markup, keep paragraph breaks."""
    cleaned = _CONTROL.sub(" ", text or "")
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _WHITESPACE.sub(" ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return "\n".join(line.strip() for line in cleaned.split("\n")).strip()


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _is_heading(line: str) -> str | None:
    if not line.strip() or len(line) > 200:
        return None
    match = _HEADING_RE.match(line)
    if match:
        return match.group("text").strip() or line.strip()
    # A short line with no terminal punctuation, followed by prose, reads as a heading —
    # but only if it reads like a *label*. Internal commas and colons mean it is a
    # sentence fragment ("Also known as: COI, proof of insurance"), and misreading one as
    # a heading silently drops its text, which is the worst possible failure for an index.
    stripped = line.strip()
    if len(stripped) > 80 or any(mark in stripped for mark in (":", ",", ";")):
        return None
    if stripped.endswith((".", "!", "?")):
        return None
    words = stripped.split()
    if 1 <= len(words) <= 10 and stripped[0].isupper():
        return stripped
    return None


@dataclass(frozen=True)
class _Section:
    heading: str | None
    paragraphs: list[str]


def _sections(text: str) -> list[_Section]:
    """Split into heading-led sections, preserving document structure where it exists."""
    sections: list[_Section] = []
    heading: str | None = None
    buffer: list[str] = []

    for block in _PARAGRAPH_SPLIT.split(text):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        candidate = _is_heading(lines[0]) if len(lines) > 1 or len(block) <= 80 else None
        if candidate and len(lines) == 1:
            if buffer or heading:
                sections.append(_Section(heading=heading, paragraphs=buffer))
            heading, buffer = candidate, []
            continue
        if candidate and len(lines) > 1:
            if buffer or heading:
                sections.append(_Section(heading=heading, paragraphs=buffer))
            heading = candidate
            buffer = ["\n".join(lines[1:]).strip()]
            continue
        buffer.append(block)

    if buffer or heading:
        sections.append(_Section(heading=heading, paragraphs=buffer))
    return sections or [_Section(heading=None, paragraphs=[text])]


def _split_oversized(paragraph: str) -> list[str]:
    """Break a paragraph that exceeds the cap, overlapping so no clause is orphaned."""
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(paragraph) if s.strip()]
    if not sentences:
        return [paragraph]

    pieces: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        candidate = " ".join([*current, sentence])
        if current and estimate_tokens(candidate) > MAX_TOKENS:
            pieces.append(" ".join(current))
            # Carry the tail forward so a clause split across the boundary stays findable
            # from either side.
            overlap_chars = OVERLAP_TOKENS * CHARS_PER_TOKEN
            tail = " ".join(current)[-overlap_chars:]
            current = [tail, sentence] if tail else [sentence]
        else:
            current.append(sentence)
    if current:
        pieces.append(" ".join(current))

    # A single sentence longer than the cap has no natural break; hard-split it rather
    # than emit a chunk that would dominate any answer it appears in.
    final: list[str] = []
    limit = MAX_TOKENS * CHARS_PER_TOKEN
    for piece in pieces:
        while len(piece) > limit:
            final.append(piece[:limit])
            piece = piece[limit:]
        if piece:
            final.append(piece)
    return final


def chunk_text(text: str, *, title: str | None = None) -> list[Chunk]:
    """Split normalized text into deterministic, embeddable passages.

    The heading is prepended to each chunk's content so the section survives into both
    the vector and the lexical index — "Cancellations" is often the only word in a
    document that matches how a customer phrases the question.
    """
    normalized = normalize(text)
    if not normalized:
        return []

    chunks: list[Chunk] = []
    carried: list[str] = []
    for section in _sections(normalized):
        if not section.paragraphs:
            # A heading with no body of its own. Carry the text forward rather than drop
            # it: an orphaned heading is still content, and losing it loses a searchable
            # term entirely.
            if section.heading:
                carried.append(section.heading)
            continue
        pending: list[str] = [*carried]
        carried = []
        for paragraph in section.paragraphs:
            for piece in (
                _split_oversized(paragraph)
                if estimate_tokens(paragraph) > MAX_TOKENS
                else [paragraph]
            ):
                candidate = "\n\n".join([*pending, piece])
                if pending and estimate_tokens(candidate) > TARGET_TOKENS:
                    chunks.append(_build(len(chunks), pending, section.heading, title))
                    pending = [piece]
                else:
                    pending.append(piece)
        if pending:
            chunks.append(_build(len(chunks), pending, section.heading, title))

    if carried:
        # Trailing orphan headings still belong in the index.
        if chunks:
            last = chunks[-1]
            chunks[-1] = Chunk(
                index=last.index,
                content=f"{last.content}\n\n" + "\n".join(carried),
                heading=last.heading,
                token_count=estimate_tokens(last.content) + estimate_tokens("\n".join(carried)),
            )
        else:
            chunks.append(_build(0, carried, None, title))

    return [c for c in chunks if len(c.content) >= MIN_CHUNK_CHARS] or (
        [_build(0, [normalized], None, title)] if normalized else []
    )


def _build(index: int, parts: list[str], heading: str | None, title: str | None) -> Chunk:
    body = "\n\n".join(parts).strip()
    # A manual entry's title is usually also its first heading; repeating it wastes
    # tokens and skews the lexical index toward whichever word appears twice.
    labels = [p for p in (title, heading) if p]
    if len(labels) == 2 and labels[0].strip().lower() == labels[1].strip().lower():
        labels = labels[:1]
    prefix = " — ".join(labels)
    content = f"{prefix}\n\n{body}" if prefix else body
    return Chunk(
        index=index,
        content=content,
        heading=heading,
        token_count=estimate_tokens(content),
    )
