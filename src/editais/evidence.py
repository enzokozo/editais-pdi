"""Literal evidence check: the barrier against invented values (decisions D11, D38).

An evidence quote is accepted only if it (1) occurs literally in the document,
(2) contains the extracted value and (3) is short enough to point at it.
Comparison ignores line breaks and repeated spaces, and fixes misread PDF glyphs.
"""
import bisect
import re
from dataclasses import dataclass

from editais import structure
from editais.textnorm import fix_glyphs

MAX_EVIDENCE_CHARS = 400


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", fix_glyphs(text)).strip()


@dataclass
class LocatedText:
    """Document lines joined into one string, keeping where each line starts."""
    text: str
    starts: list[int]
    pages: list[int]
    annex_start: int | None   # index of the first annex line (from structure.py), if any


def index_lines(lines: list[tuple[int, str]], annex_start: int | None = None) -> LocatedText:
    """lines: [(page, line)] as returned by textnorm.clean_lines."""
    parts, starts, pages, pos = [], [], [], 0
    for page, line in lines:
        line = normalize(line)
        starts.append(pos)
        pages.append(page)
        parts.append(line)
        pos += len(line) + 1
    return LocatedText(" ".join(parts), starts, pages, annex_start)


def index_document(doc: dict) -> LocatedText:
    """Index an extracted PDF (data/extracted/*.json) with the annex boundary from structure.py."""
    lines, _, _, _, (_, first_annex) = structure.parse(doc)
    pairs = [(line["page"], line["t"]) for line in lines]
    return index_lines(pairs, annex_start=first_annex if first_annex < len(lines) else None)


@dataclass
class EvidenceCheck:
    ok: bool
    reason: str
    page: int | None = None
    region: str | None = None


def check_evidence(quote: str, value: str | None, doc: LocatedText,
                   max_chars: int = MAX_EVIDENCE_CHARS) -> EvidenceCheck:
    q = normalize(quote)
    if not q:
        return EvidenceCheck(False, "empty evidence")
    if len(q) > max_chars:
        return EvidenceCheck(False, f"evidence longer than {max_chars} chars")
    if value is not None and normalize(value) not in q:
        return EvidenceCheck(False, "value not contained in evidence")
    pos = doc.text.find(q)
    if pos < 0:
        return EvidenceCheck(False, "evidence not found literally in the document")
    line = bisect.bisect_right(doc.starts, pos) - 1
    in_annex = doc.annex_start is not None and line >= doc.annex_start
    return EvidenceCheck(True, "ok", doc.pages[line], "anexo" if in_annex else "corpo")
