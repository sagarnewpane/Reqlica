const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
function load(file, extra={}) {
  const exports={}; const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  vm.runInNewContext(code,{exports, URL, AbortSignal, process:{env:{}}, ...extra}); return exports;
}
test('model discovery forwards session key only through the dedicated header', async () => {
  const handler=load('frontend/src/pages/api/ai/models.ts',{fetch:async(url,init)=>{
    assert.equal(url.pathname,'/api/ai/models'); assert.equal(init.headers['X-Reqlica-Provider-Key'],'test-key');
    assert.deepEqual(JSON.parse(init.body),{provider:'ollama',base_url:null});
    return {ok:true,status:200,json:async()=>({models:[{id:'local-model',name:'local-model'}]})};
  }}).default;
  let status,body; const res={setHeader(){},status(value){status=value;return this;},json(value){body=value;return this;}};
  await handler({method:'POST',body:{provider:'ollama'},headers:{host:'localhost:3000','content-type':'application/json','x-reqlica-provider-key':'test-key'}},res);
  assert.equal(status,200); assert.equal(body.models[0].id,'local-model');
});
test('request headers are project scoped, copied, and reject injection and managed headers',()=>{
  const s=load('frontend/src/lib/request-headers.ts');
  const rows=[{name:'Authorization',value:'test-only',enabled:true}];
  s.setRequestHeaders('one',rows); rows[0].value='changed';
  assert.equal(s.getRequestHeaders('one')[0].value,'test-only'); assert.equal(s.getRequestHeaders('two').length,0);
  for(const name of ['Host','Cookie','Sec-Fetch-Site']) assert.throws(()=>s.setRequestHeaders('one',[{name,value:'x',enabled:true}]));
  assert.throws(()=>s.setRequestHeaders('one',[{name:'X-Test',value:'x\r\nInjected: true',enabled:true}]));
  assert.throws(()=>s.setRequestHeaders('one',[{name:'X-Test',value:'a',enabled:true},{name:'x-test',value:'b',enabled:true}]));
});
test('request proxy rejects project traversal before contacting runtime',async()=>{
  const helpers=load('frontend/src/lib/request-headers.ts');
  const handler=load('frontend/src/pages/api/projects/[projectId]/request.ts',{require:()=>helpers,fetch:()=>assert.fail('unexpected fetch')}).default;
  let status;const res={setHeader(){},status(code){status=code;return this;},json(){return this;}};
  await handler({method:'POST',query:{projectId:'a'.repeat(32)},headers:{host:'localhost:3000','content-type':'application/json'},body:{method:'GET',path:'/../../api/projects',headers:[]}},res);
  assert.equal(status,400);
});
test('request tester forwards enabled headers to the selected project without redirects',async()=>{
  const helpers=load('frontend/src/lib/request-headers.ts'); const id='a'.repeat(32);
  const handler=load('frontend/src/pages/api/projects/[projectId]/request.ts',{Buffer,require:()=>helpers,fetch:async(url,init)=>{
    assert.equal(url.pathname,`/mock/${id}/hello`); assert.equal(init.headers['X-Test'],'enabled'); assert.equal(init.headers['X-Off'],undefined); assert.equal(init.redirect,'manual');
    return {status:204,body:null,headers:new Map([['x-response','ok']])};
  }}).default;
  let status,body;const res={setHeader(){},status(code){status=code;return this;},json(value){body=value;return this;}};
  await handler({method:'POST',query:{projectId:id},headers:{host:'localhost:3000','content-type':'application/json'},body:{method:'GET',path:'/hello',headers:[{name:'X-Test',value:'enabled',enabled:true},{name:'X-Off',value:'disabled',enabled:false}]}},res);
  assert.equal(status,200); assert.equal(body.status,204); assert.equal(body.headers['x-response'],'ok');
});
