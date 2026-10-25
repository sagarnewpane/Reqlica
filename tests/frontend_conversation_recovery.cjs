const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
const projectId = 'a'.repeat(32), runId = 'b'.repeat(32);
const run = {id:runId, message:'Hello', status:'completed', can_reply:true, can_resume:false, turn:0, revision:'c'.repeat(64), events:[]};
const options = {projectId, provider:'groq', model:'test', baseUrl:'', reasoningMode:'default', edits:true, execution:false, preferences:{maxTokens:8192, timeoutSeconds:300}, draft:null};
const deferred = () => {let resolve, reject; const promise = new Promise((a,b)=>{resolve=a;reject=b}); return {promise,resolve,reject};};
const flush = () => new Promise(setImmediate);
function harness(request, storage = new Map()) {
  let slots=[], index=0, effects=[], result;
  class WorkspaceError extends Error {constructor(message,status){super(message);this.status=status}}
  const react = {
    useCallback(callback,deps) {const i=index++;const old=slots[i];if(!old || deps.some((d,j)=>d!==old.deps[j]))slots[i]={deps,value:callback};return slots[i].value;},
    useState(initial) {const i=index++; if (!(i in slots)) slots[i]=initial; return [slots[i], value=>{slots[i]=typeof value==='function'?value(slots[i]):value}];},
    useRef(initial) {const i=index++; if (!(i in slots)) slots[i]={current:initial}; return slots[i];},
    useEffect(callback,deps) {const i=index++; const old=slots[i]; if (!old || deps.some((d,j)=>d!==old.deps[j])) {effects.push(()=>{old?.cleanup?.();slots[i]={deps,callback,cleanup:callback()}});}},
  };
  function load(file, require) {const exports={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,{exports,require,AbortController,Date,sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},setInterval:()=>0,clearInterval(){},setTimeout:()=>0,clearTimeout(){}});return exports;}
  const session = load('frontend/src/lib/conversation-session.ts');
  const {useConversation} = load('frontend/src/components/chat/use-conversation.ts',name=>{
    if(name==='react') return react;
    if(name==='@/lib/chat') return {workspaceRequest:request, WorkspaceError};
    if(name==='@/lib/ids') return {newRequestId:()=>runId};
    if(name==='@/lib/conversation-session') return session;
    throw Error(name);
  });
  const render=()=>{index=0;result=useConversation(options);const jobs=effects;effects=[];jobs.forEach(fn=>fn());return result;};
  return {render,storage,WorkspaceError, unmount(){slots.forEach(slot=>slot?.cleanup?.());}, replay(){slots.forEach(slot=>{if(slot?.callback){slot.cleanup?.();slot.cleanup=slot.callback();}});}};
}
test('selection and unsent text survive remount, including explicit new conversation',async()=>{
 const storage=new Map();const request=async()=>({runs:[run]});const first=harness(request,storage);first.render();await flush();let state=first.render();assert.equal(state.run.id,runId);state.setRun(null);state.setMessage('unfinished requirements');first.unmount();
 const second=harness(request,storage);second.render();await flush();state=second.render();assert.equal(state.run,null);assert.equal(state.message,'unfinished requirements');second.unmount();
});
test('lost acknowledgement reuses exact mutation after remount',async()=>{
 const storage=new Map();let saved=false;const bodies=[];
 const request=async(_project,path,method,body)=>{if(path==='agent-runs')return {runs:saved?[run]:[]};if(method==='PUT'){bodies.push(JSON.stringify(body));saved=true;if(bodies.length===1)throw Error('lost acknowledgement');return run;}return run;};
 const first=harness(request,storage);first.render();await flush();first.render().setMessage('Hello');await first.render().send();assert.equal(first.render().hasPending,true);first.unmount();
 const second=harness(request,storage);second.render();await flush();assert.equal(second.render().hasPending,true);await second.render().send();assert.equal(second.render().hasPending,false);assert.equal(bodies.length,2);assert.equal(bodies[0],bodies[1]);second.unmount();
});
test('newer composer text is retained when an older send completes',async()=>{
 const ack=deferred();const app=harness(async(_p,path,method)=>method==='PUT'?ack.promise:({runs:[]}));app.render();await flush();app.render().setMessage('first');const sending=app.render().send();app.render().setMessage('next request');ack.resolve(run);await sending;assert.equal(app.render().message,'next request');app.unmount();
});
test('effect replay stops late model responses from replacing recovered state or advancing tools',async()=>{
 const step=deferred();let steps=0;const ready={...run,status:'ready',can_resume:true,can_reply:false};
 const app=harness(async(_p,path,method)=>{if(path==='agent-runs')return {runs:[run]};if(path.endsWith('/step')){steps++;return step.promise;}if(method==='POST')return ready;return run;});
 app.render();await flush();app.render().setMessage('follow up');const sending=app.render().send();await flush();app.replay();await flush();step.resolve(ready);await sending;const state=app.render();assert.equal(state.run.status,'completed');assert.equal(state.busy,false);assert.equal(steps,1);app.unmount();
});
test('a selected conversation outside the recent list remains selected on refresh',async()=>{
 const app=harness(async(_p,path)=>path==='agent-runs'?{runs:[]}:run);app.render();await flush();app.render().setRun(run);await app.render().reload();assert.equal(app.render().run.id,runId);app.unmount();
});

test('a busy response keeps the unconfirmed mutation available for an identical retry',async()=>{
 let puts=0;const app=harness(async(_p,path,method)=>{if(path==='agent-runs')return{runs:[]};if(method==='PUT'){puts++;throw new app.WorkspaceError('busy',409);}throw Error('not yet saved');});
 app.render();await flush();app.render().setMessage('Hello');await app.render().send();assert.equal(app.render().hasPending,true);await app.render().send();assert.equal(puts,2);app.unmount();
});
