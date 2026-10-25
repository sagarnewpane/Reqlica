const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('../frontend/node_modules/typescript');
const exportsObject={};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('frontend/src/lib/request-chains.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:exportsObject});
const {resolveChainStep}=exportsObject;
test('chain substitutes escaped paths and typed JSON without evaluating code',()=>{
 const step={method:'POST',path:'/items/{{create.body.id}}?q={{create.body.label}}',body:'{"id":"{{create.body.id}}","label":"Hello {{create.body.label}}"}'};
 const result=resolveChainStep(step,{create:{body:{id:3,label:'x"/ y'}}});
 assert.equal(result.path,'/items/3?q=x%22%2F%20y');
 assert.deepEqual(JSON.parse(result.body),{id:3,label:'Hello x"/ y'});
});
test('chain rejects missing values and prototype traversal',()=>{
 assert.throws(()=>resolveChainStep({method:'GET',path:'/{{missing.body.id}}',body:''},{}),/Missing chain value/);
 assert.throws(()=>resolveChainStep({method:'GET',path:'/{{create.__proto__.secret}}',body:''},{create:{body:{}}}),/Missing chain value/);
});
