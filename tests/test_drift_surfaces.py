from copy import deepcopy
import unittest
from unittest.mock import patch
from themeforge.drift_surfaces import legacy_drift_view
from tests.test_meaning_review import source_case


class DriftSurfaceTests(unittest.TestCase):
    def test_saved_prose_and_baseline_are_not_revised(self):
        result,code=source_case('I want to try the task.', 'I want to try the task.')
        code['label']='Tries the task'
        before=deepcopy(result)
        report=legacy_drift_view(result,code)
        self.assertEqual(report['status'],'review_cues_not_semantic_validation')
        self.assertTrue(any(w['kind']=='desired_action_as_performed_needs_review' for w in report['warnings']))
        self.assertNotIn('baseline_review',report)
        self.assertEqual(result,before)

    def test_unavailable_binding_is_not_warning_free_approval(self):
        result,code=source_case('I can try the task.', 'I will try the task.')
        result['source_documents']=[]
        report=legacy_drift_view(result,code)
        self.assertEqual(report['status'],'unavailable_not_semantically_checked')
        self.assertIn('unavailable',report['reason'])

    def test_large_unselected_registry_does_not_disable_selected_review(self):
        result,code=source_case('I want to try the task.', 'I want to try the task.')
        code['label']='Tries the task'
        for n in range(600):result['registry']['unused-'+str(n)]={'unused_test_only':n}
        report=legacy_drift_view(result,code)
        self.assertEqual(report['status'],'review_cues_not_semantic_validation')
        self.assertEqual(len(report['warnings']),1)

    def test_role_reminder_display_keeps_distinct_evidence_and_substantive_alerts(self):
        result,code=source_case('I can try the task.', 'I will try the task.')
        reminder=dict(kind='qualification_relationship_unverified',fields=[],
                      message='Inspect this limit.',source_evidence=[dict(source_name='A',source_start=4,text='if asked')])
        other_source=deepcopy(reminder)
        other_source['source_evidence'][0]['source_name']='B'
        substantive=dict(kind='ability_to_commitment_needs_review',fields=['definition'],message='can / will')
        diagnostic=dict(status='review_cues_not_semantic_validation',
                        baseline_review=dict(warnings=[reminder]),warnings=[substantive],
                        attention_cues=[deepcopy(reminder),other_source,substantive])
        before=deepcopy(diagnostic)
        with patch('themeforge.drift_surfaces.review_drift',return_value=diagnostic):
            report=legacy_drift_view(result,code)
        self.assertEqual(report['attention_cues'],[other_source,substantive])
        self.assertEqual(report['warnings'],[substantive])
        self.assertEqual(report['attention_cues_already_shown_with_baseline'],1)
        self.assertEqual(diagnostic,before)


if __name__=='__main__':unittest.main()
