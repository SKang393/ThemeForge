"""Read-only exact-source card viewing and local exports, without inference."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from .source_cards import validate_cards, export_cards_html

MAX_FILE_BYTES = 16 * 1024 * 1024


def _local_path(value):
    text = str(value)
    if text.casefold().startswith(('http://', 'https://', 'file://')):
        raise ValueError('Choose an explicit absolute local path.')
    path = Path(value).expanduser()
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise ValueError('Choose an explicit absolute local path, not a network share.')
    path = path.resolve()
    if str(path).startswith(('\\\\', '//')):
        raise ValueError('Network-share paths are not supported.')
    return path


class SourceCardDialog(tk.Toplevel):
    def __init__(self, parent, cards=None):
        validated = validate_cards(cards) if cards is not None else None
        super().__init__(parent)
        self.title('Exact-source review cards')
        self.geometry('1100x760')
        self.minsize(800, 560)
        self.cards = None
        self.loaded_path = None
        self._rows = {}
        self.status = tk.StringVar(self, value='Open exact-source card JSON. No model execution or conceptual coding is performed.')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        controls = ttk.Frame(self)
        controls.grid(row=0, column=0, sticky='ew', padx=12, pady=10)
        ttk.Button(controls, text='Open source-card JSON', command=self.load).pack(side='left')
        self.json_button = ttk.Button(controls, text='Export JSON', command=lambda: self.export('json'), state='disabled')
        self.json_button.pack(side='left', padx=8)
        self.html_button = ttk.Button(controls, text='Export HTML', command=lambda: self.export('html'), state='disabled')
        self.html_button.pack(side='left')
        content = ttk.Panedwindow(self, orient='horizontal')
        content.grid(row=1, column=0, sticky='nsew', padx=12)
        listing = ttk.Frame(content)
        listing.columnconfigure(0, weight=1); listing.rowconfigure(0, weight=1)
        self.card_tree = ttk.Treeview(listing, columns=('source', 'speaker'), show='headings', selectmode='browse', height=12)
        self.card_tree.heading('source', text='Source'); self.card_tree.heading('speaker', text='Speaker')
        self.card_tree.column('source', width=200); self.card_tree.column('speaker', width=90)
        self.card_tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(listing, orient='vertical', command=self.card_tree.yview)
        scroll.grid(row=0, column=1, sticky='ns'); self.card_tree.configure(yscrollcommand=scroll.set)
        content.add(listing, weight=1)
        detail_frame = ttk.Frame(content)
        detail_frame.columnconfigure(0, weight=1); detail_frame.rowconfigure(0, weight=1)
        self.detail = tk.Text(detail_frame, wrap='word', state='disabled', width=70, height=20)
        self.detail.grid(row=0, column=0, sticky='nsew')
        detail_scroll = ttk.Scrollbar(detail_frame, orient='vertical', command=self.detail.yview)
        detail_scroll.grid(row=0, column=1, sticky='ns'); self.detail.configure(yscrollcommand=detail_scroll.set)
        content.add(detail_frame, weight=3)
        self.card_tree.bind('<<TreeviewSelect>>', self._selection_changed)
        ttk.Label(self, text='Exact source material only. No conceptual labels, definitions, categories or themes. Neither AI interpretation nor human coding; selection is not exhaustive.', wraplength=1050).grid(row=2, column=0, sticky='w', padx=12, pady=(10, 4))
        ttk.Label(self, textvariable=self.status, wraplength=1050).grid(row=3, column=0, sticky='w', padx=12, pady=(0, 12))
        if validated is not None:
            self._set_cards(validated)

    def _set_cards(self, cards):
        self.cards = deepcopy(cards)
        self._rows = {card['card_id']: card for card in self.cards['cards']}
        self.card_tree.delete(*self.card_tree.get_children())
        for card_id, card in self._rows.items():
            self.card_tree.insert('', tk.END, iid=card_id, values=(card['source_name'], card['speaker']))
        for button in (self.json_button, self.html_button):
            button.configure(state='normal')
        if self._rows:
            self.card_tree.selection_set(next(iter(self._rows)))
        self._selection_changed()
        self.status.set(f'{len(self._rows)} exact-source cards. Original provenance retained; no interpretation or coding performed.')

    def _selection_changed(self, _event=None):
        selected = self.card_tree.selection()
        sections = []
        if selected:
            card = self._rows[selected[0]]
            sections = [f"Card: {card['card_id']}\nOrigin: {card['origin']}\nSource: {card['source_name']}\nSpeaker: {card['speaker']}\nSHA256: {card['source_hash']}\nCharacters {card['source_start']}:{card['source_end']}",
                        'Exact complete source turn:\n' + card['text'],
                        'Exact source spans, not conceptual codes:']
            for span in card['support_spans']:
                sections.append(f"{span['span_id']}\nCharacters {span['source_start']}:{span['source_end']}\n{span['text']}")
            context = card['context']
            sections += [f"Context, not additional supporting evidence\nCharacters {context['source_start']}:{context['source_end']}\n{context['text']}",
                         'Reference context, not additional supporting evidence:\n' + context['reference_context'],
                         'Original source artifact provenance:\n' + json.dumps(self.cards['input_artifact'], ensure_ascii=False, indent=2)]
        self.detail.configure(state='normal')
        self.detail.delete('1.0', tk.END)
        self.detail.insert('1.0', '\n\n'.join(sections) if sections else 'No selected source cards.')
        self.detail.configure(state='disabled')

    def load(self, path=None):
        path = path or filedialog.askopenfilename(parent=self, title='Open exact-source cards', filetypes=[('Source-card JSON', '*.json')])
        if not path:
            return False
        try:
            source = _local_path(path)
            with source.open('rb') as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError('Source-card file exceeds 16 MiB.')
            cards = validate_cards(raw)
            self._set_cards(cards)
            self.loaded_path = source
            self.status.set(f'Opened {source.name}: {len(cards["cards"])} exact-source cards, without model execution or project import.')
            return True
        except (OSError, ValueError, TypeError) as error:
            self.status.set(f'Source cards not loaded: {error}')
            return False

    def export(self, format, path=None):
        if self.cards is None:
            return False
        if format not in {'json', 'html'}:
            self.status.set('Choose JSON or HTML for source-card export.')
            return False
        path = path or filedialog.asksaveasfilename(parent=self, title='Export exact-source cards to a new file',
            defaultextension='.' + format, filetypes=[(format.upper(), '*.' + format)])
        if not path:
            return False
        try:
            target = _local_path(path)
            cards = validate_cards(self.cards)
            content = (json.dumps(cards, ensure_ascii=False, indent=2, allow_nan=False) + '\n') if format == 'json' else export_cards_html(cards)
            encoded = content.encode('utf-8')
            if format == 'json' and len(encoded) > MAX_FILE_BYTES:
                raise ValueError('Source-card JSON exceeds the 16 MiB reopen limit; no file was created.')
            # Write exactly the measured bytes, including on Windows where text
            # newline expansion could otherwise exceed an exact size boundary.
            with target.open('xb') as stream:
                stream.write(encoded)
            self.status.set(f'Exported exact-source {format.upper()} to {target.name}. No conceptual interpretation added.')
            return True
        except (OSError, ValueError, TypeError) as error:
            self.status.set(f'Source-card export failed: {error}')
            return False
