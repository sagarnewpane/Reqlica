const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'../backend/reqlica/reqlicav1.html'),'utf8');
function fixture(){
 const nodes=new Map(),storage=new Map();
 const c={TextEncoder,structuredClone,URL,Map,Set,state:{project:{id:'one',name:'One'}},$:id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:''});return nodes.get(id);},pretty:JSON.stringify,errorMessage:e=>e.message,localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},confirmAction:async()=>true};
 vm.createContext(c);
 for(const [a,b] of [['function validateHeaders(','async function sendRequest('],['function resolveChainValue(','function fillTableExample('],['function validateProvider(','function renderProviderProfiles(']])vm.runInContext(html.slice(html.indexOf(a),html.indexOf(b)),c);
 return c;
}
test('saved headers survive reload, isolate projects, and override case-insensitively',()=>{
 const c=fixture();c.$('shared-headers').value='{"X-Tenant":"one","Authorization":"old"}';c.$('shared-jwt').value='Bearer jwt';c.saveSharedHeaders();
 c.$('shared-jwt').value='';c.loadSharedHeaders();assert.equal(c.$('shared-jwt').value,'jwt');
 c.$('request-headers').value='{"AUTHORIZATION":"Bearer override"}';assert.equal(c.parseHeaders().authorization,'Bearer override');assert.equal(c.parseHeaders()['x-tenant'],'one');
 c.state.project.id='two';assert.equal(Object.keys(c.savedSharedHeaders()).length,0);
 assert.throws(()=>c.validateHeaders({'X-Reqlica-UI':'evil'}));assert.throws(()=>c.validateHeaders({Authorization:'bad\r\nvalue'}));
});
test('provider key survives profile reload and clearing removes stored key',()=>{
 const c=fixture();Object.assign(c,{aiStorageKey:'ai',aiDefaultOptions:{},aiStorageAvailable:true,aiWorkspaces:new Map(),providerKeys:new Map([['p','secret']]),providerPresets:{groq:{}},renderProviderProfiles:()=>{},aiConfig:{providers:[{id:'p',name:'P',kind:'groq',model:'m',baseUrl:'https://example.com'}],defaultProvider:'p',defaults:{}}});
 c.persistAIConfig();c.providerKeys.clear();const loaded=c.loadAIConfig();assert.equal(c.providerKeys.get('p'),'secret');assert.equal(loaded.providers[0].apiKey,undefined);
 c.providerKeys.clear();c.persistAIConfig();c.loadAIConfig();assert.equal(c.providerKeys.size,0);
});
test('table examples omit generated IDs and hidden columns, preserve foreign keys',()=>{
 const c=fixture(),r=c.tableExample({columns:[{name:'id',type:'INTEGER',pk:1},{name:'user_id',type:'INTEGER'},{name:'title',type:'TEXT'},{name:'generated',hidden:2}]});assert.equal(JSON.stringify(r),'{"user_id":1,"title":"example"}');
});
test('chain references preserve types and reject missing or prototype fields',()=>{
 const c=fixture(),r={user:{id:42,name:'a/b'}};
 assert.equal(c.resolveChainValue('{{user.id}}',r),42);
 assert.equal(c.resolveChainValue('/users/{{user.name}}',r,true),'/users/a%2Fb');
 assert.throws(()=>c.resolveChainValue('{{user.missing}}',r));assert.throws(()=>c.resolveChainValue('{{user.__proto__}}',r));
 assert.throws(()=>c.parseRequestChain('[{"name":"a","method":"POST","path":"https://bad"}]'));
});
test('chain forwards returned ID and shared JWT, stops at HTTP failure',async()=>{
 const c=fixture(),calls=[];c.$('shared-headers').value='{}';c.$('shared-jwt').value='jwt';c.saveSharedHeaders();
 c.$('request-chain').value=JSON.stringify([{name:'user',method:'POST',path:'/users',body:{name:'A'}},{name:'post',method:'POST',path:'/posts',body:{user_id:'{{user.id}}'}},{name:'skip',method:'GET',path:'/posts'}]);
 c.transport=async(service,path,options)=>{calls.push({path,options});return calls.length===1?{response:{ok:true,status:201},body:{id:42}}:{response:{ok:false,status:422},body:{detail:'failed'}};};
 await c.runRequestChain();assert.equal(calls.length,2);assert.equal(JSON.parse(calls[1].options.body).user_id,42);assert.equal(calls[1].options.headers.authorization,'Bearer jwt');assert.match(c.$('request-chain-results').textContent,/422/);
});
test('chain stops before sending unresolved references and never retries transport errors',async()=>{
 const c=fixture();let calls=0;c.$('request-chain').value='[{"name":"post","method":"POST","path":"/posts","body":{"user_id":"{{missing.id}}"}}]';c.transport=async()=>{calls++;throw Error('timeout');};
 await c.runRequestChain();assert.equal(calls,0);assert.match(c.$('request-chain-results').textContent,/Missing chain/);
 c.$('request-chain').value='[{"name":"post","method":"POST","path":"/posts"}]';await c.runRequestChain();assert.equal(calls,1);assert.match(c.$('request-chain-results').textContent,/timeout/);
});
