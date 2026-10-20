const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../backend/reqlica/reqlicav1.html'),'utf8');
function fixture(){
  const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'default',textContent:'',disabled:false,options:[]});return nodes.get(id);};
  for(const id of ['provider-kind','provider-base-url','provider-model'])$(id).value={ 'provider-kind':'compatible','provider-base-url':'https://openrouter.ai/api/v1','provider-model':'nvidia/test'}[id];
  const c={$,state:{busy:false},providerReasoningVersion:0,providerReasoningLoading:false};
  vm.createContext(c);vm.runInContext(html.slice(html.indexOf('function resetProviderReasoning('),html.indexOf('function saveProvider(')),c);
  return {c,$};
}
test('optional models expose Off and mandatory models exclude it',async()=>{
  const {c,$}=fixture();let sent;
  c.api=async(...args)=>{sent=args;return {modes:['default','off'],message:'Optional reasoning'};};
  await c.loadProviderReasoning('off');
  assert.match($('provider-reasoning').innerHTML,/value="off"/);
  assert.equal($('provider-reasoning').value,'off');
  assert.equal(sent[1],'/api/ai/reasoning-options');assert.equal(sent[3].model,'nvidia/test');assert.equal(sent.length,4);
  c.api=async()=>({modes:['default','low','max'],message:'Reasoning is required.'});
  await c.loadProviderReasoning('off');
  assert.doesNotMatch($('provider-reasoning').innerHTML,/value="off"/);
  assert.equal($('provider-reasoning').value,'default');
  assert.match($('provider-reasoning-help').textContent,/saved choice is unavailable/);
});
test('failed or stale model lookups cannot enable an unsupported reasoning choice',async()=>{
  const {c,$}=fixture();let finish;
  c.api=()=>new Promise(resolve=>{finish=resolve;});
  const pending=c.loadProviderReasoning('off');c.resetProviderReasoning();
  finish({modes:['default','off'],message:'Old model'});await pending;
  assert.doesNotMatch($('provider-reasoning').innerHTML,/value="off"/);
  c.api=async()=>{throw Error('Catalog unavailable');};
  await c.loadProviderReasoning('off');
  assert.match($('provider-reasoning-help').textContent,/Support check failed/);
  assert.doesNotMatch($('provider-reasoning').innerHTML,/value="off"/);
});
test('provider validation preserves reasoning but ignores unrelated credential fields',()=>{
  const c={URL,providerPresets:{compatible:{}}};vm.createContext(c);
  vm.runInContext(html.slice(html.indexOf('function validateProvider('),html.indexOf('function loadAIConfig(')),c);
  const p=c.validateProvider({id:'p',name:'Test',kind:'compatible',baseUrl:'https://openrouter.ai/api/v1',model:'nvidia/test',reasoning:'off',key:'private-key'});
  assert.equal(p.reasoning,'off');assert.equal(p.key,undefined);
  assert.equal(c.validateProvider({...p,reasoning:'unknown'}).reasoning,'default');
});
