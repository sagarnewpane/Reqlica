const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../backend/reqlica/reqlicav1.html'),'utf8');
function fixture(){
  const nodes=new Map(),timers=new Map(),calls=[];let next=0;
  const node=()=>({dataset:{},textContent:'',innerHTML:'',children:new Map(),setAttribute(){},remove(){nodes.delete('ai-request-activity');},insertAdjacentElement(where,child){nodes.set(child.id,child);},querySelector(selector){if(!this.children.has(selector))this.children.set(selector,node());return this.children.get(selector);}});
  const $=id=>nodes.get(id);
  for(const id of ['main-content','agent-error','agent-live'])nodes.set(id,node());
  let resolve,reject;
  const request=new Promise((yes,no)=>{resolve=yes;reject=no;});
  const c={$,Date,aiConfig:{defaults:{timeoutSeconds:300}},document:{createElement:node},crypto:{randomUUID:()=> 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'},
    setTimeout:fn=>{timers.set(++next,fn);return next;},setInterval:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id),clearInterval:id=>timers.delete(id),
    transport:async(service,url,options,timeoutMs)=>{calls.push({service,url,options,timeoutMs});return url.includes('/activity/')?{response:{ok:true},body:{events:[{stage:'received',elapsed_ms:3},{stage:'provider_request',elapsed_ms:5}]}}:request;},
  };
  vm.createContext(c);vm.runInContext(html.match(/^const esc =.*$/m)[0],c);
  vm.runInContext(html.slice(html.indexOf('const aiActivityStages='),html.indexOf('async function run(')),c);
  return {c,$,timers,calls,resolve,reject};
}
test('AI request shows confirmed progress, sends an activity ID, and cleans up timers',async()=>{
  const f=fixture(),pending=f.c.api('console','/api/projects/p/agent-runs/r/step','POST',{requirements:'private'});
  const panel=f.$('ai-request-activity');
  assert.equal(panel.querySelector('[data-activity-title]').textContent,'Sending to Reqlica');
  assert.equal(f.calls[0].options.headers['X-Reqlica-Activity-ID'],'a'.repeat(32));
  assert.equal(f.calls[0].timeoutMs,360000);
  assert.equal(f.calls[0].options.headers['X-Reqlica-AI-Timeout-Seconds'],'300');
  assert.equal(JSON.parse(f.calls[0].options.body).timeout_seconds,300);
  // Fire the polling timeout (the first timer is the elapsed interval).
  const [id,poll]=[...f.timers][1];f.timers.delete(id);await poll();
  assert.equal(panel.querySelector('[data-activity-title]').textContent,'Contacting the model provider');
  assert.equal(f.calls[1].url,'/api/projects/p/activity/'+'a'.repeat(32));
  assert.equal(f.calls[1].timeoutMs,5000);
  assert.doesNotMatch(panel.querySelector('[data-activity-events]').innerHTML,/private/);
  f.resolve({response:{ok:true},body:{status:'ready'}});await pending;
  assert.equal(panel.dataset.state,'complete');assert.equal(f.timers.size,0);
});
test('custom AI timeout reaches backend and bridge; ordinary requests keep their timeout',async()=>{
  for(const [path,seconds,expected] of [['/api/projects/p/agent-runs/r/step',600,660000],['/api/projects/p/agent-runs/r/step',120,180000],['/api/projects/p/agent-runs/r/step',300,360000],['/api/projects/p/drafts',600,90000]]){
    const f=fixture();f.c.aiConfig.defaults.timeoutSeconds=seconds;
    const pending=f.c.api('console',path,'POST',{});
    assert.equal(f.calls[0].timeoutMs,expected);
    if(expected!==90000)assert.equal(JSON.parse(f.calls[0].options.body).timeout_seconds,seconds);
    else assert.equal(f.calls[0].options.headers['X-Reqlica-AI-Timeout-Seconds'],undefined);
    f.resolve({response:{ok:true},body:{}});await pending;
  }
});
test('network failure stays a failure, stops polling and preserves the original error',async()=>{
  const f=fixture(),pending=f.c.api('console','/api/projects/p/agent-runs/r/step','POST',{});
  f.reject(Error('Could not reach console'));
  await assert.rejects(pending,/Could not reach console/);
  assert.equal(f.$('ai-request-activity').dataset.state,'failed');assert.equal(f.timers.size,0);
});
test('activity is scoped to AI requests and to the correct form',()=>{
  const {c}=fixture();
  assert.equal(c.aiActivityTarget('console','/api/projects/p/agent-runs/r/step','POST',{}),'agent-error');
  assert.equal(c.aiActivityTarget('console','/api/projects/p/agent-runs/r/step','POST',{endpoint:{}}),'agent-error');
  assert.equal(c.aiActivityTarget('console','/api/projects/p/agent-runs/r/step','POST',{}),'agent-error');
  assert.equal(c.aiActivityTarget('console','/api/projects/p/drafts','POST',{}),null);
  assert.equal(c.aiActivityTarget('gateway','/api/projects/p/generate','POST',{}),null);
});

