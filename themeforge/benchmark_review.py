"""Standalone, local-only human review interface for benchmark pilot packets."""

from __future__ import annotations

import html
import json
import re


def render_review_html(packet: dict) -> str:
    """Render a packet without network resources or implicit review decisions.

    Packet content is inert JSON; the interface inserts all untrusted strings via
    textContent. Decisions remain local until the reviewer downloads an export.
    """
    if packet.get("format_version") != 1 or not isinstance(packet.get("packet_id"), str) or not packet["packet_id"]:
        raise ValueError("Review packet requires format_version 1 and a packet_id")
    ids = [item["id"] for item in packet.get("items", [])]
    if any(not isinstance(key, str) or not key for key in ids) or len(set(ids)) != len(ids):
        raise ValueError("Review item ids must be nonempty and unique")
    payload = json.dumps(packet, ensure_ascii=True, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    tokens = {"__TITLE__": html.escape(str(packet.get("title", "Pilot benchmark review"))), "__PACKET_JSON__": payload}
    return re.sub(r"__TITLE__|__PACKET_JSON__", lambda match: tokens[match.group()], _PAGE)


_PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; base-uri 'none'; form-action 'none'">
<title>__TITLE__</title><style>
:root{font-family:system-ui,sans-serif;color:#172839;background:#f1f5f7;line-height:1.55}*{box-sizing:border-box}body{margin:0}header,main{max-width:1180px;margin:auto;padding:24px}header{padding-bottom:8px}h1{margin:8px 0}h2{font-size:1.3rem}h3{font-size:1rem}p{margin:8px 0}.badge{display:inline-block;background:#d9f1e6;color:#174a35;border-radius:20px;padding:3px 12px;font-weight:650}.toolbar,.filters{display:flex;gap:12px;flex-wrap:wrap;align-items:end;margin:16px 0}.filters label,.toolbar label{flex:1;min-width:150px}label{display:block;font-weight:600}input,select,textarea,button{font:inherit}input:not([type=checkbox]),select,textarea{display:block;width:100%;padding:9px;border:1px solid #81929e;border-radius:5px;background:white;color:#172839}textarea{min-height:100px;resize:vertical}button{cursor:pointer;border:1px solid #5a7484;background:white;color:#173e57;border-radius:6px;padding:9px 14px}button.primary{background:#155b7d;color:white}button:disabled{opacity:.5;cursor:default}:focus-visible{outline:3px solid #db8b14;outline-offset:3px}.layout{display:grid;grid-template-columns:290px minmax(0,1fr);gap:20px}nav{max-height:75vh;overflow:auto}nav button{display:block;width:100%;text-align:left;margin-bottom:8px}nav button[aria-current=true]{border:2px solid #155b7d;background:#e1eff6}.card{background:white;padding:24px;border-radius:10px;border:1px solid #d3dde3}.muted{color:#526573}.evidence{border-left:4px solid #62a2be;padding:4px 16px;margin:18px 0;background:#f4f8fa;overflow-wrap:anywhere}blockquote{margin:8px 0;white-space:pre-wrap}.context,.proposed{white-space:pre-wrap;overflow-wrap:anywhere}.field{margin:16px 0}.status{min-height:28px;color:#174a35;white-space:pre-wrap}.error{color:#942d22}.counts{font-weight:650}.priority{color:#963c16;font-weight:700}details{margin:10px 0}summary{cursor:pointer}.hidden{display:none!important}@media(max-width:760px){header,main{padding:16px}.layout{grid-template-columns:1fr}nav{max-height:220px}.card{padding:18px}}
</style></head><body><header><span class="badge">Local only · offline review</span><h1 id="title"></h1><p>No data is sent anywhere. Proposed values are drafts until you explicitly save a decision.</p>
<details><summary>Review instructions and packet summary</summary><div id="instructions"></div><pre id="summary" class="context"></pre><div id="studies"></div></details>
<div class="toolbar"><label>Reviewer ID (required to save)<input id="reviewer" autocomplete="off" maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,63}" placeholder="Pseudonymous ID, e.g. reviewer-1"></label><button id="export">Export decisions JSON</button><label>Import decisions JSON<input id="import" type="file" accept="application/json,.json"></label></div>
<p id="storage" class="muted"></p><p id="message" class="status" role="status" aria-live="polite"></p><p id="counts" class="counts" aria-live="polite"></p>
<div class="filters"><label>Search<input id="search" type="search" placeholder="Search evidence, labels, source…"></label><label>Study<select id="study"><option value="">All studies</option></select></label><label>Kind<select id="kind"><option value="">All kinds</option></select></label><label>Status<select id="state"><option value="">All statuses</option><option value="pending">Pending</option><option value="accept">Saved</option><option value="defer">Deferred</option></select></label><label><input id="high" type="checkbox"> High priority only</label></div>
<details><summary>Browse read-only label inventory</summary><label>Search label inventory<input id="catalog-search" type="search" placeholder="Search labels, related labels, and notes"></label><p id="catalog-count"></p><div id="catalog" style="max-height:400px;overflow:auto"></div></details>
<button id="next" class="primary">Next pending item</button></header><main class="layout"><nav id="items" aria-label="Review items"></nav><section id="detail" class="card" aria-label="Selected review item"></section></main>
<script type="application/json" id="packet">__PACKET_JSON__</script>
<script>
'use strict';
const packet=JSON.parse(document.getElementById('packet').textContent);
const $=id=>document.getElementById(id), items=packet.items||[], itemMap=new Map(items.map(i=>[i.id,i]));
const storageKey='themeforge-review-v1:'+packet.packet_id;
const reviewerPattern=/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
let decisions=new Map(), selected=items.length?items[0].id:null, storageAvailable=true;
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.className=cls;return n;}
function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('error',error);}
function exportData(){return {format_version:1,packet_id:packet.packet_id,reviewer_id:$('reviewer').value.trim(),decisions:Array.from(decisions.values())};}
function validateImport(data){
 if(!data||data.format_version!==1||data.packet_id!==packet.packet_id)throw Error('Packet ID or format version does not match.');
 if(typeof data.reviewer_id!=='string'||!reviewerPattern.test(data.reviewer_id))throw Error('Reviewer ID must use 1-64 letters, digits, dots, underscores or hyphens and start with a letter or digit.');
 if(!Array.isArray(data.decisions))throw Error('Decisions must be an array.');
 const seen=new Set(), clean=[];
 for(const d of data.decisions){
  if(!d||!itemMap.has(d.item_id)||seen.has(d.item_id))throw Error('Unknown or duplicate item ID in decisions.');
  seen.add(d.item_id);
  if(!['accept','defer'].includes(d.action)||typeof d.reviewer_id!=='string'||!reviewerPattern.test(d.reviewer_id)||!d.values||typeof d.values!=='object'||Array.isArray(d.values)||typeof d.note!=='string'||typeof d.reviewed_at!=='string'||!Number.isFinite(Date.parse(d.reviewed_at)))throw Error('Invalid decision record or missing per-decision reviewer ID.');
  const fields=itemMap.get(d.item_id).fields||[], allowed=new Map(fields.map(f=>[f.key,f]));
  for(const [key,value] of Object.entries(d.values)){
   const f=allowed.get(key);if(!f)throw Error('Unknown field in decision.');
   if(f.type==='checkbox'?typeof value!=='boolean':typeof value!=='string')throw Error('Invalid field value type.');
   if(f.type==='select'&&!(f.options||[]).some(o=>o.value===value))throw Error('Unknown select value.');
  }
  if(d.action==='accept')for(const f of fields){const v=d.values[f.key];if(v===undefined||(f.required&&(f.type==='checkbox'?!v:!v.trim())))throw Error('Saved decision has missing required fields.');}
  clean.push({item_id:d.item_id,action:d.action,reviewer_id:d.reviewer_id,values:d.values,note:d.note,reviewed_at:d.reviewed_at});
 }
 return {reviewer_id:data.reviewer_id.trim(),decisions:clean};
}
function persist(){
 try{localStorage.setItem(storageKey,JSON.stringify(exportData()));}
 catch(e){storageAvailable=false;}
 $('storage').textContent=storageAvailable?'Progress is stored in this browser for this packet. Export JSON for a portable backup.':'Browser storage is unavailable. Progress lasts only while this page stays open. Export JSON to keep it.';
}
function filtered(){const q=$('search').value.trim().toLowerCase();return items.filter(i=>(!$('study').value||i.study_id===$('study').value)&&(!$('kind').value||i.kind===$('kind').value)&&(!$('high').checked||i.priority==='high')&&(!$('state').value||((decisions.get(i.id)||{}).action||'pending')===$('state').value)&&(!q||JSON.stringify(i).toLowerCase().includes(q)));}
function renderList(){
 const shown=filtered();$('items').replaceChildren();
 for(const i of shown){const state=(decisions.get(i.id)||{}).action||'pending';const b=node('button',(i.priority==='high'?'High · ':'')+i.title+' · '+(state==='accept'?'saved':state));b.type='button';b.setAttribute('aria-current',String(selected===i.id));b.onclick=()=>{selected=i.id;render();};$('items').append(b);}
 if(!shown.length)$('items').append(node('p','No matching items.'));
 const saved=Array.from(decisions.values()).filter(d=>d.action==='accept').length,deferred=decisions.size-saved;
 $('counts').textContent=`${items.length} total · ${items.length-decisions.size} remaining pending · ${saved} saved · ${deferred} deferred · ${shown.length} shown`;
 $('next').disabled=!shown.some(i=>!decisions.has(i.id));
}
function renderDetail(){
 const host=$('detail');host.replaceChildren();const i=itemMap.get(selected);if(!i){host.append(node('p','No review items in this packet.'));return;}
 const decision=decisions.get(i.id);
 host.append(node('p',i.study_name+' · '+i.kind,'muted'),node('h2',i.title),node('p',i.prompt));
 if(i.priority==='high')host.append(node('p','High priority','priority'));
 host.append(node('p','Status: '+(decision?(decision.action==='accept'?'saved decision':'deferred'):'pending')+(decision?' · Reviewer: '+decision.reviewer_id:'')));
 for(const e of i.evidence||[]){const box=node('div',undefined,'evidence'),quote=node('blockquote',e.text||'');quote.style.maxHeight='320px';quote.style.overflow='auto';quote.tabIndex=0;box.append(node('h3',e.label||'Evidence'),quote,node('p','Source: '+(e.source||'Not specified'),'muted'));if(e.detail){const detail=node('details');detail.append(node('summary','Source context and details'),node('p',e.detail,'context'));box.append(detail);}host.append(box);}
 if(i.binding&&i.binding.kind==='annotation'&&Array.isArray(packet.documents)&&packet.documents.length){
  const context=node('details');context.append(node('summary','Full transcript context'));const span=i.source_span,documentSource=span&&packet.documents.find(document=>document.id===span.document_id);
  if(!documentSource||typeof documentSource.text!=='string')context.append(node('p','Full transcript unavailable: the source document is missing.','error'));
  else{
   context.append(node('p','Source: '+documentSource.name,'context'),node('p','Extracted text SHA-256: '+(documentSource.text_sha256||'Not specified'),'context'));
   if(documentSource.source_sha256)context.append(node('p','Source file SHA-256: '+documentSource.source_sha256,'context'));
   const text=node('div',undefined,'context');text.style.maxHeight='500px';text.style.overflow='auto';text.style.padding='12px';text.style.background='#f4f8fa';text.tabIndex=0;
   const characters=Array.from(documentSource.text),start=span.source_start,end=span.source_end;
   if(!Number.isInteger(start)||!Number.isInteger(end)||start<0||end<=start||end>characters.length){context.append(node('p','Cannot highlight target: the source range is missing or invalid. Full transcript is shown without a highlight.','error'));text.textContent=documentSource.text;}
   else{const target=node('mark',characters.slice(start,end).join(''));text.append(document.createTextNode(characters.slice(0,start).join('')),target,document.createTextNode(characters.slice(end).join('')));context.addEventListener('toggle',()=>{if(context.open)target.scrollIntoView({block:'center',inline:'nearest'});});}
   context.append(text);
  }
  host.append(context);
 }
 if(i.proposed){const proposed=node('details');proposed.append(node('summary','Proposed draft (unreviewed)'),node('p',i.proposed,'proposed'));host.append(proposed);}
 const form=node('form');const inputs=new Map();
 for(const [index,f] of (i.fields||[]).entries()){
  const wrap=node('div',undefined,'field');let fieldLabel=f.label,reference=null;
  if(i.binding&&i.binding.kind==='annotation'&&f.key.startsWith('code_')){
   const code=items.find(candidate=>candidate.binding&&candidate.binding.kind==='code_definition'&&candidate.binding.code_id===f.key.slice(5));
   if(code){const saved=decisions.get(code.id),reviewed=saved&&saved.action==='accept',values=reviewed?saved.values:Object.fromEntries((code.fields||[]).map(field=>[field.key,field.default]));
    if(values.name)fieldLabel=values.name;
    reference=node('details');reference.append(node('summary','Code reference · '+(reviewed?'saved definition':'unreviewed proposal')));
    for(const [key,title] of [['name','Name'],['definition','Definition'],['inclusion_criteria','Include'],['exclusion_criteria','Exclude']])reference.append(node('p',title+': '+(values[key]||'Not specified'),'context'));
   }
  }
  const label=node('label',fieldLabel+(f.required?' (required)':''));const input=node(f.type==='textarea'?'textarea':f.type==='select'?'select':'input');
  input.id='field-'+index;label.htmlFor=input.id;
  if(f.type==='select'){for(const o of f.options||[]){const opt=node('option',o.label);opt.value=o.value;input.append(opt);}}
  else if(f.type!=='textarea')input.type=f.type==='checkbox'?'checkbox':'text';
  const value=decision&&Object.hasOwn(decision.values,f.key)?decision.values[f.key]:f.default;
  if(f.type==='checkbox')input.checked=value===true;else if(value!==undefined)input.value=String(value);
  input.required=!!f.required;inputs.set(f.key,{input,field:f});wrap.append(label,input);if(reference)wrap.append(reference);form.append(wrap);
 }
 const noteLabel=node('label','Reviewer note'),note=node('textarea');note.id='note';noteLabel.htmlFor='note';note.value=decision?decision.note:'';form.append(noteLabel,note);
 const actions=node('div',undefined,'toolbar'),save=node('button','Save decision','primary'),defer=node('button','Defer item'),undo=node('button','Undo to pending');save.type='submit';defer.type=undo.type='button';undo.disabled=!decision;
 function record(action){const reviewer=$('reviewer').value.trim();if(!reviewerPattern.test(reviewer)){message('Enter a reviewer ID with 1-64 letters, digits, dots, underscores or hyphens, starting with a letter or digit.',true);$('reviewer').focus();return;}
  const values=Object.fromEntries(Array.from(inputs,([key,{input,field}])=>[key,field.type==='checkbox'?input.checked:input.value]));
  decisions.set(i.id,{item_id:i.id,action,reviewer_id:reviewer,values,note:note.value,reviewed_at:new Date().toISOString()});persist();render();message(action==='accept'?'Decision saved. Select Next pending item to continue.':'Item deferred.');
 }
 form.onsubmit=e=>{e.preventDefault();if(form.reportValidity())record('accept');};defer.onclick=()=>record('defer');undo.onclick=()=>{decisions.delete(i.id);persist();render();message('Decision removed. Item is pending.');};actions.append(save,defer,undo);form.append(actions);host.append(form);
}
function render(){renderList();renderDetail();}
$('title').textContent=packet.title||'Pilot benchmark review';
for(const text of packet.instructions||[])$('instructions').append(node('p',text));
$('summary').textContent=JSON.stringify(packet.summary||{},null,2);
for(const s of packet.studies||[])$('studies').append(node('p',s.name+': '+(typeof s.selection==='string'?s.selection:JSON.stringify(s.selection))));
function renderCatalog(){const q=$('catalog-search').value.trim().toLowerCase(),catalog=(packet.catalog||[]).filter(entry=>!q||JSON.stringify(entry).toLowerCase().includes(q));$('catalog').replaceChildren();$('catalog-count').textContent=`${catalog.length} of ${(packet.catalog||[]).length} labels · read-only inventory`;
 for(const entry of catalog){const section=node('div',undefined,'evidence');section.append(node('h3',entry.label),node('p',entry.study_name+' · '+entry.label_id+' · '+entry.occurrences+' occurrences','muted'));if(entry.related_labels&&entry.related_labels.length)section.append(node('p','Related labels: '+entry.related_labels.join(', ')));if(entry.note)section.append(node('p',entry.note,'context'));$('catalog').append(section);}}
