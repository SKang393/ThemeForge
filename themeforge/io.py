from __future__ import annotations

from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree


HEX_ESCAPE_RE = re.compile(r"\\'([0-9a-fA-F]{2})")
CONTROL_WORD_RE = re.compile(r"\\[a-zA-Z]+-?\d* ?")
DESTINATION_RE = re.compile(r"{\\\*[^{}]*(?:{[^{}]*}[^{}]*)*}")
RTF_GROUPS_TO_DROP = {"fonttbl", "colortbl", "stylesheet", "info"}


def load_transcript_text(path: str | Path) -> str:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".docx":
        return _load_docx(source)
    if suffix == ".rtf":
        return _load_rtf(source)
    return _load_plain_text(source)


def _load_plain_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp1252", errors="replace")


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
            elif tag == "br":
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
