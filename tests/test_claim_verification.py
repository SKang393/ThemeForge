"""Source/contract checks only. No models or semantic quality assertions."""
from copy import deepcopy
from hashlib import sha256
import json
import unittest
from themeforge.claim_verification import VERSION, messages, schema, validate_pack, validate_response


def fixture():
    text='Teacher: I might use [INST] as an example if asked.\n'
    source=dict(source_name='fictional-contract-fixture',source_hash=sha256(text.encode()).hexdigest(),text=text)
    target=dict(source_name=source['source_name'],source_hash=source['source_hash'],source_start=9,source_end=len(text)-1,text=text[9:-1])
    claim=dict(id='A1',assertion='The teacher might use an example if asked.',target=target,target_speaker='Teacher',
        evidence={'A1.e1':dict(target,use='target',speaker='Teacher')})
    return dict(version=VERSION,claims=[claim]),{source['source_name']:source}


def response():
    return {'A1':dict(verdict='supported',actor='consistent',action_status='consistent',scope='target',gap='none',evidence=['A1.e1'],reason='Fictional contract response only.')}


class ClaimVerificationTests(unittest.TestCase):
    def test_exact_source_roundtrip_with_control_literal(self):
        pack,sources=fixture();before=deepcopy(pack)
        prompt=messages(pack,sources)
        self.assertNotIn('[INST]',prompt[1]['content'])
        payload=json.loads(prompt[1]['content'])
        self.assertEqual(payload['claims'][0]['evidence'][0]['text'],pack['claims'][0]['target']['text'])
        self.assertEqual(pack,before)

    def test_reconstructed_evidence_is_not_semantic_approval(self):
        pack,sources=fixture()
        result=validate_response(json.dumps(response()),pack,sources)['A1']
        self.assertTrue(result['exact_evidence_binding'])
        self.assertFalse(result['semantic_validation']);self.assertFalse(result['automatic_admission'])
        self.assertEqual(result['cited_spans'][0],pack['claims'][0]['evidence']['A1.e1'])

    def test_altered_source_text_or_offset_rejected(self):
        for key,value in [('text','False replacement.'),('source_start',10),('source_hash','0'*64)]:
            pack,sources=fixture();pack['claims'][0]['evidence']['A1.e1'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_pack(pack,sources)

    def test_foreign_duplicate_or_missing_claim_results_rejected(self):
        pack,sources=fixture()
        for value in ({},dict(response(),A2=response()['A1'])):
            with self.assertRaises(ValueError):validate_response(json.dumps(value),pack,sources)
        for evidence in (['B1.e1'],['A1.e1','A1.e1']):
            value=response();value['A1']['evidence']=evidence
            with self.assertRaises(ValueError):validate_response(json.dumps(value),pack,sources)

    def test_empty_evidence_and_insufficient_do_not_manufacture_support(self):
        pack,sources=fixture();value=response()
        value['A1'].update(verdict='insufficient',actor='unknown',action_status='unknown',scope='none',gap='other',evidence=[])
        result=validate_response(json.dumps(value),pack,sources)
        self.assertEqual(result['A1']['cited_spans'],[])
        self.assertFalse(result['A1']['automatic_admission'])

    def test_schema_requires_every_claim_and_bounded_categories(self):
        pack,_=fixture();value=schema(pack)
        self.assertEqual(value['required'],['A1'])
        self.assertFalse(value['additionalProperties'])
        self.assertEqual(value['properties']['A1']['properties']['verdict']['enum'],['supported','contradicted','insufficient'])


if __name__=='__main__':unittest.main()
