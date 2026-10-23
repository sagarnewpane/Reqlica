const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../backend/reqlica/reqlicav1.html'),'utf8');
function fixture(){
  const elements=new Map(), calls=[], confirmations=[], workspace={};
  const $=id=>{if(!elements.has(id))elements.set(id,{value:'',innerHTML:'',hidden:false});return elements.get(id);};
  const current={id:'a'.repeat(32),revision:'b'.repeat(64),name:'JWT signup',base_version:'v',created_at:'2026-10-19T12:00:00+00:00',updated_at:'2026-10-20T12:00:00+00:00'};
  const other={...current,id:'c'.repeat(32),name:'Other draft',updated_at:'2026-10-19T12:00:00+00:00'};
  const context={$,state:{draft:current,drafts:[other,current],project:{id:'p',active_version:'v'},dirty:true,editorPath:'endpoints/auth.py',editorRevision:current.revision},Date,
    esc:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    badge:value=>value,root:()=>'/api/projects/p',draftRoot:()=>'/api/projects/p/drafts/'+context.state.draft.id,
    confirmAction:async(...args)=>{confirmations.push(args);return true;},api:async(...args)=>{calls.push(args);return {status:'deleted'};},
    aiWorkspace:()=>workspace,renderDraft(){},renderRequestRoutes(){},syncControls(){},toast(){},updateDraft:value=>{context.state.draft=value;}
  };
  $('file-content').value='unsaved source';
  vm.createContext(context);
  vm.runInContext(html.slice(html.indexOf('function draftProgress('),html.indexOf('function updateDraft(')),context);
  return {context,$,calls,confirmations,workspace,current,other};
}
test('list leads with escaped names, local timestamps, latest first and deletion controls',()=>{
  const f=fixture();f.current.name='<img src=x onerror=alert(1)>';
  f.context.renderDraftList();const rendered=f.$('draft-list').innerHTML;
  assert.ok(rendered.includes('&lt;img'));
  assert.ok(!rendered.includes('<img'));
  assert.ok(rendered.indexOf(f.current.id)<rendered.indexOf(f.other.id));
  assert.match(rendered,/Created/);assert.match(rendered,/Updated/);assert.match(rendered,/data-delete-draft/);
  assert.equal(f.context.draftTime(null),'Not recorded');assert.equal(f.context.draftTime('invalid'),'Not recorded');
});
test('cancel deletion keeps unsaved source and makes no request',async()=>{
  const f=fixture();f.context.confirmAction=async()=>false;
  await f.context.deleteDraft(f.current.id);
  assert.equal(f.calls.length,0);assert.equal(f.context.state.drafts.length,2);assert.equal(f.$('file-content').value,'unsaved source');
});
test('failed or stale deletion preserves selected draft and editor',async()=>{
  const f=fixture();f.context.api=async()=>{throw Error('409 conflict');};
  await assert.rejects(()=>f.context.deleteDraft(f.current.id),/409/);
  assert.equal(f.context.state.draft.id,f.current.id);assert.equal(f.context.state.drafts.length,2);assert.equal(f.$('file-content').value,'unsaved source');
});
test('deleting selected draft pins revision and clears its references only after success',async()=>{
  const f=fixture();f.workspace.agentDraft=f.current.id;
  await f.context.deleteDraft(f.current.id);
  assert.equal(f.calls[0][2],'DELETE');assert.equal(f.calls[0][3].expected_revision,f.current.revision);assert.equal(f.calls[0][3].confirm,true);
  assert.match(f.confirmations[0][1],/Unsaved/);assert.match(f.confirmations[0][1],/Published versions/);
  assert.equal(f.context.state.draft,null);assert.equal(f.context.state.dirty,false);assert.equal(f.$('file-content').value,'');assert.equal(f.workspace.agentDraft,'');
  assert.equal(f.context.state.drafts[0].id,f.other.id);
});
test('deleting another draft preserves selected unsaved source',async()=>{
  const f=fixture();await f.context.deleteDraft(f.other.id);
  assert.equal(f.context.state.draft.id,f.current.id);assert.equal(f.context.state.dirty,true);assert.equal(f.$('file-content').value,'unsaved source');
});
test('rename preserves editor content and sends a revision-pinned metadata patch',async()=>{
  const f=fixture();f.$('draft-name').value='  Login flow  ';f.context.api=async(...args)=>{f.calls.push(args);return {...f.current,name:args[3].name};};
  await f.context.saveDraftName();assert.equal(f.calls[0][2],'PATCH');assert.equal(f.calls[0][3].name,'Login flow');assert.equal(f.calls[0][3].expected_revision,f.current.revision);
  assert.equal(f.$('file-content').value,'unsaved source');assert.equal(f.context.state.dirty,true);
});
