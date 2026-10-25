const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');const ts=require('../frontend/node_modules/typescript');
async function call(route,method,headers={}){
 const exports={},requests=[];vm.runInNewContext(ts.transpileModule(fs.readFileSync('frontend/src/pages/api/ai/[...path].ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,{exports,URL,AbortSignal,process:{env:{}},fetch:async(url,init)=>{requests.push({url,init});return {ok:true,status:200,json:async()=>({configured:true})};}});
 const result={};await exports.default({method,query:{path:route.split('/')},headers:{host:'localhost:3000','content-type':'application/json',...headers},body:{provider:'groq',api_key:'fixture-key',project_id:'must-not-forward'}},{setHeader(){},status(code){result.status=code;return this;},json(body){result.body=body;return this;}});return {...result,requests};
}
test('app key routes are independent of project IDs and discard unrelated fields',async()=>{
 for(const [route,method] of [['key','PUT'],['key','DELETE'],['key/status','POST'],['key/reveal','POST']]){const result=await call(route,method);assert.equal(result.status,200);assert.equal(result.requests[0].url.href,`http://127.0.0.1:3001/api/ai/${route}`);const payload=JSON.parse(result.requests[0].init.body);assert.equal(payload.project_id,undefined);assert.equal(payload.api_key,method==='PUT'?'fixture-key':undefined);assert.equal(result.requests[0].init.redirect,'error');}
});
test('app credentials reject cross-origin requests and unknown routes',async()=>{
 const denied=await call('key','PUT',{origin:'https://other.example'});assert.equal(denied.status,403);assert.equal(denied.requests.length,0);
 assert.equal((await call('key','GET')).status,405);assert.equal((await call('profiles/../key','POST')).status,405);
});
