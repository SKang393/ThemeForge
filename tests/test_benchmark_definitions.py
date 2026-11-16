import tempfile
import unittest
import zipfile
from pathlib import Path

from themeforge.benchmark_definitions import read_workbook_definitions


def _workbook(path: Path, sheet_xml: str, *, shared_strings: str | None = None) -> None:
    workbook = '''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets><sheet name="Codes" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    rels = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="worksheet"/>
      </Relationships>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        if shared_strings is not None:
            archive.writestr("xl/sharedStrings.xml", shared_strings)


class WorkbookDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.xlsx"

    def test_reads_shared_and_inline_strings_and_sparse_actual_rows(self):
        shared = '''<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
          <si><t>Potential Code</t></si><si><t>Definition</t></si>
          <si><t>Literal code</t></si><si><t>Keep  spacing</t></si></sst>'''
        sheet = '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
          <row r="2"><c r="A2" t="s"><v>0</v></c><c r="D2" t="s"><v>1</v></c></row>
          <row r="7"><c r="A7" t="s"><v>2</v></c><c r="D7" t="s"><v>3</v></c></row>
          <row r="12"><c r="A12" t="inlineStr"><is><t>Unspecified definition</t></is></c></row>
        </sheetData></worksheet>'''
        _workbook(self.path, sheet, shared_strings=shared)
        self.assertEqual(read_workbook_definitions(self.path), [
            {"sheet": "Codes", "row": 7, "label": "Literal code", "definition": "Keep  spacing"},
            {"sheet": "Codes", "row": 12, "label": "Unspecified definition", "definition": ""},
        ])

    def test_accepts_code_and_description_header_aliases(self):
        sheet = '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
          <row r="1"><c r="B1" t="inlineStr"><is><t>Code</t></is></c><c r="E1" t="inlineStr"><is><t>Description</t></is></c></row>
          <row r="3"><c r="B3" t="inlineStr"><is><t>Another code</t></is></c><c r="E3" t="inlineStr"><is><t>Meaning</t></is></c></row>
        </sheetData></worksheet>'''
        _workbook(self.path, sheet)
        self.assertEqual(read_workbook_definitions(self.path), [
            {"sheet": "Codes", "row": 3, "label": "Another code", "definition": "Meaning"},
        ])

    def test_formula_cells_are_reported_and_never_evaluated(self):
        sheet = '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
          <row r="1"><c r="A1" t="inlineStr"><is><t>Theme</t></is></c><c r="B1" t="inlineStr"><is><t>Definition</t></is></c></row>
          <row r="4"><c r="A4" t="inlineStr"><is><t>Code name</t></is></c><c r="B4"><f>1+1</f><v>2</v></c></row>
        </sheetData></worksheet>'''
        _workbook(self.path, sheet)
        with self.assertRaisesRegex(ValueError, r"unsupported formula in Codes!B4"):
            read_workbook_definitions(self.path)

    def test_sheets_without_supported_header_pair_are_ignored(self):
        sheet = '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
          <row r="1"><c r="A1" t="inlineStr"><is><t>Code</t></is></c><c r="B1" t="inlineStr"><is><t>Notes</t></is></c></row>
          <row r="2"><c r="A2" t="inlineStr"><is><t>Unmapped</t></is></c></row>
        </sheetData></worksheet>'''
        _workbook(self.path, sheet)
        self.assertEqual(read_workbook_definitions(self.path), [])


if __name__ == "__main__":
    unittest.main()
