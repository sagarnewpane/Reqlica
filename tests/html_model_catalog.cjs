const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../backend/reqlica/reqlicav1.html'),'utf8');
function fixture(){
  const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',disabled:false,options:[]});return nodes.get(id);};
  $('provider-kind').value='groq';$('provider-base-url').value='https://api.groq.com/openai/v1';$('provider-api-key').value='private-key';$('provider-model').value='saved-model';
  const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const c={$,esc,state:{busy:false},providerModelsVersion:0,providerModelsLoading:false,providerReasoningVersion:0,providerReasoningLoading:false};
  vm.createContext(c);vm.runInContext(html.slice(html.indexOf('function resetProviderModels('),html.indexOf('function saveProvider(')),c);
  return {c,$};
}
test('discovery sends keys only in headers and keeps the saved model selection',async()=>{
  const {c,$}=fixture();let sent;
  c.api=async(...args)=>{sent=args;return {models:[{id:'new-model',name:'New'},{id:'saved-model',name:'Saved'}],message:'Provider list'};};
  await c.loadProviderModels();
  assert.equal(sent[1],'/api/ai/models');assert.equal(sent[3].provider,'groq');
  assert.equal(sent[3].model,undefined);assert.equal(JSON.stringify(sent[3]).includes('private-key'),false);
  assert.equal(sent[4]['X-Reqlica-Provider-Key'],'private-key');
  assert.equal($('provider-model').value,'saved-model');assert.equal($('provider-model-list').value,'saved-model');
  assert.equal($('provider-model-list').disabled,false);assert.match($('provider-models-help').textContent,/2 models returned/);
  assert.equal(c.providerModelsLoading,false);
});
test('catalog content is escaped and does not automatically replace a manual model',async()=>{
  const {c,$}=fixture();
  c.api=async()=>({models:[{id:'model"><script>',name:'<script>bad</script>'}],message:'List'});
  await c.loadProviderModels();
  assert.doesNotMatch($('provider-model-list').innerHTML,/<script>/);
  assert.match($('provider-model-list').innerHTML,/&lt;script&gt;/);
  assert.equal($('provider-model').value,'saved-model');
  assert.match($('provider-models-help').textContent,/entered model is not in this list/);
});
test('empty or failed catalogs keep manual entry available and allow retry',async()=>{
  const {c,$}=fixture();
  c.api=async()=>({models:[],message:'No models'});await c.loadProviderModels();
  assert.equal($('provider-model-list').disabled,true);assert.equal($('provider-model').value,'saved-model');
  assert.match($('provider-models-help').textContent,/Enter an identifier manually/);
  c.api=async()=>{throw Error('Denied');};await c.loadProviderModels();
  assert.equal($('load-provider-models').disabled,false);assert.equal($('provider-model').value,'saved-model');
  assert.match($('provider-models-help').textContent,/Denied/);
});
test('changing provider, URL, key, or closing invalidates a pending catalog',async()=>{
  const {c,$}=fixture();let finish;
  c.api=()=>new Promise(resolve=>{finish=resolve;});
  const pending=c.loadProviderModels();
  $('provider-kind').value='ollama';c.resetProviderModels();
  finish({models:[{id:'old-provider-model'}],message:'Old'});await pending;
  assert.doesNotMatch($('provider-model-list').innerHTML,/old-provider-model/);
  assert.equal($('provider-model-list').disabled,true);
  assert.equal($('load-provider-models').textContent,'Fetch models');
});
test('Ollama catalog choice checks reasoning without trying inference',async()=>{
  const {c,$}=fixture();let calls=[];
  $('provider-kind').value='ollama';$('provider-api-key').value='';$('provider-model-list').value='installed-model:latest';
  c.api=async(...args)=>{calls.push(args);return {modes:['default'],message:'Provider default available'};};
  c.chooseProviderModel();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal($('provider-model').value,'installed-model:latest');assert.equal(calls.length,1);
  assert.equal(calls[0][1],'/api/ai/reasoning-options');
  assert.equal(calls[0][3].provider,'ollama');
  assert.match($('provider-reasoning-help').textContent,/Provider default available/);
});
