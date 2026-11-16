"""Source-exact boundary recovery review export, without inferred theme labels."""
from html import escape
import json
from .study_segments import validate_segment_recovery


def export_recovery_html(value):
    validate_segment_recovery(value)
    e=lambda x:escape(str(x),quote=True)
    show=lambda x:'<pre>'+e(json.dumps(x,ensure_ascii=False,indent=2))+'</pre>'
    rows=['<!doctype html><meta charset="utf-8"><title>Boundary recovery coverage</title>',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'">',
        '<style>body{font:16px/1.5 system-ui;max-width:1100px;margin:auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>',
        '<h1>Boundary recovery: source coverage, unresolved attribution</h1>',
        '<p>Original failed attempt and source ledger are preserved. Distinct segment vectors are not a pooled parent, independent participants, codes, categories or themes.</p>',
        show(value['recovery_coverage']),'<h2>Encoder budget</h2>',
        show(dict(payload_tokens_per_core=254,special_tokens=2,maximum_input_tokens=256,encoder_generated_output_tokens=0,
                  context_in_encoder_vector=False,LLM_generation_reserve='Separate native planning must reserve output plus EOS; this encoder produces no text.')),
        '<h2>Original failed source span</h2>',show(value['plan']['original_parent_coverage']),show(value['plan']['parent'])]
    for segment in value['plan']['segments']:
        saved=value['encoder_ledger']
        state=saved['coverage'][segment['id']] if saved else dict(status=segment['planning_status'])
        rows+=['<article><h2>'+e(segment['id'])+'</h2>',show(dict(segment=segment,encoding_state=state)),'</article>']
    rows+=['<details><summary>Full parent and adjacent archive context</summary>',show(value['plan']['parent_archive_context']),'</details>',
           '<p>Recovery SHA-256: '+e(value['recovery_sha256'])+'</p>']
    return '\n'.join(rows)