test('saved AI settings migrate missing timeouts and accept only valid configured values',()=>{
  for(const [saved,expected] of [[{},300],[{timeoutSeconds:100},100],[{timeoutSeconds:600},600],[{timeoutSeconds:601},300],[{timeoutSeconds:'100'},300]]){
    const c={aiStorageKey:'test',localStorage:{getItem:()=>JSON.stringify({defaults:saved})}};
    vm.createContext(c);
    vm.runInContext(html.match(/^const aiDefaultOptions=.*$/m)[0],c);
    vm.runInContext(html.slice(html.indexOf('function loadAIConfig('),html.indexOf('function persistAIConfig(')),c);
    assert.equal(c.loadAIConfig().defaults.timeoutSeconds,expected);
  }
});

test('AI defaults save valid timeout values and reject values outside the supported range',()=>{
  for(const [value,valid] of [['30',true],['300',true],['600',true],['29',false],['601',false],['30.5',false]]){
    const errors=[],nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',checked:true});return nodes.get(id);};
    $('ai-temperature').value='0';$('ai-max-tokens').value='8192';$('ai-timeout-seconds').value=value;$('default-ai-provider').value='p';
    let saved;
    const c={$,aiConfig:{providers:[{id:'p'}],defaults:{}},aiStorageAvailable:true,clearError(){},showError:e=>errors.push(e),persistAIConfig:()=>{saved={...c.aiConfig.defaults};},renderAIProviderChoices(){},toast(){}};
    vm.createContext(c);vm.runInContext(html.slice(html.indexOf('function saveAIDefaults('),html.indexOf('// Chat persistence:')),c);
    c.saveAIDefaults();
    assert.equal(errors.length,valid?0:1);
    assert.equal(saved?.timeoutSeconds,valid?Number(value):undefined);
  }
});

test('64k output budgets save and reload while larger budgets are rejected',()=>{
  for(const [value,valid] of [[65536,true],[65792,false]]){
    const errors=[],nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:''});return nodes.get(id);};
    $('ai-temperature').value='0';$('ai-max-tokens').value=String(value);$('ai-timeout-seconds').value='300';$('default-ai-provider').value='';
    let saved;
    const c={$,aiConfig:{providers:[],defaults:{}},aiStorageAvailable:true,aiStorageKey:'test',clearError(){},showError:e=>errors.push(e),persistAIConfig:()=>{saved={...c.aiConfig.defaults};},renderAIProviderChoices(){},toast(){},localStorage:{getItem:()=>JSON.stringify({defaults:{maxTokens:value}})}};
    vm.createContext(c);vm.runInContext(html.match(/^const aiDefaultOptions=.*$/m)[0],c);
    vm.runInContext(html.slice(html.indexOf('function loadAIConfig('),html.indexOf('function persistAIConfig(')),c);
    vm.runInContext(html.slice(html.indexOf('function saveAIDefaults('),html.indexOf('// Chat persistence:')),c);
    c.saveAIDefaults();
    assert.equal(errors.length,valid?0:1);
    assert.equal(saved?.maxTokens,valid?value:undefined);
    assert.equal(c.loadAIConfig().defaults.maxTokens,valid?value:8192);
  }
  assert.match(html, /id="ai-max-tokens"[^>]*max="65536"/);
});
