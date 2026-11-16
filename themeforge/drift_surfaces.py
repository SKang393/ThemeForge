"""View-only bindings for drift assistance; no edits to saved model results."""
from .drift_review import review_drift


def additional_drift_view(result, code, sources):
    """Bound the registry to selected evidence and return only additional cues.

    Legacy baseline warnings remain at their existing UI/export location. A
    missing source binding is visible as unavailable, never as an empty pass.
    """
    try:
        selected=[entry for field in ('evidence','qualifications','counterexamples')
                  for entry in code.get(field,[])]
        registry={key:value for key,value in result['registry'].items() if value in selected}
        view=dict(result,registry=registry)
        diagnostic=review_drift(view,code,sources=sources)
        additional={k:v for k,v in diagnostic.items() if k!='baseline_review'}
        # The caller already renders the baseline. Suppress only identical
        # generic role reminders here, never a substantive warning or a
        # reminder with different evidence. Keep raw diagnostics untouched.
        baseline=diagnostic['baseline_review']['warnings']
        attention=diagnostic.get('attention_cues',[])
        retained=[cue for cue in attention if not (
            cue.get('kind') in ('qualification_relationship_unverified','counterexample_relationship_unverified')
            and not cue.get('fields') and cue in baseline)]
        additional['attention_cues']=retained
        if len(retained)!=len(attention):
            additional['attention_cues_already_shown_with_baseline']=len(attention)-len(retained)
        return additional
    except (KeyError,TypeError,ValueError) as error:
        return dict(status='unavailable_not_semantically_checked',warnings=[],
                    reason=str(error),notice='Inspect the original source. Missing diagnostics never approve a meaning or change a draft.')


def legacy_drift_view(result,code):
    sources={d['name']:{'text':d['text'],'source_hash':d['sha256']} for d in result.get('source_documents',[])}
    return additional_drift_view(result,code,sources)
