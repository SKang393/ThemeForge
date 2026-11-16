from hashlib import sha256
import unittest
from themeforge.source_context import review_context


class SourceContextTests(unittest.TestCase):
    def source(self,text):return dict(source_name='fictional',text=text,source_hash=sha256(text.encode()).hexdigest())

    def unit(self,source,start,end):return dict(id='u',source_name=source['source_name'],source_hash=source['source_hash'],speaker='P1',source_start=start,source_end=end,text=source['text'][start:end])

    def test_composite_source_warns_without_reassigning_speaker(self):
        source=self.source('Prior question.\nThanks.\nParticipant 3 Responses, Fiction\n1. Why?\nNo.\n2. When?\nOnly if asked.\nNext.')
        unit=self.unit(source,16,len(source['text'])-5)
        result=review_context(unit,source,radius=5)
        self.assertTrue(result['attribution_review_required'])
        self.assertEqual(unit['speaker'],'P1')
        for item in result['embedded_section_headers']+[result['preceding_context'],result['following_context']]:
            self.assertEqual(source['text'][item['source_start']:item['source_end']],item['text'])
        self.assertFalse(result['semantic_validation'])

    def test_ordinary_participant_mention_is_not_a_section_boundary(self):
        source=self.source('Participant 3 said no, but I might agree if asked.')
        result=review_context(self.unit(source,0,len(source['text'])),source)
        self.assertFalse(result['attribution_review_required'])
        self.assertIsNone(result['preceding_context']);self.assertIsNone(result['following_context'])

    def test_source_mismatch_and_unbounded_preview_rejected(self):
        source=self.source('No, only if asked.');unit=self.unit(source,0,len(source['text']))
        unit['text']='Yes, always.'
        with self.assertRaises(ValueError):review_context(unit,source)
        with self.assertRaises(ValueError):review_context(self.unit(source,0,len(source['text'])),source,radius=10001)
