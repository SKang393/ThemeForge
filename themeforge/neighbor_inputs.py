"""Source-turn selection for neighbor retrieval, without generated code prose."""
from .source_cards import build_source_cards
from .semantic_neighbors import prepare_packet


def packet_from_native(value):
    cards = build_source_cards(value)
    nodes = []
    for card in cards['cards']:
        node = {key: card[key] for key in ('source_name', 'source_hash', 'speaker', 'source_start', 'source_end', 'text')}
        context = card['context']
        node.update(id=card['unit_id'], context_start=context['source_start'], context_end=context['source_end'],
                    context_text=context['text'])
        nodes.append(node)
    return prepare_packet(nodes, sources=[dict(source_name=d['name'], source_hash=d['sha256'], text=d['text'])
                                          for d in cards['source_documents']],
                          provenance=dict(selection='selected_original_turns_from_validated_native_artifact',
                              input_artifact=cards['input_artifact'], source_cards_sha256=cards['cards_sha256'],
                              input_fields=['node.text'], generated_labels_used=False,
                              human_coding=False, meaning_units_not_exhaustive=True))
