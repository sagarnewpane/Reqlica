const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../backend/reqlica/reqlicav1.html'), 'utf8');
const section = (start,end) => html.slice(html.indexOf(start),html.indexOf(end));
function fixture(status='needs_information') {
  const elements = new Map();
  const $ = id => {
    if(!elements.has(id))elements.set(id,{value:'',hidden:true,innerHTML:'',textContent:'',focus(){},remove(){}});
    return elements.get(id);
  };
  const provider = {id:'groq',kind:'groq',model:'openai/gpt-oss-20b',baseUrl:'https://api.groq.com/openai/v1'};
  $('generation-provider').value=$('endpoint-ai-provider').value=provider.id;
  $('ai-endpoint-prompt').value='Check credentials using the database';
  const workspace = {description:'Check credentials using the database',generationResult:null,endpointPrompts:{}};
  const result = {status,summary:'No users table. <img src=x onerror=alert(1)>',assumptions:['Which <hash> scheme?'],context_revision:'a'.repeat(64)};
  const errors=[],calls=[];
  const context = {
    crypto: require('node:crypto').webcrypto,
    $,state:{project:{id:'project',active_version:'version'},drafts:[]},
    aiConfig:{providers:[provider],defaults:{temperature:0.2,maxTokens:8192}},
    providerPresets:{groq:{url:provider.baseUrl}},providerKeys:new Map(),
    aiWorkspace:()=>workspace,generationRequirements:()=>workspace.description,
    activeClarification:()=>null,
    aiEndpointContext:{projectId:'project',endpoint:{method:'POST',path:'/login'},revision:'a'.repeat(64)},
    root:()=>'/api/projects/project',confirmAction:async()=>true,
    api:async(...args)=>{
      calls.push(args);
      const [service,url,method,body]=args;
      if(url.endsWith('/generation-jobs'))return {jobs:[]};
      if(url.includes('/generation-jobs/'))return {
        id:'b'.repeat(32),status:method==='PUT'?'ready':'completed',
        completed_files:[],total_files:0,summary:result.summary,
        provider:{provider:provider.kind,model:provider.model,base_url:provider.baseUrl},
        can_resume:method==='PUT',result:method==='PUT'?null:result,
      };
      return result;
    },
    clearError(){},showError:error=>errors.push(error),toast(){},
    renderDraftList(){throw Error('Clarification must not render a new draft');},
    buildAIBrief(){throw Error('Not a draft');},
    renderAIWorkspace:()=>{$('generation-result').innerHTML=context.needsInformationMarkup(workspace.generationResult);},
  };
  vm.createContext(context);
  vm.runInContext(html.match(/^const esc =.*$/m)[0],context);
  vm.runInContext(section('function needsInformationMarkup(','function renderAIWorkspace('),context);
  vm.runInContext(section('function renderGenerationJobs(','async function askProjectWithGroq('),context);
  vm.runInContext(section('async function generateEndpointEdit(','const actions='),context);
  return {context,$,workspace,result,errors,calls};
}

test('endpoint clarification preserves input, escapes model text, and has no review-draft action', async()=>{
  const f=fixture();
  await f.context.generateEndpointEdit();
  assert.deepEqual(f.errors,[]);
  assert.equal(f.calls.length,1);
  assert.equal(f.$('ai-endpoint-prompt').value,'Check credentials using the database');
  assert.equal(f.workspace.endpointPrompts['POST /login'],'Check credentials using the database');
  assert.equal(f.context.state.drafts.length,0);
  assert.equal(f.$('ai-endpoint-result').hidden,false);
  assert.match(f.$('ai-endpoint-result').innerHTML,/No implementation returned/);
  assert.doesNotMatch(f.$('ai-endpoint-result').innerHTML,/add the missing details/);
  assert.match(f.$('ai-endpoint-result').innerHTML,/&lt;img/);
  assert.match(f.$('ai-endpoint-result').innerHTML,/Which &lt;hash&gt; scheme/);
  assert.doesNotMatch(f.$('ai-endpoint-result').innerHTML,/<img|review-endpoint-edit/);
});