$('catalog-search').oninput=renderCatalog;renderCatalog();
for(const [id,key] of [['study','study_id'],['kind','kind']]){const seen=new Set();for(const i of items){if(!seen.has(i[key])){seen.add(i[key]);const o=node('option',id==='study'?i.study_name:i.kind);o.value=i[key];$(id).append(o);}}}
for(const id of ['search','study','kind','state','high'])$(id).addEventListener(id==='search'?'input':'change',renderList);
$('reviewer').oninput=persist;
$('next').onclick=()=>{const visible=filtered(),at=visible.findIndex(i=>i.id===selected);for(let n=1;n<=visible.length;n++){const i=visible[(at+n)%visible.length];if(!decisions.has(i.id)){selected=i.id;render();$('detail').scrollIntoView({block:'start'});$('detail').setAttribute('tabindex','-1');$('detail').focus();break;}}};
$('export').onclick=()=>{if(!reviewerPattern.test($('reviewer').value.trim())){message('Enter a valid pseudonymous reviewer ID before exporting.',true);$('reviewer').focus();return;}const blob=new Blob([JSON.stringify(exportData(),null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=node('a');a.href=url;a.download='review-decisions.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);message(`Exported ${decisions.size} explicit decisions.`);};
$('import').onchange=async()=>{const file=$('import').files[0];if(!file)return;try{const imported=validateImport(JSON.parse(await file.text()));if(decisions.size&&!confirm('Replace current local decisions with this imported file? Export first if you need a backup.'))return;decisions=new Map(imported.decisions.map(d=>[d.item_id,d]));$('reviewer').value=imported.reviewer_id;persist();render();message(`Imported ${decisions.size} explicit decisions.`);}catch(e){message('Import rejected: '+e.message,true);}finally{$('import').value='';}};
try{const raw=localStorage.getItem(storageKey);if(raw){const data=JSON.parse(raw);if(Array.isArray(data.decisions)&&data.decisions.length===0&&data.packet_id===packet.packet_id&&data.format_version===1){$('reviewer').value=typeof data.reviewer_id==='string'?data.reviewer_id:'';}else{const restored=validateImport(data);decisions=new Map(restored.decisions.map(d=>[d.item_id,d]));$('reviewer').value=restored.reviewer_id;}}}catch(e){storageAvailable=false;message('Stored progress could not be restored. Import an exported JSON backup if available.',true);}
persist();render();
</script></body></html>'''
