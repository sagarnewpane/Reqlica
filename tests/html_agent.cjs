const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'../backend/reqlica/reqlicav1.html'),'utf8');
const code=html.slice(html.indexOf('const agentToolLabels='),html.indexOf('function renderGenerationJobs('));
function fixture(){
  const nodes=new Map(),workspace={agentMessage:'Build a status endpoint',agentRun:null,agentRuns:[],agentDraft:''};
  const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,scrollTop:0,scrollHeight:0,clientHeight:0,querySelectorAll:()=>[]});return nodes.get(id);};
  const provider={id:'test',kind:'compatible',model:'model',baseUrl:'http://127.0.0.1:9000/v1'};
  const c={$,aiWorkspace:()=>workspace,pretty:JSON.stringify,dataRecordCounts:records=>Array.isArray(records)?{records:records.length}:Object.fromEntries(Object.entries(records || {}).map(([key,value])=>[key,value.length])),state:{project:{id:'a'.repeat(32),active_version:'v'},drafts:[],busy:false},aiConfig:{providers:[provider],defaults:{temperature:0,maxTokens:8192,timeoutSeconds:300}},providerKeys:new Map([['test','secret']]),crypto:{randomUUID:()=> 'a'.repeat(32)},root:()=>'/api/projects/'+c.state.project.id,showError:error=>c.errors.push(error.message),clearError:()=>{},errors:[],loadDrafts:async()=>{const id=workspace.agentRun?.draft_id;if(id)c.state.drafts=[{id,name:'Working draft',base_version:'v'}];},confirmAction:async()=>true,draftName:d=>d.name};
  vm.createContext(c);vm.runInContext(html.match(/^const esc =.*$/m)[0],c);vm.runInContext(code,c);
  $('agent-provider').value='test';$('agent-allow-edits').checked=true;return {c,$,w:workspace,provider};
}
function run(overrides={}){return {id:'b'.repeat(32),status:'ready',message:'Build',model_calls:1,tool_calls:0,limits:{model_calls:24,tool_calls:64},draft_id:'c'.repeat(32),revision:'d'.repeat(64),events:[],checks:{},can_resume:true,provider:{provider:'compatible',model:'model',base_url:'http://127.0.0.1:9000/v1'},timeout_seconds:300,...overrides};}

test('pause keeps the returned checkpoint and stops before the next request',async()=>{
  const {c,w}=fixture();let steps=0;
  c.api=async()=>{steps++;w.agentStop=true;return run({tool_calls:1});};
  await c.continueAgent(run(),c.aiConfig.providers[0]);
  assert.equal(steps,1);assert.equal(w.agentRun.tool_calls,1);assert.equal(w.agentRunning,false);assert.equal(w.agentDraft,'c'.repeat(32));
});

test('a failed step never automatically retries provider requests',async()=>{
  const {c,w}=fixture();let calls=0;c.api=async()=>{calls++;return run({status:'failed',error:{message:'Rate limit'}});};
  await c.continueAgent(run(),c.aiConfig.providers[0]);
  assert.equal(calls,1);assert.equal(w.agentRun.status,'failed');
});

test('browser timeout only reads saved state, never replays an uncertain tool',async()=>{
  const {c,w}=fixture();let calls=[];c.api=async(service,url,method)=>{calls.push(method || 'GET');if(method==='POST')throw Error('timeout');return run({status:'running'});};
  await c.continueAgent(run(),c.aiConfig.providers[0]);
  assert.deepEqual(calls,['POST','GET']);assert.equal(w.agentRun.status,'running');assert.equal(c.errors[0],'timeout');
});

test('rendering escapes model content and uses actual revision-linked checks',()=>{
  const {c,w,$}=fixture();w.agentRun=run({answer:'<img src=x onerror=bad()>',events:[{kind:'tool',name:'write_draft_file',status:'done',path:'<script>bad()</script>'}],checks:{test_draft:{status:'passed',revision:'e'.repeat(64)}}});
  c.renderAgentWorkspace();
  assert.match($('agent-events').innerHTML,/&lt;script&gt;/);assert.doesNotMatch($('agent-events').innerHTML,/<script>/);
  assert.match($('agent-answer').innerHTML,/&lt;img/);assert.doesNotMatch($('agent-answer').innerHTML,/<img/);assert.match($('agent-checks').innerHTML,/failed \/ stale/);
  assert.doesNotMatch($('agent-checks').innerHTML,/Tests: passed/);
});

test('disabled and completed runs expose review without a resume action',()=>{
  const {c,w,$}=fixture();w.agentRun=run({status:'completed',can_resume:false});c.renderAgentWorkspace();
  assert.equal($('agent-resume').hidden,true);assert.equal($('agent-review').hidden,false);assert.equal($('agent-status').textContent,'Finished');
});

test('resuming requires the matching model and destination, never an arbitrary key',()=>{
  const {c}=fixture();assert.equal(c.agentProvider(run()).id,'test');
  assert.equal(c.agentProvider(run({provider:{provider:'compatible',model:'model',base_url:'https://other.example/v1'}})),undefined);
});

