const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../backend/reqlica/reqlicav1.html'),'utf8');
const code=html.slice(html.indexOf('function repairableDraft('),html.indexOf('function renderDatabaseSetup('));
function fixture(statuses=['checks_failed','repaired']) {
  const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,textContent:''});return nodes.get(id);};
  const makeDraft=revision=>({id:'draft',revision,base_version:'base',generation:{requirements:'Original API contract'},test:{status:'failed',revision}});
  const provider={id:'groq',kind:'groq',model:'openai/gpt-oss-120b',baseUrl:'https://api.groq.com/openai/v1'};
  $('repair-provider').value='groq';$('repair-attempts').value='2';
  const calls=[],errors=[],confirmations=[];
  const context={$,state:{project:{id:'project',active_version:'base'},draft:makeDraft('revision-1'),dirty:false},
    aiConfig:{providers:[provider],defaults:{temperature:0.2,maxTokens:8192}},providerPresets:{groq:{url:provider.baseUrl}},providerKeys:new Map([['groq','private-provider-key']]),
    clearError(){},showError:e=>errors.push(e.message),pretty:JSON.stringify,toast(){},
    confirmAction:async(...args)=>{confirmations.push(args);return true;},
    updateDraft:d=>{context.state.draft=d;},refreshDraft:async()=>{},readFile:async()=>{},
    api:async(...args)=>{calls.push(args);const status=statuses[calls.length-1];if(status==='throw')throw Error('Provider failed');return {status,summary:'Diagnosis <b>literal</b>',changed_files:['endpoints/hello.py'],...(status==='needs_information'?{}:{draft:makeDraft('revision-'+(calls.length+1))})};},
  };
  vm.createContext(context);
  vm.runInContext(html.match(/^const esc =.*$/m)[0],context);
  vm.runInContext(html.match(/^function badge\(.*$/m)[0],context);
  vm.runInContext(code,context);context.renderRepair();
  return {context,$,calls,errors,confirmations};
}
test('failed checks run a bounded sequence pinned to each returned revision',async()=>{
  const f=fixture();await f.context.repairFailedDraft();
  assert.equal(f.calls.length,2);
  assert.equal(f.calls[0][3].expected_revision,'revision-1');
  assert.equal(f.calls[1][3].expected_revision,'revision-2');
  assert.equal(f.calls[0][3].requirements,'Original API contract');
  assert.equal(f.calls[0][3].confirm,true);
  assert.equal(f.calls[0][4]['X-Reqlica-Groq-Key'],'private-provider-key');
  assert.equal(f.calls.every(call=>call[1].endsWith('/repair')),true);
  assert.match(f.$('repair-status').textContent,/Checks passed/);
  assert.match(f.$('repair-report').textContent,/<b>literal<\/b>/);
  assert.deepEqual(f.errors,[]);
});
test('attempt limit stops persistent failures and cannot exceed two',async()=>{
  const f=fixture(['checks_failed','checks_failed']);f.$('repair-attempts').value='99';
  await f.context.repairFailedDraft();assert.equal(f.calls.length,2);
  assert.match(f.$('repair-status').textContent,/Attempt limit reached/);
});
test('cancel, unsaved edits, blockers, and provider errors do not retry or publish',async()=>{
  const canceled=fixture();canceled.context.confirmAction=async()=>false;await canceled.context.repairFailedDraft();assert.equal(canceled.calls.length,0);
  const dirty=fixture();dirty.context.state.dirty=true;await dirty.context.repairFailedDraft();assert.equal(dirty.calls.length,0);assert.equal(dirty.confirmations.length,0);
  for(const status of ['needs_information','throw']){const f=fixture([status]);await f.context.repairFailedDraft();assert.equal(f.calls.length,1);assert.match(f.$('repair-status').textContent,/Stopped|stopped/);}
});
