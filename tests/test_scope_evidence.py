from copy import deepcopy
import unittest
from themeforge import scope_evidence as scope


class ScopeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.e={'e1':{'text':'We can share these records.'},'e2':{'text':'Only if the participant agrees.'},
                'e3':{'text':'So, yeah.'},'e4':{'text':'Our records are on a server.'}}
        self.c={'label':'Conditional record sharing','claim':'We can share these records.','evidence_ids':['e1'],'referent_ids':[]}
        self.role=dict(evidence_id='e2',role='qualification',kind='condition',source_fragment='Only if the participant agrees',
                       claim_fragment='share these records',relation='Sharing is conditional on participant agreement.')
        self.value=dict(decision='define',definition='Possible sharing of records when the participant agrees.',roles=[self.role])

    def test_empty_roles_need_no_fabricated_limit_and_positive_condition_is_grounded(self):
        empty=dict(self.value,roles=[])
        self.assertEqual(scope.validate(empty,self.e,self.c),empty)
        self.assertEqual(scope.validate(self.value,self.e,self.c),self.value)
        self.assertEqual(scope.role_ids(self.value,'qualification'),['e2'])

    def test_raw_filler_partition_rejected_unchanged(self):
        bad=deepcopy(self.value);bad['roles'][0].update(evidence_id='e3',source_fragment='So, yeah.')
        before=deepcopy(bad)
        with self.assertRaisesRegex(ValueError,'filler'):scope.validate(bad,self.e,self.c)
        self.assertEqual(before,bad)

    def test_unselected_is_not_a_relationship_and_foreign_fragments_rejected(self):
        for field,value in [('relation','This is a remaining sentence outside the support.'),
                            ('source_fragment','Only if management agrees'),('claim_fragment','publish all records')]:
            with self.subTest(field=field):
                bad=deepcopy(self.value);bad['roles'][0][field]=value
                with self.assertRaises(ValueError):scope.validate(bad,self.e,self.c)

    def test_condition_cannot_be_mislabeled_contradiction_and_support_cannot_be_reassigned(self):
        for patch in ({'role':'counterexample'},{'evidence_id':'e1','source_fragment':'share these records'}):
            bad=deepcopy(self.value);bad['roles'][0].update(patch)
            with self.assertRaises(ValueError):scope.validate(bad,self.e,self.c)

    def test_no_auxiliary_choices_permits_only_empty_array(self):
        definition=scope.schema(['e1'],['e1'])['oneOf'][0]['properties']
        self.assertEqual(definition['roles']['minItems'],0)
        self.assertEqual(definition['roles']['maxItems'],0)

    def test_exact_quote_does_not_certify_semantic_relationship(self):
        bad=deepcopy(self.value);bad['roles'][0].update(evidence_id='e4',source_fragment='records are on a server',
            kind='scope_limit',relation='This restricts the scope to the server records.')
        accepted=scope.validate(bad,self.e,self.c)
        cues=scope.review_warnings(accepted,self.e,self.c)
        self.assertFalse(cues['semantic_validation'])
        self.assertTrue(cues['warnings'])

    def test_default_guard_rejects_filler_preserves_useful_condition_and_allows_none(self):
        raw=dict(decision='define',definition='Possible sharing with participant agreement.',qualification_ids=['e2'],counterexample_ids=[])
        self.assertEqual(scope.validate_guarded_legacy(raw,self.e,self.c),raw)
        raw['qualification_ids']=[]
        self.assertEqual(scope.validate_guarded_legacy(raw,self.e,self.c),raw)
        raw['qualification_ids']=['e3'];before=deepcopy(raw)
        with self.assertRaisesRegex(ValueError,'filler'):scope.validate_guarded_legacy(raw,self.e,self.c)
        self.assertEqual(raw,before)
        self.assertEqual(scope.DEFAULT_VERSION,scope.GUARDED_VERSION)

    def test_bare_negation_or_agreement_is_not_automatically_discarded(self):
        for answer in ('No.', 'Yes.', 'Sure.'):
            with self.subTest(answer=answer):
                evidence=deepcopy(self.e);evidence['e3']['text']=answer
                raw=dict(decision='define',definition='Possible sharing with participant agreement.',
                         qualification_ids=[],counterexample_ids=['e3'])
                self.assertEqual(scope.validate_guarded_legacy(raw,evidence,self.c),raw)


if __name__=='__main__':unittest.main()
