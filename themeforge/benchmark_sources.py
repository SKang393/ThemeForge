"""Local annotation evidence extraction and conservative quote alignment.

Authors and literal labels are private evidence, not export-safe identifiers.
Offsets are Python character offsets in the original supplied document string.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_CANDIDATES = 100


def _inventory_text(node: ET.Element) -> str:
    # Deliberately identical to prepare_coding_benchmark.annotation_label_candidates.
    return "\n".join("".join(p.itertext()) for p in node.iter(W + "p"))


def _labels(node: ET.Element) -> list[str]:
    return [line for line in _inventory_text(node).splitlines() if line.strip()]


def _text(node: ET.Element) -> str:
    paragraphs = []
    for paragraph in node.iter(W + "p"):
        if any(child is not paragraph for child in paragraph.iter(W + "p")):
            raise ValueError("Unsupported DOCX: nested paragraphs")
        parts = []
        for child in paragraph.iter():
            if child.tag == W + "t":
                parts.append(child.text or "")
            elif child.tag == W + "tab":
                parts.append("\t")
            elif child.tag in (W + "br", W + "cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _comment_scopes(document: ET.Element, ids: set[str]) -> dict[str, str]:
    active: set[str] = set()
    started: set[str] = set()
    ended: set[str] = set()
    pieces: dict[str, list[str]] = {cid: [] for cid in ids}
    visited_markers = 0
    for index, paragraph in enumerate(document.iter(W + "p")):
        if any(child is not paragraph for child in paragraph.iter(W + "p")):
            raise ValueError("Unsupported DOCX: nested paragraphs")
        if index:
            for cid in active:
                pieces[cid].append("\n")
        for node in paragraph.iter():
            if node.tag in (W + "commentRangeStart", W + "commentRangeEnd"):
                visited_markers += 1
                cid = node.get(W + "id")
                if cid not in ids:
                    raise ValueError("Malformed DOCX: range references unknown comment")
                if node.tag == W + "commentRangeStart":
                    if cid in started:
                        raise ValueError("Unsupported DOCX: duplicate comment range start")
                    started.add(cid)
                    active.add(cid)
                else:
                    if cid not in active or cid in ended:
                        raise ValueError("Malformed DOCX: comment range end without active start")
                    active.remove(cid)
                    ended.add(cid)
            else:
                piece = ""
                if node.tag == W + "t":
                    piece = node.text or ""
                elif node.tag == W + "tab":
                    piece = "\t"
                elif node.tag in (W + "br", W + "cr"):
                    piece = "\n"
                for cid in active:
                    pieces[cid].append(piece)
    marker_count = sum(node.tag in (W + "commentRangeStart", W + "commentRangeEnd")
                       for node in document.iter())
    if visited_markers != marker_count:
        raise ValueError("Unsupported DOCX: comment marker outside paragraph")
    if active:
        raise ValueError("Malformed DOCX: unclosed comment range")
    return {cid: "".join(parts) for cid, parts in pieces.items()}


def read_annotation_records(path: Path) -> list[dict]:
    """Read DOCX comments and labeled tables without interpreting coder labels.

    Missing comment scopes and table quote columns yield empty quotes. Empty-label
    comments are retained with label ''. Unsupported formats fail explicitly.
    record_key is local to the source file and stable for unchanged source XML.
    """
    if path.suffix.lower() != ".docx":
        raise ValueError("Unsupported annotation format: expected DOCX")
    try:
        with zipfile.ZipFile(path) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
            comments = (ET.fromstring(archive.read("word/comments.xml"))
                        if "word/comments.xml" in archive.namelist() else None)
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError("Malformed DOCX annotation package") from exc
    comment_nodes = [] if comments is None else list(comments.findall(W + "comment"))
    ids = [node.get(W + "id") for node in comment_nodes]
    if None in ids or len(ids) != len(set(ids)):
        raise ValueError("Malformed DOCX: missing or duplicate comment ID")
    scopes = _comment_scopes(document, set(ids))
    records = []
    for node in comment_nodes:
        cid = node.get(W + "id")
        for index, label in enumerate(_labels(node) or [""]):
            records.append(dict(record_key=f"comment:{cid}:label:{index}",
                                kind="docx_comment", label=label,
                                quote=scopes[cid], author=node.get(W + "author", "")))
    for table_index, table in enumerate(document.iter(W + "tbl")):
        rows = table.findall(W + "tr")
        if not rows:
            continue
        header = [_inventory_text(cell).strip().casefold() for cell in rows[0].findall(W + "tc")]
        label_column = next((header.index(name) for name in ("theme", "comment text", "code") if name in header), None)
        if label_column is None:
            continue
        if len(header) != len(set(header)):
            raise ValueError("Unsupported DOCX: duplicate annotation table headers")
        def column(*names: str) -> int | None:
            return next((header.index(name) for name in names if name in header), None)
        quote_column = column("text", "comment scope", "quote")
        author_column = column("author")
        definition_column = column("definition", "description")
        for row_index, row in enumerate(rows[1:], 1):
            cells = row.findall(W + "tc")
            def value(index: int | None) -> str:
                return _text(cells[index]) if index is not None and index < len(cells) else ""
            if label_column >= len(cells):
                continue
            for index, label in enumerate(_labels(cells[label_column])):
                record = dict(record_key=f"table:{table_index}:row:{row_index}:label:{index}",
                              kind="docx_table", label=label, quote=value(quote_column),
                              author=value(author_column))
                if definition_column is not None:
                    record["definition"] = value(definition_column)
                records.append(record)
    return records


def _normalized(text: str) -> tuple[str, list[tuple[int, int]]]:
    parts = []
    offsets = []
    for match in re.finditer(r"\s+|\S", text):
        parts.append(" " if match.group().isspace() else match.group())
        offsets.append(match.span())
    return "".join(parts), offsets


def _align_quote(quote: str, documents: dict[str, str], prepared: dict) -> dict:
    """Find all exact/whitespace-only matches; never choose repeated evidence.

    Whitespace normalization collapses each Unicode whitespace run and ignores
    quote-edge whitespace. Case, punctuation and Unicode composition are literal.
    At most MAX_CANDIDATES are returned, but candidate_count includes all ranges.
    """
    if not quote.strip():
        return dict(status="empty", candidates=[], candidate_count=0)
    needle, _ = _normalized(quote.strip())
    candidates = []
    total = 0
    for document_id, source in sorted(documents.items()):
        normalized, offsets = prepared[document_id]
        ranges: dict[tuple[int, int], str] = {}
        for text, wanted, kind in ((source, quote, "exact"), (normalized, needle, "whitespace")):
            cursor = 0
            while (found := text.find(wanted, cursor)) != -1:
                end = found + len(wanted)
                span = (found, end) if kind == "exact" else (offsets[found][0], offsets[end - 1][1])
                # Edge whitespace variants describing the same selected content
                # are one match; preserve the literal exact range when available.
                if kind == "whitespace" and quote != quote.strip():
                    if any(source[a:b].strip() == source[span[0]:span[1]] and a <= span[0] and b >= span[1]
                           for a, b in ranges):
                        cursor = found + 1
                        continue
                ranges.setdefault(span, kind)
                cursor = found + 1
        for (start, end), kind in sorted(ranges.items()):
            total += 1
            if len(candidates) < MAX_CANDIDATES:
                raw = source[start:end]
                candidates.append(dict(document_id=document_id, source_start=start, source_end=end,
                                       span_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                                       text=raw, match_type=kind))
    status = ("unmatched" if not total else "ambiguous" if total > 1 else
              "unique_exact" if candidates[0]["match_type"] == "exact" else "unique_whitespace")
    return dict(status=status, candidates=candidates, candidate_count=total)


class QuoteAligner:
    """Reusable immutable snapshot of document text and normalized offsets."""

    def __init__(self, documents: dict[str, str]):
        self._documents = dict(documents)
        self._prepared = {key: _normalized(text) for key, text in self._documents.items()}

    def align(self, quote: str) -> dict:
        return _align_quote(quote, self._documents, self._prepared)


def align_quote(quote: str, documents: dict[str, str]) -> dict:
    """Align one quote; use QuoteAligner to reuse normalization across quotes."""
    return QuoteAligner(documents).align(quote)