test('start is disabled without valid requirements, a supported provider, while legacy projects remain read-only',()=>{
  const {c,w,$}=fixture();w.agentMessage=' ';c.syncAgentControls();assert.equal($('agent-start').disabled,true);
  w.agentMessage='Build';c.syncAgentControls();assert.equal($('agent-start').disabled,false);
  c.state.project.active_version=null;c.renderAgentWorkspace();assert.equal($('agent-start').disabled,false);assert.equal($('agent-allow-edits').checked,false);assert.equal($('agent-allow-edits').disabled,true);
});

test('published draft selections reset to a new draft and old bases are disabled',async()=>{
  const {c,w,$}=fixture();w.agentDraft='old';
  c.state.drafts=[{id:'old',name:'Published draft',base_version:'previous'},{id:'current',name:'Current draft',base_version:'v'}];
  c.renderAgentDraftChoices();
  assert.equal(w.agentDraft,'');assert.equal($('agent-draft').value,'');
  assert.match($('agent-draft').innerHTML,/<option value="old" disabled>/);
  assert.doesNotMatch($('agent-draft').innerHTML,/<option value="current" disabled>/);
  assert.match($('agent-draft-help').textContent,/original draft is kept/);
  $('agent-message').value='Add another endpoint';let submitted;
  c.api=async(service,url,method,body)=>{assert.equal(method,'PUT');submitted=body;return run();};
  c.continueAgent=async()=>{};
  await c.startAgent();assert.equal(submitted.draft_id,undefined);assert.equal(submitted.expected_revision,undefined);
});

test('current draft selection is retained and backend rollover notice is visible',()=>{
  const {c,w,$}=fixture();w.agentDraft='current';c.state.drafts=[{id:'current',name:'Current draft',base_version:'v'}];
  c.renderAgentDraftChoices();assert.equal($('agent-draft').value,'current');
  w.agentRun=run({notice:'The selected draft matches the published API. Changes will use a new draft.'});
  c.renderAgentWorkspace();assert.equal($('agent-notice').hidden,false);assert.match($('agent-notice').textContent,/new draft/);
});

test('AI Markdown formats options and code while treating HTML as text',()=>{
  const {c}=fixture();
  const rendered=c.agentMarkdown('**Concrete question:** what should `GET /books` return?\n\n- **A.** `200` with `[]`\n- **B.** `200` with an object\n\n```json\n{"books": []}\n```\n\n<img src=x onerror=bad()>');
  assert.match(rendered,/<strong>Concrete question:<\/strong>/);
  assert.match(rendered,/<code>GET \/books<\/code>/);
  assert.match(rendered,/<ul><li><strong>A\.<\/strong>/);
  assert.match(rendered,/<pre><code>/);assert.match(rendered,/&lt;img/);assert.doesNotMatch(rendered,/<img/);
  for(const input of ['`<script>bad()</script>`','**<img onerror=bad()>**','<svg onload=bad()>','[bad](javascript:bad())']) {
    const output=c.agentMarkdown(input);assert.doesNotMatch(output,/<script|<img|<svg|<a /);
  }
});

test('the same composer handles replies and preserves per-conversation unsent messages',()=>{
  const {c,w,$}=fixture();w.agentRun=run({status:'completed',can_reply:true});
  w.agentReplyDrafts={[w.agentRun.id]:'Option B'};c.renderAgentWorkspace();
  assert.equal($('agent-message').value,'Option B');assert.equal($('agent-start').textContent,'Send reply →');
  assert.equal($('agent-start').disabled,false);w.agentRunning=true;c.renderAgentWorkspace();
  assert.equal($('agent-message').disabled,true);
});

test('sending a reply advances the same run without creating a new build',async()=>{
  const {c,w,$}=fixture();w.agentRun=run({status:'completed',can_reply:true,turn:2});$('agent-message').value='Option B';
  let submitted,advanced;c.api=async(service,url,method,body)=>{submitted={url,method,body};return run({turn:3});};
  c.continueAgent=async result=>{advanced=result;};
  await c.sendAgentReply();assert.match(submitted.url,/agent-runs\/b+\/replies$/);
  assert.equal(submitted.body.expected_turn,2);assert.equal(submitted.body.expected_revision,'d'.repeat(64));
  assert.equal(submitted.body.message,'Option B');assert.equal(advanced.turn,3);
  assert.equal(w.agentReplyDrafts[w.agentRun.id],'');
});

test('lost reply acknowledgement reads saved state without resubmitting',async()=>{
  const {c,w,$}=fixture();w.agentRun=run({status:'completed',can_reply:true});$('agent-message').value='Option B';
  let calls=[];c.api=async(service,url,method)=>{calls.push(method || 'GET');if(method==='POST')throw Error('timeout');return run({last_reply_id:'a'.repeat(32),turn:1});};
  await c.sendAgentReply();assert.deepEqual(calls,['POST','GET']);assert.equal(w.agentReplyPending,null);
  assert.equal(w.agentRun.turn,1);assert.equal(w.agentReplyDrafts[w.agentRun.id],'');
});