test('API generation also handles a clarification without accessing a draft ID', async()=>{
  const f=fixture();
  await f.context.generateWithGroq();
  assert.deepEqual(f.errors,[]);
  assert.equal(f.calls.length,3);
  assert.equal(f.calls[0][2],'PUT');
  assert.match(f.calls[1][1],/\/step$/);
  assert.equal(f.workspace.description,'Check credentials using the database');
  assert.equal(f.workspace.generationResult.status,'needs_information');
  assert.match(f.$('generation-result').innerHTML,/no draft created/);
  assert.equal(f.context.state.drafts.length,0);
});


test('compatible profile sends its selected base URL and custom model', async()=>{
  const f=fixture();
  f.context.aiConfig.providers[0].kind='compatible';
  f.context.aiConfig.providers[0].model='custom-model';
  f.context.aiConfig.providers[0].baseUrl='https://provider.example/v1';
  f.context.aiConfig.providers[0].reasoning='off';
  await f.context.generateWithGroq();
  assert.deepEqual(f.errors,[]);
  assert.equal(f.calls.length,3);
  assert.equal(f.calls[0][3].provider,'compatible');
  assert.equal(f.calls[0][3].base_url,'https://provider.example/v1');
  assert.equal(f.calls[0][3].model,'custom-model');
  assert.equal(f.calls[0][3].reasoning_mode,'off');
});

test('requirements review uses the saved output token budget for API and endpoint flows', async()=>{
  for(const scope of ['generate','endpoint']) {
    for(const budget of [1024,32768]) {
      const f=fixture('ready');
      f.result.questions=[];
      f.context.aiConfig.defaults.maxTokens=budget;
      f.context.clarificationSnapshot=()=>({});
      f.context.renderAIWorkspace=()=>{};
      f.context.clarificationMarkup=()=>'';
      f.context.syncControls=()=>{};
      await f.context.prepareGeneration(scope);
      assert.deepEqual(f.errors,[]);
      assert.equal(f.calls.length,1);
      assert.equal(f.calls[0][0],'console');
      assert.equal(f.calls[0][1],'/api/projects/project/clarify');
      assert.equal(f.calls[0][3].max_tokens,budget);
    }
  }
});

test('resuming an exhausted job grants one batch and never creates a new job', async()=>{
  const f=fixture(),job={id:'c'.repeat(32),status:'exhausted',summary:'Saved work',completed_files:['models/user.py'],total_files:2,can_resume:true,timeout_seconds:100,provider:{provider:'groq',model:'openai/gpt-oss-20b',base_url:'https://api.groq.com/openai/v1'}};
  f.context.api=async(...args)=>{
    f.calls.push(args);
    if(args[1].endsWith('/generation-jobs'))return {jobs:[]};
    if(args[1].endsWith('/resume'))return {...job,status:'ready'};
    if(args[1].endsWith('/step'))return {...job,status:'completed',result:f.result};
    return job;
  };
  await f.context.resumeGeneration(job.id);
  assert.deepEqual(f.errors,[]);
  assert.equal(f.calls.filter(call=>call[2]==='PUT').length,0);
  assert.equal(f.calls.filter(call=>call[1].endsWith('/resume')).length,1);
  const step=f.calls.find(call=>call[1].endsWith('/step'));
  assert.equal(step[4]['X-Reqlica-AI-Timeout-Seconds'],'100');
});

test('network failure leaves a saved job visible and does not automatically retry', async()=>{
  const f=fixture(),job={id:'d'.repeat(32),status:'ready',summary:'Saved <source>',completed_files:['models/user.py'],total_files:2,can_resume:true,provider:{model:'test'}};
  f.context.api=async()=>{throw Error('Connection lost');};
  await assert.rejects(f.context.continueGeneration(job,f.context.aiConfig.providers[0]),/Connection lost/);
  assert.equal(f.workspace.generationJobs[0].id,job.id);
  assert.match(f.$('generation-jobs').innerHTML,/Resume generation/);
  assert.match(f.$('generation-jobs').innerHTML,/Saved &lt;source&gt;/);
});

test('switching projects during a step never attaches its result to another project', async()=>{
  const f=fixture(),job={id:'e'.repeat(32),status:'ready',summary:'Saved work',completed_files:[],total_files:1,can_resume:true,provider:{model:'test'}};
  f.context.api=async()=>{f.context.state.project={id:'other'};return {...job,status:'completed',result:f.result};};
  await f.context.continueGeneration(job,f.context.aiConfig.providers[0]);
  assert.equal(f.workspace.generationResult,null);
});
