from copy import deepcopy
from html.parser import HTMLParser
from dataclasses import replace
from hashlib import sha256
import json
import unittest

from themeforge.analysis_types import AnalysisResult, Theme, ThemeQuote, CodebookEntry
from themeforge.discovery_exports import export_discovery_html, export_discovery_markdown


def example():
    unit = dict(id='u1', source_name='Interview A', speaker='P1', source_start=7,
                source_end=42, source_hash='a'*64, text='I need time, but it can still work.',
                context_text='Interviewer: What changes?\nP1: I need time, but it can still work.',
                reference_context='What changes?', flags=['context_required'], eligible=True)
    # Bounds are derived from the exact synthetic text rather than a hand count.
    unit['source_end'] = unit['source_start'] + len(unit['text'])
    other = dict(unit, id='u2', speaker='P2', source_start=100, source_end=125,
                 text='It works without changes.', flags=['negation'])
    other['source_end'] = other['source_start'] + len(other['text'])
    code = dict(id='c1', label='Time and adaptation', definition='An extractive proposal.',
                inclusion_criteria='Practical adaptations', exclusion_criteria='Bare procedure names',
                scope_terms=['time', 'adaptation'], origin='local_extractive', definition_kind='extractive_anchor',
                anchor_unit_id='u1', anchor_text='I need time', anchor_source_start=7, anchor_source_end=18,
                evidence_unit_ids=['u1'], counterexample_unit_ids=['u2'], uncertainty=['Context needs review'], context_relevance=0.42)
    payload = dict(mode='guided', requested_context=dict(research_questions=['How does it fit?'], theoretical_lens='Work practices'),
                   codes=[code], categories=[dict(label='Implementation', rationale='Overlapping practical scope', code_ids=['c1'])],
                   interpretive_themes=[], relations=[], unassigned_unit_ids=['u2'], flagged_unit_ids=['u1', 'u2'],
                   rare_unit_ids=['u1'], units=[unit, other], provenance={'parameter_training':False}, diagnostics={}, notes=[])
    return AnalysisResult(version='test', document_count=1, quote_count=2, themes=[], notes=[],
                          source_hashes={'Interview A':'a'*64}, discovery=payload)


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class DiscoveryExportsTests(unittest.TestCase):
    def test_separates_artifact_levels_and_resolves_full_evidence(self):
        result = example()
        for render in (export_discovery_markdown, export_discovery_html):
            output = render(result)
            for text in ('Candidate codes', 'Candidate categories', 'Interpretive themes',
                         'No interpretive themes generated', 'not confidence', 'not proven logical contradictions',
                         'I need time', 'It works without changes', 'What changes?', 'P1', 'P2', 'Bare procedure names'):
                self.assertIn(text, output)
        markdown = export_discovery_markdown(result)
        self.assertIn('7:18', markdown)
        self.assertIn('a'*64, markdown)

    def test_workspace_rename_definition_memo_and_quote_edits_remain_visible(self):
        result = example()
        result.discovery['workspace_fingerprint'] = sha256(json.dumps([], sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        quote = ThemeQuote('manual-id', 'P3', 'A researcher selected a different passage.', 0, 1,
                           source_name='Interview A', source_start=200, source_end=241, source_hash='a'*64, memo='Keep this distinction')
        edited = Theme('edited', 'Researcher renamed category', '#123456', [], 1, 0, quotes=[quote],
                       memo='Researcher memo', code_definition=CodebookEntry('Edited', 'Researcher revised definition'))
        result = replace(result, themes=[edited])
        for render in (export_discovery_html, export_discovery_markdown):
            output = render(result)
            for text in ('Generated snapshot predates workspace edits', 'Current workspace coding', 'Researcher renamed category',
                         'Researcher revised definition', 'Researcher memo', 'Keep this distinction',
                         'A researcher selected a different passage', 'Time and adaptation'):
                self.assertIn(text, output)

    def test_refuses_missing_evidence_and_stale_anchor(self):
        for field in ('evidence_unit_ids', 'qualification_unit_ids', 'counterexample_unit_ids'):
            result = example()
            result.discovery['codes'][0][field] = ['invented']
            for render in (export_discovery_markdown, export_discovery_html):
                with self.assertRaisesRegex(ValueError, 'Missing discovery evidence'):
                    render(result)
        result = example()
        result.discovery['codes'][0]['anchor_text'] = 'invented quote'
        with self.assertRaisesRegex(ValueError, 'anchor does not resolve'):
            export_discovery_html(result)

    def test_retrieved_assertions_and_qualifications_preserve_source_scope(self):
        result = example()
        code = result.discovery['codes'][0]
        code['counterexample_unit_ids'] = []
        code['qualification_unit_ids'] = ['u2']
        code['matches'] = [dict(unit_id='u1', text='I need time', source_start=7, source_end=18,
                               similarity=1.0, role='anchor')]
        for render in (export_discovery_html, export_discovery_markdown):
            output = render(result)
            self.assertIn('Retrieved assertion', output)
            self.assertIn('Possible qualifications', output)
            self.assertIn('Full participant turn', output)
        code['matches'][0]['text'] = 'Invented assertion'
        with self.assertRaisesRegex(ValueError, 'Retrieved assertion'):
            export_discovery_html(result)
        code['matches'] = []
        code['evidence_unit_ids'].append('u2')
        with self.assertRaisesRegex(ValueError, 'must be separate'):
            export_discovery_html(result)

    def test_refuses_source_version_and_category_reference_mismatch(self):
        result = example()
        result.discovery['units'][0]['source_hash'] = 'stale'
        with self.assertRaisesRegex(ValueError, 'version mismatch'):
            export_discovery_markdown(result)
        result = example()
        result.discovery['categories'][0]['code_ids'] = ['missing-code']
        with self.assertRaisesRegex(ValueError, 'Missing category'):
            export_discovery_html(result)

    def test_html_escapes_untrusted_fields_and_has_no_active_or_remote_content(self):
        result = example()
        attack = '</script><img src="https://evil.invalid/x" onerror="alert(1)">'
        result.discovery['codes'][0]['label'] = attack
        result.discovery['requested_context']['theoretical_lens'] = attack
        result.discovery['units'][0]['context_text'] = attack
        result.discovery['provenance']['unsafe'] = attack
        output = export_discovery_html(result)
        self.assertIn('&lt;/script&gt;', output)
        self.assertNotIn('<img', output)
        parser = Tags()
        parser.feed(output)
        self.assertFalse(any(tag in ('script', 'img', 'iframe', 'form', 'link') for tag, attrs in parser.tags))
        self.assertTrue(any(tag == 'details' for tag, attrs in parser.tags))
        self.assertFalse(any(any(key.startswith('on') for key in attrs) for tag, attrs in parser.tags))

    def test_archive_preserves_unassigned_flagged_and_rare_material_without_mutation(self):
        result = example()
        before = deepcopy(result.discovery)
        output = export_discovery_html(result)
        for text in ('Unassigned passages (1)', 'Flagged passages (2)', 'Rare passages explicitly retained (1)',
                     'Complete source-unit archive (2)', 'Single source/speaker evidence'):
            self.assertIn(text, output)
        self.assertEqual(result.discovery, before)

    def test_empty_output_and_invalid_duplicate_units(self):
        result = example()
        result.discovery.update(codes=[], categories=[])
        self.assertIn('No candidate codes proposed', export_discovery_html(result))
        result.discovery['units'].append(deepcopy(result.discovery['units'][0]))
        with self.assertRaisesRegex(ValueError, 'unique nonempty IDs'):
            export_discovery_markdown(result)


if __name__ == '__main__':
    unittest.main()
