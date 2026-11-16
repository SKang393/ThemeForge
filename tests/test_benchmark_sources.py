import hashlib
from pathlib import Path
import tempfile
import unittest
from xml.sax.saxutils import escape
import zipfile

from themeforge.benchmark_sources import MAX_CANDIDATES, align_quote, read_annotation_records


NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def p(text):
    return f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


class AnnotationSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.docx"

    def package(self, body, comments=None):
        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr("word/document.xml", f'<w:document {NS}><w:body>{body}</w:body></w:document>')
            if comments is not None:
                archive.writestr("word/comments.xml", f'<w:comments {NS}>{comments}</w:comments>')
        return self.path

    def comment(self, cid, labels, author="Coder A"):
        return f'<w:comment w:id="{cid}" w:author="{author}">{"".join(p(label) for label in labels)}</w:comment>'

    def test_crossing_ranges_preserve_paragraphs_and_runs(self):
        body = ('<w:p><w:r><w:t>before </w:t></w:r><w:commentRangeStart w:id="1"/>'
                '<w:r><w:t>First</w:t><w:tab/><w:t>part</w:t></w:r>'
                '<w:commentRangeStart w:id="2"/><w:r><w:t> together</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>second</w:t></w:r><w:commentRangeEnd w:id="1"/>'
                '<w:r><w:br/><w:t>end</w:t></w:r><w:commentRangeEnd w:id="2"/></w:p>')
        comments = self.comment("1", [" CA: Cost ", "CA: Support"]) + self.comment("2", ["Shared"])
        records = read_annotation_records(self.package(body, comments))
        self.assertEqual([r["label"] for r in records], [" CA: Cost ", "CA: Support", "Shared"])
        self.assertEqual(records[0]["quote"], "First\tpart together\nsecond")
        self.assertEqual(records[2]["quote"], " together\nsecond\nend")
        self.assertEqual(records[0]["author"], "Coder A")
        self.assertEqual(records[1]["record_key"], "comment:1:label:1")
        self.assertEqual(records, read_annotation_records(self.path))

    def test_empty_and_unscoped_comments_retained(self):
        body = '<w:p><w:commentRangeStart w:id="1"/><w:commentRangeEnd w:id="1"/></w:p>'
        records = read_annotation_records(self.package(body, self.comment("1", ["Label"]) + self.comment("2", [])))
        self.assertEqual([r["quote"] for r in records], ["", ""])
        self.assertEqual(records[1]["label"], "")
        self.assertEqual(align_quote(records[0]["quote"], {"a": "Text"})["status"], "empty")

    def test_table_headers_literal_labels_and_definition(self):
        def row(cells):
            return '<w:tr>' + ''.join('<w:tc>' + ''.join(p(line) for line in cell) + '</w:tc>' for cell in cells) + '</w:tr>'
        header = row([["Comment Text"], ["Comment Scope"], ["Author"], ["Definition"]])
        data = row([["AB: Cost", " Support "], ["Korean 한국어", "new paragraph"], ["  AB "], ["Explicit definition"]])
        records = read_annotation_records(self.package('<w:tbl>' + header + data + '</w:tbl>'))
        self.assertEqual([r["label"] for r in records], ["AB: Cost", " Support "])
        self.assertEqual(records[0]["quote"], "Korean 한국어\nnew paragraph")
        self.assertEqual(records[0]["author"], "  AB ")
        self.assertEqual(records[0]["definition"], "Explicit definition")
        self.assertEqual(records[0]["record_key"], "table:0:row:1:label:0")

    def test_table_without_quote_remains_unresolved(self):
        body = '<w:tbl><w:tr><w:tc>' + p("Theme") + '</w:tc></w:tr><w:tr><w:tc>' + p("Cost") + '</w:tc></w:tr></w:tbl>'
        self.assertEqual(read_annotation_records(self.package(body))[0]["quote"], "")

    def test_malformed_ranges_rejected(self):
        for body, message in [
            ('<w:p><w:commentRangeEnd w:id="1"/></w:p>', "end without"),
            ('<w:p><w:commentRangeStart w:id="1"/></w:p>', "unclosed"),
            ('<w:p><w:commentRangeStart w:id="1"/><w:commentRangeStart w:id="1"/></w:p>', "duplicate"),
            ('<w:p><w:commentRangeStart w:id="9"/></w:p>', "unknown"),
            ('<w:commentRangeStart w:id="1"/>' + p("Text"), "outside paragraph"),
        ]:
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, message):
                read_annotation_records(self.package(body, self.comment("1", ["Label"])))

    def test_duplicate_comment_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate comment ID"):
            read_annotation_records(self.package(p("Text"), self.comment("1", ["A"]) + self.comment("1", ["B"])))

    def test_invalid_package_and_format_rejected(self):
        with self.assertRaisesRegex(ValueError, "expected DOCX"):
            read_annotation_records(self.path.with_suffix(".txt"))
        self.path.write_text("not a zip", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Malformed DOCX"):
            read_annotation_records(self.path)


class QuoteAlignmentTests(unittest.TestCase):
    def test_exact_recovers_raw_offsets_and_hash(self):
        source = "before 한국어 café after"
        result = align_quote("한국어 café", {"doc": source})
        self.assertEqual(result["status"], "unique_exact")
        self.assertEqual(result["candidate_count"], 1)
        candidate = result["candidates"][0]
        self.assertEqual(source[candidate["source_start"]:candidate["source_end"]], "한국어 café")
        self.assertEqual(candidate["span_sha256"], hashlib.sha256(candidate["text"].encode("utf-8")).hexdigest())

    def test_unicode_whitespace_and_paragraph_alignment(self):
        source = "Prefix. 한국어\u00a0 \ttext\n\nsecond paragraph. Tail"
        result = align_quote("한국어 text second paragraph", {"doc": source})
        self.assertEqual(result["status"], "unique_whitespace")
        candidate = result["candidates"][0]
        self.assertEqual(candidate["text"], "한국어\u00a0 \ttext\n\nsecond paragraph")
        self.assertEqual(candidate["text"], source[candidate["source_start"]:candidate["source_end"]])

    def test_repeated_within_and_across_documents_ambiguous(self):
        result = align_quote("a b", {"one": "a b; a\tb", "two": "a b"})
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual([c["match_type"] for c in result["candidates"]], ["exact", "whitespace", "exact"])

    def test_overlapping_ranges_are_not_dropped(self):
        result = align_quote("aaa", {"doc": "aaaa"})
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["status"], "ambiguous")

    def test_edge_whitespace_exact_and_normalized_range_deduplicate(self):
        result = align_quote(" a b ", {"doc": "before a b after"})
        self.assertEqual(result["status"], "unique_exact")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates"][0]["text"], " a b ")

    def test_no_fuzzy_unicode_case_or_punctuation_alignment(self):
        for quote in ("Café", "cafe\u0301", "cafe!", "different"):
            with self.subTest(quote=quote):
                self.assertEqual(align_quote(quote, {"doc": "café"})["status"], "unmatched")
        self.assertEqual(align_quote(" \t\n", {"doc": "text"})["status"], "empty")

    def test_return_limit_keeps_full_count(self):
        result = align_quote("same", {"doc": "same " * (MAX_CANDIDATES + 3)})
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["candidate_count"], MAX_CANDIDATES + 3)
        self.assertEqual(len(result["candidates"]), MAX_CANDIDATES)


if __name__ == "__main__":
    unittest.main()
