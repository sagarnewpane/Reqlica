// Server adapter coverage moved to test_dashboard.py. Browser helper tests remain here.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
function load(file, extra={}) {
  const exports={}; const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  vm.runInNewContext(code,{exports, URL, AbortSignal, process:{env:{}}, ...extra}); return exports;
}
test('request headers are project scoped, copied, and reject injection and managed headers',()=>{
  const s=load('frontend/src/lib/request-headers.ts');
  const rows=[{name:'Authorization',value:'test-only',enabled:true}];
  s.setRequestHeaders('one',rows); rows[0].value='changed';
  assert.equal(s.getRequestHeaders('one')[0].value,'test-only'); assert.equal(s.getRequestHeaders('two').length,0);
  for(const name of ['Host','Cookie','Sec-Fetch-Site']) assert.throws(()=>s.setRequestHeaders('one',[{name,value:'x',enabled:true}]));
  assert.throws(()=>s.setRequestHeaders('one',[{name:'X-Test',value:'x\r\nInjected: true',enabled:true}]));
  assert.throws(()=>s.setRequestHeaders('one',[{name:'X-Test',value:'a',enabled:true},{name:'x-test',value:'b',enabled:true}]));
});
