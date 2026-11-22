from __future__ import annotations

import codecs
import logging
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree


HEX_ESCAPE_RE = re.compile(r"\\'([0-9a-fA-F]{2})")
CONTROL_WORD_RE = re.compile(r"\\[a-zA-Z]+-?\d* ?")
DESTINATION_RE = re.compile(r"{\\\*[^{}]*(?:{[^{}]*}[^{}]*)*}")
RTF_GROUPS_TO_DROP = {"fonttbl", "colortbl", "stylesheet", "info"}
PDF_DEPENDENCY_MESSAGE = "PDF input requires pypdf. Install the package dependency or use DOCX, RTF, or TXT."
PDF_EXTRACTION_MESSAGE = "PDF text could not be extracted. Export the transcript to DOCX, RTF, or TXT before analysis."


def load_transcript_text(path: str | Path) -> str:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".docx":
        return _load_docx(source)
    if suffix == ".rtf":
        return _load_rtf(source)
    if suffix == ".pdf":
        return _load_pdf(source)
    return _load_plain_text(source)


def _load_plain_text(path: Path) -> str:
    if _has_pdf_header(path):
        return _load_pdf(path)
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        with path.open("rb") as source:
            header = source.read(4)
        if header.startswith(codecs.BOM_UTF8):
            raise ValueError(f"{path.name} contains invalid UTF-8 text.") from exc

    # The UTF-32 little-endian mark starts with the UTF-16 mark.
    if header.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif header.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        return path.read_text(encoding="cp1252", errors="replace")
    try:
        return path.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path.name} contains invalid {encoding} text. "
                         "Re-export the transcript as UTF-8 and try again.") from exc


def _has_pdf_header(path: Path) -> bool:
    with path.open("rb") as source:
        return source.read(5) == b"%PDF"


def _load_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:
        raise ValueError(PDF_DEPENDENCY_MESSAGE) from exc

    previous_logging_threshold = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        try:
            reader = PdfReader(str(path))
        except PdfReadError as exc:
            raise ValueError(PDF_EXTRACTION_MESSAGE) from exc
    finally:
        logging.disable(previous_logging_threshold)
    pages = [_normalize_spaces(page.extract_text() or "") for page in reader.pages]
    text = "\n".join(page for page in pages if page)
    if not text.strip():
        raise ValueError(PDF_EXTRACTION_MESSAGE)
    return text


def _load_docx(path: Path) -> str:
    paragraphs: list[str] = []
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

    with zipfile.ZipFile(path) as archive:
        with archive.open("word/document.xml") as document_file:
            root = ElementTree.parse(document_file).getroot()

    for paragraph in root.findall(".//w:p", namespace):
        pieces: list[str] = []
        for node in paragraph.iter():
            tag = _strip_namespace(node.tag)
            if tag == "t" and node.text:
                pieces.append(node.text)
            elif tag == "tab":
                pieces.append("\t")
            elif tag in {"br", "cr"}:
                pieces.append("\n")
        text = _normalize_spaces("".join(pieces))
        if text:
            paragraphs.append(text)

    return "\n".join(paragraphs)


def _load_rtf(path: Path) -> str:
    raw = path.read_text(encoding="cp1252", errors="replace")
    return _strip_rtf(raw)


def _strip_rtf(raw: str) -> str:
    raw = _remove_rtf_groups(raw, RTF_GROUPS_TO_DROP)
    text = raw.replace("\\par", "\n").replace("\\line", "\n")
    text = HEX_ESCAPE_RE.sub(_decode_hex_escape, text)
    text = text.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")
    text = DESTINATION_RE.sub(" ", text)
    text = CONTROL_WORD_RE.sub("", text)
    text = text.replace("{", " ").replace("}", " ")
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    lines = [_normalize_spaces(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _remove_rtf_groups(raw: str, destinations: set[str]) -> str:
    output: list[str] = []
    index = 0
    while index < len(raw):
        if raw[index] == "{" and raw.startswith("{\\", index):
            match = re.match(r"{\\([a-zA-Z]+)", raw[index:])
            if match and match.group(1).lower() in destinations:
                output.append(" ")
                index = _skip_balanced_group(raw, index)
                continue
        output.append(raw[index])
        index += 1
    return "".join(output)


def _skip_balanced_group(raw: str, start: int) -> int:
    depth = 0
    index = start
    while index < len(raw):
        char = raw[index]
        escaped = index > 0 and raw[index - 1] == "\\"
        if char == "{" and not escaped:
            depth += 1
        elif char == "}" and not escaped:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(raw)


def _decode_hex_escape(match: re.Match[str]) -> str:
    return bytes([int(match.group(1), 16)]).decode("cp1252", errors="replace")


def _normalize_spaces(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