test('tool error explanations are escaped and visible',()=>{
  const {c}=fixture();const markup=c.agentEventMarkup({kind:'tool',name:'get_api_schema',status:'error',detail:'Validate current revision. <script>bad()</script>'});
  assert.match(markup,/Validate current revision/);assert.match(markup,/&lt;script&gt;/);assert.doesNotMatch(markup,/<script>/);
});

test('specifications are validated and bounded together with the user message',async()=>{
  const {c,w,$}=fixture();$('agent-message').value='Build from this spec';w.agentAttachment={name:'api.json',text:'{"paths":{}}'};
  assert.match(c.assistantMessage(),/Build from this spec\n\nAttached specification \(api.json\)/);
  w.agentAttachment.text='{';assert.throws(()=>c.assistantMessage(),/invalid/);
  w.agentAttachment={name:'api.md',text:'x'.repeat(32768)};assert.throws(()=>c.assistantMessage(),/together within/);
  $('assistant-file').files=[{name:'api.json',size:12,text:async()=>'{bad'}];await c.readAssistantFile();assert.match(c.errors.at(-1),/invalid/);
});

test('new conversation keeps saved work and does not auto-select history on refresh',async()=>{
  const {c,w,$}=fixture();w.agentRun=run({status:'completed',can_reply:true});w.agentRuns=[w.agentRun];w.agentDraft=w.agentRun.draft_id;
  assert.equal(await c.newAssistantConversation(),true);assert.equal(w.agentRun,null);assert.equal(w.agentRuns.length,1);assert.equal(w.agentDraft,'');
  assert.equal($('agent-execution').checked,false);assert.equal($('agent-allow-edits').checked,true);
  c.api=async()=>({runs:[run()]});await c.loadAgentRuns();assert.equal(w.agentRun,null);assert.equal(w.agentRuns.length,1);
});

test('tool groups retain previous replies even when the final answer repeats them',()=>{
  const {c}=fixture();const result=c.assistantConversationMarkup(run({message:'Question',answer:'Same reply',events:[
    {kind:'tool',name:'get_project_context',status:'done'}, {kind:'assistant',text:'Same reply'}, {kind:'user',text:'Again'},
    {kind:'tool',name:'get_project_context',status:'done'},
  ]}));
  assert.match(result,/Same reply/);assert.match(result,/<details class="assistant-tools">/);assert.match(result,/YOU|You/);
});

test('opening a data artifact prepares manual review without trusting saved validation',async()=>{
  const {c,w}=fixture();w.agentRun=run({data_preview:{target:'json',records:[{title:'Example'}],validation:{status:'passed'},preview_digest:'a'.repeat(64)}});
  w.dataValidation={validation:{status:'passed'}};w.dataImportResult={inserted_count:2};let view;
  c.setView=value=>{view=value;};c.renderDataWorkspace=()=>{};c.syncControls=()=>{};
  await c.reviewAssistantData();assert.equal(view,'data');assert.equal(w.dataTarget,'json');assert.equal(w.dataValidation,null);assert.equal(w.dataImportResult,null);
  assert.equal(JSON.parse(w.dataRawPreview)[0].title,'Example');
});

test('endpoint and repair entry points prefill a new conversation and respect unsaved source',async()=>{
  const {c,w,$}=fixture();c.setView=()=>{};c.state.drafts=[{id:'selected',name:'Draft',base_version:'v'}];
  assert.equal(await c.openAssistantContext({draftId:'selected',message:'Repair this',checks:true}),true);
  assert.equal(w.agentMessage,'Repair this');assert.equal(w.agentDraft,'selected');assert.equal($('agent-execution').checked,true);
  c.state.dirty=true;assert.equal(await c.openAssistantContext({message:'Replace prompt'}),false);assert.equal(w.agentMessage,'Repair this');assert.match(c.errors.at(-1),/unsaved/);
});

test('manual draft continuation reuses its conversation when revision and permissions match',async()=>{
  const {c,w,$}=fixture();w.agentRun=run({status:'completed',can_reply:true,allow_execution:true});const id=w.agentRun.id;
  c.state.draft={id:w.agentRun.draft_id,revision:w.agentRun.revision,base_version:'v'};c.setView=()=>{};
  await c.openAssistantContext({draftId:c.state.draft.id,message:'Repair this failure',checks:true});
  assert.equal(w.agentRun.id,id);assert.equal($('agent-message').value,'Repair this failure');
  assert.equal(w.agentReplyDrafts[id],'Repair this failure');
});

test('failed attachment reply retains the original text without duplicating the specification',async()=>{
  const {c,w,$}=fixture();w.agentRun=run({status:'completed',can_reply:true});$('agent-message').value='Use this shape';w.agentAttachment={name:'shape.json',text:'{"status":"ready"}'};
  c.api=async()=>{throw Error('offline');};await c.sendAgentReply();c.renderAgentWorkspace();
  assert.equal($('agent-message').value,'Use this shape');
  assert.equal(c.assistantMessage().match(/Attached specification/g).length,1);
});
