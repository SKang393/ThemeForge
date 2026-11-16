"""Explicit category authoring over immutable member evidence, without inference."""
import json
import tkinter as tk
from tkinter import ttk


def source_context_text(code):
    """Expose exact background without silently promoting it to member support."""
    rows = ['SELECTED REFERENT CONTEXT (not additional support)']
    for span in code.get('referent_context', []):
        rows.append(f"{span['role']} / {span['source_name']} / {span['speaker']}\n"
                    f"SHA256 {span['source_hash']}\nCharacters {span['source_start']}:{span['source_end']}\n{span['text']}")
    if not code.get('referent_context'):
        rows.append('None selected; inspect the full target for unresolved referents.')
    seen = set()
    for role in ('evidence', 'qualifications', 'counterexamples'):
        for span in code[role]:
            identity = (span['source_hash'], span['unit_source_start'], span['unit_source_end'])
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(f"FULL TARGET TURN (not automatically fully coded)\n{span['source_name']} / {span['speaker']}\n"
                        f"SHA256 {span['source_hash']}\nCharacters {span['unit_source_start']}:{span['unit_source_end']}\n{span['unit_text']}")
            rows.append(f"SURROUNDING CONTEXT (not additional support; may include other speakers)\n"
                        f"Characters {span['context_start']}:{span['context_end']}\n{span['context_text']}")
    return '\n\n'.join(rows)


def open_reviewed_category_editor(owner, code_ids, *, reviewer_kind):
    """Keep every edit local until Apply succeeds against the same parent draft."""
    window = tk.Toplevel(owner)
    window.title('Add category without a model')
    window.geometry('1050x850')
    window.minsize(820, 650)
    window.transient(owner)
    window.columnconfigure(1, weight=1)
    window.rowconfigure(3, weight=1)
    before_digest = owner.result['result_sha256']
    notice = (f'Author: {reviewer_kind}. This creates a provisional category during review. '
              'It preserves the model outcome, original codes and raw calls. '
              'Source evidence is copied exactly; interpretation is not automatically validated.')
    ttk.Label(window, text=notice, wraplength=970).grid(row=0, column=0, columnspan=2, sticky='w', padx=12, pady=10)
    ttk.Label(window, text='Category name').grid(row=1, column=0, sticky='nw', padx=12)
    label = ttk.Entry(window)
    label.grid(row=1, column=1, sticky='ew', padx=12, pady=(0, 8))
    ttk.Label(window, text='Shared relationship').grid(row=2, column=0, sticky='nw', padx=12)
    shared = tk.Text(window, height=3, wrap='word')
    shared.grid(row=2, column=1, sticky='ew', padx=12, pady=(0, 8))
    book = ttk.Notebook(window)
    book.grid(row=3, column=0, columnspan=2, sticky='nsew', padx=12, pady=4)
    notes, previews = {}, {}
    for index, cid in enumerate(code_ids, 1):
        code = owner._codes[cid]
        frame = ttk.Frame(book)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        book.add(frame, text=f'Member {index}')
        ttk.Label(frame, text=f"{code['label']}\n{code['definition']}", wraplength=960).grid(row=0, column=0, sticky='w', padx=8, pady=5)
        ttk.Label(frame, text='Explain this member\'s contribution and any different condition, position or limit.').grid(row=1, column=0, sticky='w', padx=8)
        exact = tk.Text(frame, height=10, wrap='word')
        exact.grid(row=2, column=0, sticky='nsew', padx=(8, 0), pady=5)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=exact.yview)
        scroll.grid(row=2, column=1, sticky='ns', pady=5)
        exact.configure(yscrollcommand=scroll.set)
        rows = [f"Code: {cid}", f"Claim: {code['claim']}",
                f"Wording origins: {code.get('field_origins', {})}",
                f"Evidence-role origins: {code.get('evidence_role_origins', {})}"]
        if 'candidate_review_workflow' in owner.result:
            from .codebook_review import candidate_review_state
            rows.append('CANDIDATE REVIEW (declared authorship, not semantic validation)\n' +
                        json.dumps(candidate_review_state(owner.result, cid), ensure_ascii=False, indent=2))
        for role in ('evidence', 'qualifications', 'counterexamples'):
            rows.append('\nMEMBER ' + role.upper() + ' (not an automatic category-level judgment)')
            for span in code[role]:
                rows.append(f"{span['source_name']} / {span['speaker']}\nSHA256 {span['source_hash']}\nCharacters {span['source_start']}:{span['source_end']}\n{span['text']}")
            if not code[role]:
                rows.append('None selected; not proof of absence.')
        rows.append(source_context_text(code))
        owner._replace(exact, '\n\n'.join(rows))
        note = tk.Text(frame, height=3, wrap='word')
        note.grid(row=3, column=0, columnspan=2, sticky='ew', padx=8, pady=(0, 6))
        notes[cid], previews[cid] = note, exact
    ttk.Label(window, text='Category limits\nand differences').grid(row=4, column=0, sticky='nw', padx=12, pady=5)
    limits = tk.Text(window, height=3, wrap='word')
    limits.grid(row=4, column=1, sticky='ew', padx=12, pady=5)
    ttk.Label(window, text='Reason for this\nreview decision').grid(row=5, column=0, sticky='nw', padx=12, pady=5)
    reason = tk.Text(window, height=2, wrap='word')
    reason.grid(row=5, column=1, sticky='ew', padx=12, pady=5)
    error = tk.StringVar(window, '')
    ttk.Label(window, textvariable=error, wraplength=970).grid(row=6, column=0, columnspan=2, sticky='w', padx=12)
    ttk.Label(window, text='All fields are required. Categories are added as separate review records. To change saved membership or wording, reopen the earlier parent draft and save a new branch.', wraplength=970).grid(row=7, column=0, columnspan=2, sticky='w', padx=12, pady=5)
    buttons = ttk.Frame(window)
    buttons.grid(row=8, column=0, columnspan=2, sticky='e', padx=12, pady=10)

    def apply():
        if owner.result is None or owner.result['result_sha256'] != before_digest:
            error.set('The parent draft changed. Cancel and reopen this editor on the current draft.')
            return
        if owner.add_reviewed_category(code_ids, label=label.get(),
                shared_meaning=shared.get('1.0', 'end-1c'),
                member_notes={cid: widget.get('1.0', 'end-1c') for cid, widget in notes.items()},
                limits=limits.get('1.0', 'end-1c'), reviewer_kind=reviewer_kind,
                reason=reason.get('1.0', 'end-1c')):
            window.destroy()
        else:
            error.set(owner.status.get())

    apply_button = ttk.Button(buttons, text='Add provisional category', command=apply)
    apply_button.pack(side='left', padx=6)
    cancel_button = ttk.Button(buttons, text='Cancel', command=window.destroy)
    cancel_button.pack(side='left', padx=6)
    window.label_entry, window.shared_text = label, shared
    window.member_notes, window.source_previews = notes, previews
    window.limits_text, window.reason_text = limits, reason
    window.error_text, window.author_notice = error, notice
    window.apply_button, window.cancel_button = apply_button, cancel_button
    if owner.state() == 'withdrawn':
        window.withdraw()
    else:
        window.grab_set()
    return window


