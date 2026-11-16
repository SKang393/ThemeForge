"""Explicit post-group revision window; never starts inference or changes parent."""

import json

import tkinter as tk

from tkinter import ttk, filedialog, messagebox

from .review_branch import begin_branch, revise_branch, undo_revision, group_states, save_branch, load_branch

from .codebook_review import source_review_view

from .review_branch import revise_branch_roles, split_branch, decide_branch_candidate, rereview_branch_group



class ReviewBranchDialog(tk.Toplevel):

    def __init__(self, parent, result):

        super().__init__(parent)

        self.title('Separate post-group revision branch'); self.geometry('1000x800')

        self.original=result; self.branch=None

        self.saved_branch_sha256=None

        self.protocol("WM_DELETE_WINDOW",lambda:self.invoke(self.request_close))

        self.kind=tk.StringVar(self,value='assistant_development_review')

        self.name=tk.StringVar(self); self.reason=tk.StringVar(self)

        self.notice=tk.StringVar(self,value='Choose declared authorship and reason, then create a separate branch or open a saved branch. No approval is inherited by groups.')

        self.columnconfigure(1,weight=1); self.rowconfigure(7,weight=1)

        ttk.Label(self,text='Declared author (not verified)').grid(row=0,column=0)

        ttk.Combobox(self,textvariable=self.kind,values=('assistant_development_review','researcher'),state='readonly').grid(row=0,column=1,sticky='ew')

        for row,title,var in ((1,'Reviewer name',self.name),(2,'Reason for branch / edit',self.reason)):

            ttk.Label(self,text=title).grid(row=row,column=0)

            ttk.Entry(self,textvariable=var).grid(row=row,column=1,sticky='ew')

        actions=ttk.Frame(self); actions.grid(row=3,column=0,columnspan=2)

        for label,action in (('Create branch',self.create_branch),('Open branch JSON',self.open_branch),('Save new branch JSON',self.save),('Undo last revision',self.undo)):

            ttk.Button(actions,text=label,command=lambda f=action:self.invoke(f)).pack(side='left')

        self.choice=ttk.Combobox(self,state='readonly'); self.choice.grid(row=4,column=0,columnspan=2,sticky='ew')

        self.choice.bind('<<ComboboxSelected>>',lambda e:self.invoke(self.select_code))

        self.editors={}; fields=ttk.Frame(self); fields.grid(row=5,column=0,columnspan=2,sticky='ew'); fields.columnconfigure(1,weight=1)

        for row,field in enumerate(('label','claim','definition')):

            ttk.Label(fields,text=field).grid(row=row,column=0)

            editor=tk.Text(fields,height=2,wrap='word'); editor.grid(row=row,column=1,sticky='ew'); self.editors[field]=editor

        ttk.Button(self,text='Apply interpretation edit to separate branch',command=lambda:self.invoke(self.apply)).grid(row=6,column=0,columnspan=2)

        advanced=ttk.Frame(self); advanced.grid(row=7,column=0,columnspan=2,sticky='ew')

        for title,callback in (('Evidence roles',self.open_roles),('Split into two candidates',self.open_split),('Re-review historical group',self.open_group)):

            ttk.Button(advanced,text=title,command=lambda f=callback:self.invoke(f)).pack(side='left')

        for decision in ('accept','edit','reject','uncertain'):

            ttk.Button(advanced,text=decision.title(),command=lambda d=decision:self.invoke(lambda:self.decide(d))).pack(side='left')

        self.rowconfigure(7,weight=0);self.rowconfigure(8,weight=1)

        self.details=tk.Text(self,wrap='word',state='disabled'); self.details.grid(row=8,column=0,columnspan=2,sticky='nsew')

        ttk.Label(self,textvariable=self.notice,wraplength=950).grid(row=9,column=0,columnspan=2,sticky='ew')



    def author(self):

        return dict(reviewer_kind=self.kind.get(), reviewer_name=self.name.get(),reason=self.reason.get())



    def invoke(self,operation):

        try: operation()

        except (ValueError,KeyError,TypeError,OSError,json.JSONDecodeError) as error: self.notice.set(str(error))



    def create_branch(self):

        if self.branch is not None: raise ValueError('Open another window to create another branch; save this branch first.')

        self.branch=begin_branch(self.original,**self.author()); self.refresh()



    def open_branch(self):

        if self.branch is not None: raise ValueError('Open another window to load another branch; save this branch first.')

        path=filedialog.askopenfilename(parent=self,filetypes=[('Branch JSON','*.json')])

        if path:

            loaded=load_branch(path)

            self.branch=loaded; self.saved_branch_sha256=loaded["branch_sha256"]; self.refresh()



    def require_branch(self):

        if self.branch is None: raise ValueError('Create or open a branch first.')



    def refresh(self):

        values=[c['id'] for c in self.branch['draft']['codes']]

        self.choice.configure(values=values)

        if self.choice.get() not in values: self.choice.set(values[0] if values else '')

        self.show_code()

        self.notice.set('Separate branch only. Parent and historical groups preserved; changed dependencies require review. Save a new JSON to retain edits. No semantic validation.')



    def has_unapplied_wording(self):
        if self.branch is None or not getattr(self, 'editors', None):
            return False
        cid=getattr(self, 'displayed_code_id', self.choice.get())
        code=next((c for c in self.branch['draft']['codes'] if c['id']==cid),None)
        return bool(code and any(w.get('1.0','end-1c') != code[f] for f,w in self.editors.items()))

    def require_applied_wording(self):
        if ReviewBranchDialog.has_unapplied_wording(self):
            raise ValueError('Apply your wording edits before saving or changing review decisions. The text has been kept.')

    def select_code(self):
        previous=getattr(self, 'displayed_code_id', None)
        if previous and ReviewBranchDialog.has_unapplied_wording(self):
            if self.choice.get()==previous: return False
            if not messagebox.askyesno('Unapplied wording',
                    'Discard unapplied wording and switch candidate? No keeps your text.',parent=self):
                self.choice.set(previous)
                return False
        self.show_code()
        return True

    def show_code(self):

        self.require_branch(); cid=self.choice.get()

        code=next(c for c in self.branch['draft']['codes'] if c['id']==cid)
        self.displayed_code_id=cid

        for field,widget in self.editors.items(): widget.delete('1.0','end'); widget.insert('1.0',code[field])

        detail=dict(exact_source_and_context=source_review_view(self.branch['draft'],cid),

                    historical_group_dependency_states=group_states(self.branch),

                    historical_groups={k:self.branch['parent'].get(k,[]) for k in ('categories','reviewed_categories','interpretive_themes')},

                    revisions=self.branch['revisions'],current_approval=False)

        self.details.configure(state='normal'); self.details.delete('1.0','end')

        self.details.insert('1.0',json.dumps(detail,ensure_ascii=False,indent=2)); self.details.configure(state='disabled')



    def apply(self):

        self.require_branch()

        edits={field:widget.get('1.0','end-1c') for field,widget in self.editors.items()}

        self.branch=revise_branch(self.branch,self.choice.get(),edits,**self.author()); self.refresh()



    def undo(self):
        ReviewBranchDialog.require_applied_wording(self)


        self.require_branch(); self.branch=undo_revision(self.branch); self.refresh()



    def save(self, *, confirmed_discard_wording=False):
        if not confirmed_discard_wording:
            ReviewBranchDialog.require_applied_wording(self)


        self.require_branch()

        path=filedialog.asksaveasfilename(parent=self,defaultextension='.json',filetypes=[('Branch JSON','*.json')])

        if not path: return False

        save_branch(self.branch,path)

        self.saved_branch_sha256=self.branch['branch_sha256']

        self.notice.set('Saved separate branch. Original unchanged; historical grouping is not current approval.')

        return True



    def request_close(self):
        # Never silently discard an in-memory branch or un-applied wording.
        if self.branch is not None:
            code=next((c for c in self.branch['draft']['codes'] if c['id']==self.choice.get()),None)
            unapplied=bool(code and any(widget.get('1.0','end-1c') != code[field] for field,widget in self.editors.items()))
            if unapplied and not messagebox.askyesno('Unapplied wording',
                    'There are wording edits that have not been applied. Discard that wording and continue closing?',parent=self):
                return False
            if self.branch['branch_sha256'] != getattr(self,'saved_branch_sha256',None):
                answer=messagebox.askyesnocancel('Unsaved revision branch',
                    'Save applied branch revisions to a new JSON before closing? Cancel keeps this window open.',parent=self)
                if answer is None: return False
                if answer:
                    saved = self.save(confirmed_discard_wording=True) if unapplied else self.save()
                    if not saved: return False
        self.destroy()
        return True

    def decide(self, decision):
        ReviewBranchDialog.require_applied_wording(self)


        self.require_branch()

        self.branch=decide_branch_candidate(self.branch,self.choice.get(),decision,**self.author());self.refresh()



    def require_snapshot(self, expected_sha256):
        if expected_sha256 is not None and (self.branch is None or self.branch['branch_sha256'] != expected_sha256):
            raise ValueError('The branch changed while this editor was open. Your text is kept; reopen the editor and inspect current evidence before applying.')

    def apply_roles(self, code_id, roles, *, expected_sha256=None):
        ReviewBranchDialog.require_snapshot(self, expected_sha256)
        ReviewBranchDialog.require_applied_wording(self)


        self.branch=revise_branch_roles(self.branch,code_id,roles,**self.author());self.refresh()



    def apply_split(self, code_id, parts, *, expected_sha256=None):
        ReviewBranchDialog.require_snapshot(self, expected_sha256)
        ReviewBranchDialog.require_applied_wording(self)


        self.branch=split_branch(self.branch,code_id,parts,**self.author());self.refresh()



    def apply_group(self, collection, group_id, fields, *, expected_sha256=None):
        ReviewBranchDialog.require_snapshot(self, expected_sha256)
        ReviewBranchDialog.require_applied_wording(self)


        self.branch=rereview_branch_group(self.branch,collection,group_id,**fields,**self.author());self.refresh()



    def _form(self, title, fields, submit):

        # Child editor does not mutate branch until the complete validated submit succeeds.

        window=tk.Toplevel(self);window.title(title);window.geometry('950x700')

        window.columnconfigure(0,weight=1);window.rowconfigure(0,weight=1)

        canvas=tk.Canvas(window);canvas.grid(row=0,column=0,sticky='nsew')

        scroll=ttk.Scrollbar(window,orient='vertical',command=canvas.yview);scroll.grid(row=0,column=1,sticky='ns');canvas.configure(yscrollcommand=scroll.set)

        body=ttk.Frame(canvas);item=canvas.create_window((0,0),window=body,anchor='nw');body.columnconfigure(0,weight=1)

        body.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))

        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(item,width=e.width))

        editors={}

        for label,initial in fields:

            ttk.Label(body,text=label,wraplength=850).pack(anchor='w',padx=8)

            editor=tk.Text(body,height=3,wrap='word');editor.insert('1.0',initial);editor.pack(fill='x',padx=8,pady=3);editors[label]=editor

        note=tk.StringVar(window,value='Use exact source evidence; explicit reviewer/name/reason come from the main branch window. No automatic approval.')

        ttk.Label(window,textvariable=note,wraplength=850).grid(row=1,column=0)

        def apply():

            try:

                submit({label:editor.get('1.0','end-1c') for label,editor in editors.items()});window.destroy()

            except (ValueError,KeyError,TypeError) as error: note.set(str(error))

        ttk.Button(window,text='Apply validated change',command=apply).grid(row=2,column=0)

        return window



    def _selected(self):

        self.require_branch()

        return next(c for c in self.branch['draft']['codes'] if c['id']==self.choice.get())



    def open_roles(self):

        code=self._selected(); expected_sha256=self.branch['branch_sha256']
        window=tk.Toplevel(self);window.title('Exact evidence role assignments');window.geometry('950x650')

        rows=[(sid,entry) for sid,entry in self.branch['draft']['registry'].items() if entry['unit_id']==code['unit_id']]

        canvas=tk.Canvas(window);canvas.pack(side='left',fill='both',expand=True)

        scrollbar=ttk.Scrollbar(window,orient='vertical',command=canvas.yview);scrollbar.pack(side='right',fill='y');canvas.configure(yscrollcommand=scrollbar.set)

        body=ttk.Frame(canvas);canvas.create_window((0,0),window=body,anchor='nw');body.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))

        variables={};current={sid:'support' for sid in code['support_span_ids']}

        for role in ('qualifications','counterexamples'):

            for sid,entry in rows:

                if entry in code[role]:current[sid]=role

        for sid,entry in rows:

            ttk.Label(body,text=sid+' | '+entry['text'],wraplength=800).pack(anchor='w',padx=8,pady=4)

            var=tk.StringVar(window,value=current.get(sid,'unused'));variables[sid]=var

            ttk.Combobox(body,textvariable=var,values=('unused','support','qualifications','counterexamples'),state='readonly').pack(anchor='w')

        note=tk.StringVar(window,value='Support remains required. Roles must be disjoint, bounded and from this exact target turn.')

        ttk.Label(body,textvariable=note,wraplength=800).pack()

        def apply():

            try:

                self.apply_roles(code['id'],{r:[sid for sid,v in variables.items() if v.get()==r] for r in ('support','qualifications','counterexamples')},expected_sha256=expected_sha256);window.destroy()

            except ValueError as error:note.set(str(error))

        ttk.Button(body,text='Apply evidence roles',command=apply).pack()

        return window



    def open_split(self):
        code=self._selected(); expected_sha256=self.branch['branch_sha256']
        rows=[(sid,entry) for sid,entry in self.branch['draft']['registry'].items() if entry['unit_id']==code['unit_id']]
        window=tk.Toplevel(self);window.title('Split with exact source evidence');window.geometry('950x700')
        notebook=ttk.Notebook(window);notebook.pack(fill='both',expand=True)
        entries=[]
        for index in (1,2):
            tab=ttk.Frame(notebook);notebook.add(tab,text='Child '+str(index));tab.columnconfigure(1,weight=1);tab.rowconfigure(3,weight=1)
            editors={}
            for row,field in enumerate(('label','claim','definition')):
                ttk.Label(tab,text=field.title()).grid(row=row,column=0)
                widget=tk.Text(tab,height=3,wrap='word');widget.grid(row=row,column=1,sticky='ew');editors[field]=widget
            evidence=ttk.Treeview(tab,columns=('text',),show='headings',selectmode='extended');evidence.heading('text',text='Select exact supporting sentences (Ctrl/Shift)');evidence.column('text',width=800)
            evidence.grid(row=3,column=0,columnspan=2,sticky='nsew')
            for sid,entry in rows:evidence.insert('','end',iid=sid,values=(entry['text'],))
            # No automatic evidence inheritance: each new child needs explicit support selection.
            entries.append((editors,evidence))
        notice=tk.StringVar(window,value='Both children need explicit wording and exact support selection. The original parent stays visible; children start unreviewed.')
        ttk.Label(window,textvariable=notice,wraplength=900).pack()
        def submit():
            try:
                parts=[dict(**{field:widget.get('1.0','end-1c') for field,widget in editors.items()},support_span_ids=list(evidence.selection())) for editors,evidence in entries]
                self.apply_split(code['id'],parts,expected_sha256=expected_sha256);window.destroy()
            except (ValueError,KeyError,TypeError) as error:notice.set(str(error))
        ttk.Button(window,text='Create two unreviewed children',command=submit).pack()
        return window

    def open_group(self):

        self.require_branch()

        from .codebook_review import candidate_review_state

        expected_sha256=self.branch['branch_sha256']
        groups=[(field,g) for field in ('categories','reviewed_categories','interpretive_themes') for g in self.branch['parent'].get(field,[])]

        if not groups:raise ValueError('No historical groups in this branch.')

        window=tk.Toplevel(self);window.title('Re-review a historical relationship');window.geometry('950x750')

        window.columnconfigure(0,weight=1);window.rowconfigure(2,weight=1)

        ttk.Label(window,text='Choose a historical relationship. Members must be selected explicitly; split children are not substituted automatically.').grid(row=0,column=0,sticky='w')

        choice=ttk.Combobox(window,state='readonly',values=[g.get('label',g['id'])+' ['+field+':'+g['id']+']' for field,g in groups])

        choice.grid(row=1,column=0,sticky='ew');choice.current(0)

        members=ttk.Treeview(window,columns=('label','decision','claim'),show='headings',selectmode='extended')

        for key,width in (('label',220),('decision',130),('claim',530)):members.heading(key,text=key.title());members.column(key,width=width)

        members.grid(row=2,column=0,sticky='nsew')

        for code in self.branch['draft']['codes']:

            state=candidate_review_state(self.branch['draft'],code['id'])['state']

            members.insert('', 'end',iid=code['id'],values=(code['label'],state,code['claim']))

        detail=tk.Text(window,height=8,wrap='word',state='disabled');detail.grid(row=3,column=0,sticky='ew')

        def show(_event=None):

            text=[]

            for cid in members.selection():

                text.append(json.dumps(source_review_view(self.branch['draft'],cid),ensure_ascii=False,indent=2))

            detail.configure(state='normal');detail.delete('1.0','end');detail.insert('1.0','\n'.join(text));detail.configure(state='disabled')

        members.bind('<<TreeviewSelect>>',show)

        notice=tk.StringVar(window,value='Select 2–6 members using Ctrl/Shift. Accept/edit each candidate first in the branch window. Exact source appears below the list.')

        ttk.Label(window,textvariable=notice,wraplength=900).grid(row=4,column=0)

        def describe():

            try:

                ReviewBranchDialog.require_snapshot(self, expected_sha256)
                fields=group_form_fields(self.branch,list(members.selection()))

                collection,group=groups[choice.current()]

                selected=list(members.selection())

                def submit(values):

                    self.apply_group(collection,group['id'],dict(code_ids=selected,

                        label=values['Relationship label'],shared_meaning=values['Shared meaning'],limits=values['Limits'],

                        member_notes={cid:values['Member '+str(index+1)+' rationale'] for index,cid in enumerate(selected)}),expected_sha256=expected_sha256)

                    window.destroy()

                self._form('Describe the relationship and each selected contribution',fields,submit)

            except ValueError as error:notice.set(str(error))

        ttk.Button(window,text='Describe selected relationship',command=describe).grid(row=5,column=0)

        return window





def group_form_fields(branch, code_ids):

    """Validated selection model shared by widget callback and offline tests."""

    from .codebook_review import candidate_review_state

    codes={c['id']:c for c in branch['draft']['codes']}

    if not 2 <= len(code_ids) <= 6 or len(set(code_ids)) != len(code_ids) or any(cid not in codes for cid in code_ids):

        raise ValueError('Select two to six distinct known candidates.')

    if any(candidate_review_state(branch['draft'],cid)['state'] not in ('accept','edit') for cid in code_ids):

        raise ValueError('Every selected candidate needs a current accept/edit decision; split parents cannot be used.')

    fields=[('Relationship label',''),('Shared meaning',''),('Limits','')]

    for index,cid in enumerate(code_ids):

        fields.append(('Member '+str(index+1)+' rationale',''))

    return fields



def close_review_branches(owner):
    """Ask before destroying nested review branches from any owning window."""
    for child in tuple(owner.winfo_children()):
        if isinstance(child, ReviewBranchDialog):
            try:
                if not child.request_close(): return False
            except (ValueError, KeyError, TypeError, OSError) as error:
                child.notice.set(str(error))
                return False
        elif not close_review_branches(child):
            return False
    return True
