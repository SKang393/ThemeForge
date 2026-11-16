from copy import deepcopy
import json
import unittest

from themeforge.codebook_contracts import (
    MAX_RESPONSE_BYTES, MAX_SYNTHESIS_REASON_CHARS, VERSION, decision_schema, scope_schema, synthesis_schema,
    validate_decision, validate_scope, validate_synthesis,
    grounded_scope_schema, validate_grounded_scope,
)


def definition():
    return dict(decision='define', definition='Repeated copies protect against loss.',
                inclusion='Statements linking backups to recoverability.',
                exclusion='Storage mentions without a protective rationale.',
                qualification_ids=[], counterexample_ids=[], uncertainty='Membership requires source review.')


def category():
    return dict(decision='group', label='Protecting recoverability',
                shared_meaning='Different storage practices aim to reduce the risk of losing work.',
                code_ids=['C1', 'C2'], qualification_code_ids=[], counterexample_code_ids=[],
                uncertainty='Related purpose is provisional; differences remain important.')


class CodebookContractsTests(unittest.TestCase):
    def test_each_schema_has_two_complete_closed_discriminated_objects(self):
        for schema, decisions in ((decision_schema(['E1']), ['code', 'abstain']),
                                  (scope_schema(['E1'], ['E1']), ['define', 'insufficient']),
                                  (synthesis_schema(['C1', 'C2']), ['group', 'no_group'])):
            self.assertEqual(list(schema), ['oneOf'])
            self.assertEqual(len(schema['oneOf']), 2)
            for branch, expected in zip(schema['oneOf'], decisions):
                self.assertEqual(branch['type'], 'object')
                self.assertFalse(branch['additionalProperties'])
                self.assertEqual(set(branch['required']), set(branch['properties']))
                self.assertEqual(branch['properties']['decision']['const'], expected)
                self.assertFalse({'id','unit_id','quotes','source_start','source_end'} & set(branch['properties']))
            self.assertNotIn('$ref', json.dumps(schema))
        self.assertTrue(VERSION.startswith('themeforge.'))

    def test_mutually_exclusive_decision_and_abstention(self):
        code = dict(decision='code', label='Backup practices', claim='Copies reduce loss.', evidence_ids=['E1'])
        self.assertEqual(validate_decision(json.dumps(code), ['E1']), code)
        self.assertEqual(validate_decision({'decision':'abstain','reason':'Only a clarification question.'}, []),
                         {'decision':'abstain','reason':'Only a clarification question.'})
        for value in (dict(code, reason='Also abstain'), dict(code, decision='abstain'),
                      {'decision':'code','label':'A','claim':'B','evidence_ids':[]},
                      {'decision':True,'reason':'Wrong discriminant'}):
            with self.assertRaises(ValueError):
                validate_decision(value, ['E1'])

    def test_duplicate_json_keys_nested_objects_extra_fields_and_nonstandard_json_rejected(self):
        invalid = ['{"decision":"code","decision":"abstain","reason":"A"}',
                   '{"decision":"abstain","reason":{"x":1,"x":2}}',
                   '{"decision":"abstain","reason":NaN}',
                   '{"decision":"abstain","reason":Infinity}', '[]', 'null', '```json\n{}\n```']
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_decision(raw, ['E1'])
        for field in ('unit_id','quotes','source_start','version'):
            with self.assertRaises(ValueError):
                validate_decision(dict(decision='abstain',reason='A',**{field:'extra'}), ['E1'])

    def test_unknown_duplicate_and_incorrectly_typed_ids_rejected(self):
        base=dict(decision='code',label='A',claim='A claim.',evidence_ids=['E1'])
        for ids in (['missing'], ['E1','E1'], [True], 'E1', ('E1',), [1]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                validate_decision(dict(base,evidence_ids=ids), ['E1'])
        for ids in (['E1','E1'], [True], [''], ['x'*65], 'E1'):
            with self.assertRaises(ValueError):
                decision_schema(ids)
        # A trusted registry mapping can supply the host-owned ID keys directly.
        self.assertEqual(validate_decision(base, {'E1': {'source_record':'host-owned'}}), base)

    def test_scope_support_is_host_owned_and_all_roles_disjoint(self):
        raw=definition();raw.update(qualification_ids=['E2'],counterexample_ids=['E3'])
        self.assertEqual(validate_scope(raw, ['E1','E2','E3'], ['E1']), raw)
        schema=scope_schema(['E1','E2','E3'], ['E1'])['oneOf'][0]
        self.assertEqual(schema['properties']['qualification_ids']['items']['enum'], ['E2','E3'])
        self.assertNotIn('support_ids', schema['properties'])
        for quals,counters in ((['E1'],[]),([],['E1']),(['E2'],['E2']),(['E2','E2'],[]),(['unknown'],[])):
            with self.assertRaises(ValueError):
                validate_scope(dict(raw,qualification_ids=quals,counterexample_ids=counters), ['E1','E2','E3'], ['E1'])
        with self.assertRaises(ValueError):
            scope_schema(['E1'], ['unknown'])
        with self.assertRaises(ValueError):
            validate_scope(dict(raw,support_ids=['E2']), ['E1','E2','E3'], ['E1'])

    def test_scope_placeholders_rejected_without_claiming_semantic_quality(self):
        for field in ('definition','inclusion','exclusion','uncertainty'):
            for value in ('True',' false ','YES','No','None','n-a','N/A'):
                with self.subTest(field=field,value=value), self.assertRaisesRegex(ValueError,'placeholder'):
                    validate_scope(dict(definition(),**{field:value}), ['E1'], ['E1'])
        # A substantive negative boundary is preserved; no word/meaning classifier is implied.
        raw=definition();raw['exclusion']='No assignment for a bare storage reference.'
        self.assertEqual(validate_scope(raw,['E1'],['E1'])['exclusion'],raw['exclusion'])

    def test_empty_optional_role_arrays_and_negative_branches_preserved(self):
        raw=definition();before=deepcopy(raw)
        validated=validate_scope(raw,['E1'],['E1'])
        self.assertEqual(validated,raw)
        validated['qualification_ids'].append('changed copy')
        self.assertEqual(raw,before)
        self.assertEqual(scope_schema(['E1'],['E1'])['oneOf'][0]['properties']['qualification_ids']['maxItems'],0)
        insufficient={'decision':'insufficient','reason':'The referent cannot be resolved.'}
        self.assertEqual(validate_scope(insufficient,['E1'],['E1']),insufficient)
        self.assertEqual(validate_synthesis({'decision':'no_group','reason':'No common meaning supported.'},[]),
                         {'decision':'no_group','reason':'No common meaning supported.'})
        self.assertEqual(validate_synthesis(category(),['C1','C2'])['counterexample_code_ids'],[])

    def test_synthesis_requires_two_known_codes_disjoint_roles_and_six_total(self):
        raw=category();allowed=[f'C{i}' for i in range(1,9)]
        self.assertEqual(validate_synthesis(raw,allowed),raw)
        for mutate in (
            lambda v:v.update(code_ids=['C1']),
            lambda v:v.update(code_ids=['C1','C1']),
            lambda v:v.update(qualification_code_ids=['C1']),
            lambda v:v.update(qualification_code_ids=['C3'],counterexample_code_ids=['C3']),
            lambda v:v.update(counterexample_code_ids=['unknown']),
            lambda v:v.update(code_ids=['C1','C2','C3'],qualification_code_ids=['C4','C5'],counterexample_code_ids=['C6','C7']),
            lambda v:v.update(decision='theme'),
            lambda v:v.update(theme='Pretend finding'),
        ):
            value=deepcopy(raw);mutate(value)
            with self.assertRaises(ValueError):
                validate_synthesis(value,allowed)
        boundary=dict(raw,code_ids=['C1','C2'],qualification_code_ids=['C3','C4'],counterexample_code_ids=['C5','C6'])
        self.assertEqual(validate_synthesis(boundary,allowed),boundary)

    def test_synthesis_refusal_decoder_allows_completion_and_host_keeps_bounds(self):
        schema = synthesis_schema(['C1', 'C2'])
        self.assertEqual(schema['oneOf'][1]['properties']['reason'],
                         {'type': 'string', 'minLength': 1})
        reason = ('The accounts describe different purposes and knowledge positions. ' * 8
                  + 'A shared relationship remains unsupported by this response.')
        self.assertGreater(len(reason), 400)
        refusal = {'decision': 'no_group', 'reason': reason}
        raw = json.dumps(refusal)
        self.assertEqual(validate_synthesis(raw, ['C1', 'C2']), refusal)
        self.assertEqual(raw, json.dumps(refusal))
        boundary = dict(refusal, reason='x' * MAX_SYNTHESIS_REASON_CHARS)
        self.assertEqual(validate_synthesis(boundary, []), boundary)
        for invalid in ('', '  \n', None, 1, True,
                        'x' * (MAX_SYNTHESIS_REASON_CHARS + 1)):
            with self.subTest(reason_type=type(invalid).__name__), self.assertRaises(ValueError):
                validate_synthesis(dict(refusal, reason=invalid), [])
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            validate_synthesis(json.dumps(refusal) + ' ' * MAX_RESPONSE_BYTES, [])

    def test_synthesis_legacy_refusal_and_positive_contract_remain_distinct(self):
        legacy = '{"decision":"no_group","reason":"Only a common topic is supported."}'
        self.assertEqual(validate_synthesis(legacy, []), json.loads(legacy))
        positive = synthesis_schema(['C1', 'C2'])['oneOf'][0]
        self.assertEqual(positive['required'], ['decision', 'label', 'shared_meaning', 'code_ids',
                                             'qualification_code_ids', 'counterexample_code_ids', 'uncertainty'])
        self.assertEqual(positive['properties']['label']['maxLength'], 120)
        self.assertEqual(positive['properties']['shared_meaning']['maxLength'], 600)
        self.assertEqual(positive['properties']['uncertainty']['maxLength'], 400)
        self.assertEqual(validate_synthesis(category(), ['C1', 'C2']), category())
        for mixed in (dict(category(), reason='No shared meaning.'),
                      dict(category(), uncertainty='x' * 401),
                      dict(decision='no_group', reason='No shared meaning.', label='A category')):
            with self.subTest(mixed=mixed), self.assertRaises(ValueError):
                validate_synthesis(mixed, ['C1', 'C2'])
        # Other stages retain their original 400-character refusal limit.
        self.assertEqual(decision_schema(['E1'])['oneOf'][1]['properties']['reason']['maxLength'], 400)
        with self.assertRaises(ValueError):
            validate_decision(dict(decision='abstain', reason='x' * 401), ['E1'])

    def test_grounded_scope_closed_branches_and_no_generated_boundaries(self):
        schema = grounded_scope_schema(['E1', 'E2'], ['E1'])
        for branch, fields in zip(schema['oneOf'], (
            {'decision', 'definition', 'qualification_ids', 'counterexample_ids'},
            {'decision', 'reason'},
        )):
            self.assertFalse(branch['additionalProperties'])
            self.assertEqual(set(branch['required']), fields)
            self.assertEqual(set(branch['properties']), fields)
        properties = schema['oneOf'][0]['properties']
        self.assertEqual(properties['definition']['maxLength'], 600)
        self.assertEqual(properties['qualification_ids']['items']['enum'], ['E2'])
        self.assertEqual(properties['counterexample_ids']['items']['enum'], ['E2'])
        raw = dict(decision='define', definition='Copies protect against loss.',
                   qualification_ids=[], counterexample_ids=[])
        validated = validate_grounded_scope(json.dumps(raw), ['E1'], ['E1'])
        self.assertEqual(validated, raw)
        validated['qualification_ids'].append('E2')
        self.assertEqual(raw['qualification_ids'], [])
        for field in ('inclusion', 'exclusion', 'uncertainty', 'support_ids', 'reason'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_grounded_scope(dict(raw, **{field: 'extra'}), ['E1'], ['E1'])
        for field in raw:
            missing = dict(raw)
            del missing[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                validate_grounded_scope(missing, ['E1'], ['E1'])

    def test_grounded_scope_roles_are_disjoint_known_and_six_total(self):
        allowed = [f'E{i}' for i in range(1, 9)]
        raw = dict(decision='define', definition='Copies protect against loss.',
                   qualification_ids=['E2', 'E3', 'E4'], counterexample_ids=['E5', 'E6', 'E7'])
        self.assertEqual(validate_grounded_scope(raw, allowed, ['E1']), raw)
        for quals, counters in ((['E1'], []), ([], ['E1']), (['E2'], ['E2']),
                                (['E2', 'E2'], []), (['unknown'], []),
                                ([True], []), (('E2',), []), ('E2', []),
                                (['E2', 'E3', 'E4'], ['E5', 'E6', 'E7', 'E8']),
                                (allowed[1:], [])):
            with self.subTest(quals=quals, counters=counters), self.assertRaises(ValueError):
                validate_grounded_scope(dict(raw, qualification_ids=quals, counterexample_ids=counters),
                                        allowed, ['E1'])
        with self.assertRaises(ValueError):
            grounded_scope_schema(['E1'], ['unknown'])
        with self.assertRaises(ValueError):
            validate_grounded_scope(raw, allowed, ['unknown'])
        empty = grounded_scope_schema(['E1'], ['E1'])['oneOf'][0]['properties']
        self.assertEqual(empty['qualification_ids']['maxItems'], 0)

    def test_grounded_scope_text_bounds_and_insufficient_exclusivity(self):
        raw = dict(decision='define', definition='x' * 600,
                   qualification_ids=[], counterexample_ids=[])
        self.assertEqual(validate_grounded_scope(raw, [], []), raw)
        for value in ('x' * 601, '', ' ', True, None, 'True', ' false ', 'YES', 'No', 'None', 'n-a', 'N/A'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_grounded_scope(dict(raw, definition=value), [], [])
        insufficient = {'decision': 'insufficient', 'reason': 'x' * 400}
        self.assertEqual(validate_grounded_scope(insufficient, [], []), insufficient)
        for value in (dict(insufficient, reason='x' * 401), dict(insufficient, reason=' '),
                      dict(insufficient, definition='Also define.'), dict(raw, decision='insufficient'),
                      dict(insufficient, decision=True)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_grounded_scope(value, [], [])
        with self.assertRaises(ValueError):
            validate_grounded_scope('{"decision":"define","decision":"insufficient","reason":"A"}', [], [])

    def test_string_bounds_and_response_byte_bound(self):
        base=dict(decision='code',label='x'*120,claim='x'*600,evidence_ids=['E1'])
        self.assertEqual(validate_decision(base,['E1']),base)
        for field in ('label','claim'):
            for value in (base[field]+'x',' ',True,5,None):
                with self.assertRaises(ValueError):
                    validate_decision(dict(base,**{field:value}),['E1'])
        for validate,args,raw,field,limit in (
            (validate_decision,([],),{'decision':'abstain','reason':'x'},'reason',400),
            (validate_scope,(['E1'],['E1']),definition(),'definition',600),
            (validate_scope,(['E1'],['E1']),definition(),'inclusion',400),
            (validate_scope,(['E1'],['E1']),definition(),'exclusion',400),
            (validate_scope,(['E1'],['E1']),definition(),'uncertainty',400),
            (validate_synthesis,(['C1','C2'],),category(),'shared_meaning',600),
        ):
            with self.assertRaises(ValueError):validate(dict(raw,**{field:'x'*(limit+1)}),*args)
        with self.assertRaisesRegex(ValueError,'bytes'):
            validate_decision(' '*MAX_RESPONSE_BYTES+'{}',[])
        with self.assertRaises(ValueError):
            validate_decision(b'\xff',[])
        self.assertEqual(validate_decision(b'{"decision":"abstain","reason":"No full claim."}',[]),
                         {'decision':'abstain','reason':'No full claim.'})


if __name__ == '__main__':
    unittest.main()