def reviewed_categories_text(result):
    """Readable author/evidence view; the original model pane stays untouched."""
    categories = result.get('reviewed_categories', [])
    if not categories:
        return ('No category has been added during review. Select 2 to 6 defined codes from '
                'at least two source contents, review their evidence, then choose Add category (no model).')
    rows = ['CATEGORIES ADDED DURING REVIEW\nProvisional interpretations with explicit authorship. '
            'The original model outcome remains in Category proposals.']
    history = {event['category_id']: event for event in result['reviewed_category_history']}
    for category in categories:
        rows.extend([f"\n{category['label']}\nAuthor: {category['origin']} | {category['review_status']}",
                     'Shared relationship: ' + category['shared_meaning'],
                     'Limits and differences: ' + category['limits'],
                     'Review reason: ' + history[category['id']]['reason']])
        for member in category['members']:
            rows.append(f"MEMBER {member['code_id']}: {member['label']}\n{member['definition']}\nContribution/limits: {member['member_note']}")
            if 'candidate_review' in member:
                rows.append('CANDIDATE REVIEW SNAPSHOT (AI is not researcher approval)\n' +
                            json.dumps(member['candidate_review'], ensure_ascii=False, indent=2))
            for role in ('evidence', 'qualifications', 'counterexamples'):
                rows.append('Member ' + role + ':')
                for span in member[role]:
                    rows.append(f"{span['source_name']} / {span['speaker']} chars {span['source_start']}:{span['source_end']}\nSHA256 {span['source_hash']}\n{span['text']}")
                if not member[role]:
                    rows.append('None selected; not proof of absence.')
            rows.append(source_context_text(member))
    return '\n\n'.join(rows)
