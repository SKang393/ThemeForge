"""Read literal code definitions from local Excel workbooks without formulas."""
from __future__ import annotations

from pathlib import Path
import posixpath
import re
import zipfile
from xml.etree import ElementTree


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"m": MAIN_NS}
LABEL_HEADERS = {"potential code", "code", "theme"}
DEFINITION_HEADERS = {"definition", "description"}


def _text(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        inline = cell.find("m:is", NS)
        return "" if inline is None else "".join(node.text or "" for node in inline.iter(f"{{{MAIN_NS}}}t"))
    value = cell.find("m:v", NS)
    if value is None or value.text is None:
        return ""
    if kind == "s":
        try:
            return shared_strings[int(value.text)]
        except (ValueError, IndexError) as exc:
            raise ValueError(f"invalid shared string index in cell {cell.get('r', '?')}") from exc
    return value.text


def _column_number(reference: str) -> int:
    letters = re.match(r"[A-Za-z]+", reference)
    if not letters:
        raise ValueError(f"invalid Excel cell reference: {reference!r}")
    number = 0
    for char in letters.group().upper():
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def read_workbook_definitions(path: Path) -> list[dict]:
    """Return literal code/definition rows from XLSX sheets.

    Supported header names are Potential Code, Code, or Theme and Definition
    or Description. Formula cells in either selected column are rejected with
    their sheet and cell location; formulas are never evaluated.
    """
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            rel.get("Id"): rel.get("Target", "")
            for rel in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")
        }
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t"))
                for item in root.findall("m:si", NS)
            ]

        result = []
        for sheet in workbook.findall("m:sheets/m:sheet", NS):
            sheet_name = sheet.get("name", "")
            target = targets.get(sheet.get(f"{{{REL_NS}}}id"), "")
            if not target:
                raise ValueError(f"missing worksheet relationship for sheet {sheet_name!r}")
            worksheet_path = posixpath.normpath(posixpath.join("xl", target))
            worksheet = ElementTree.fromstring(archive.read(worksheet_path))
            rows = worksheet.findall("m:sheetData/m:row", NS)
            header_row_index = None
            label_column = definition_column = None
            for row_index, row in enumerate(rows):
                cells = { _column_number(cell.get("r", "")): cell for cell in row.findall("m:c", NS) }
                normalized = {column: _text(cell, shared_strings).strip().casefold() for column, cell in cells.items()}
                labels = [column for column, value in normalized.items() if value in LABEL_HEADERS]
                definitions = [column for column, value in normalized.items() if value in DEFINITION_HEADERS]
                if labels and definitions:
                    header_row_index = row_index
                    label_column, definition_column = min(labels), min(definitions)
                    break
            if header_row_index is None:
                continue

            for row in rows[header_row_index + 1:]:
                cells = { _column_number(cell.get("r", "")): cell for cell in row.findall("m:c", NS) }
                label_cell = cells.get(label_column)
                definition_cell = cells.get(definition_column)
                for cell in (label_cell, definition_cell):
                    if cell is not None and cell.find("m:f", NS) is not None:
                        raise ValueError(f"unsupported formula in {sheet_name}!{cell.get('r', '?')}")
                label = _text(label_cell, shared_strings) if label_cell is not None else ""
                if not label.strip():
                    continue
                definition = _text(definition_cell, shared_strings) if definition_cell is not None else ""
                result.append({
                    "sheet": sheet_name,
                    "row": int(row.get("r", "0")),
                    "label": label,
                    "definition": definition,
                })
        return result
