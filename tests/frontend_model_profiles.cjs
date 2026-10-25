const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'); const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
function setup(browser = {}) {
  const data = new Map(); const storage={getItem:key=>data.get(key)||null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
  function load(file,require) { const exports={}; const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText; vm.runInNewContext(code,{exports,require,URL,localStorage:storage,...browser}); return exports; }
  const settings=load('frontend/src/lib/settings.ts'); const profiles=load('frontend/src/lib/model-profiles.ts',()=>settings);
  return {...profiles,data,settings};
}
const a='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa', b='bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb';
function profile(id=a,name='Coding') { return {id,name,provider:'ollama',model:'local-code',baseUrl:'http://localhost:11434',maxTokens:8192,timeoutSeconds:300}; }
test('profiles create, update, duplicate and delete independently',()=>{
  const s=setup(); s.saveModelProfile(profile()); s.saveModelProfile(profile(b,'Coding copy'));
  assert.equal(s.readModelProfiles().length,2);
  s.saveModelProfile({...profile(),model:'better-code'});
  assert.equal(s.readModelProfiles()[0].model,'better-code');
  assert.equal(s.readModelProfiles()[1].model,'local-code');
  s.deleteModelProfile(a); assert.equal(s.readModelProfiles()[0].id,b);
});
test('duplicate names and invalid connections are rejected without replacing profiles',()=>{
  const s=setup(); s.saveModelProfile(profile());
  assert.throws(()=>s.saveModelProfile(profile(b,'coding')));
  assert.throws(()=>s.saveModelProfile({...profile(b,'Other'),baseUrl:'http://user:secret@example.com'}));
  assert.throws(()=>s.saveModelProfile({...profile(b,'Other'),model:''}));
  assert.equal(s.readModelProfiles().length,1);
});
test('profile persistence excludes credentials and does not alter workspace defaults',()=>{
  const s=setup(); s.settings.saveSettings({...s.settings.defaultSettings,model:'workspace-model'});
  s.saveModelProfile({...profile(),apiKey:'secret-never-save',allowExecution:true});
  const saved=s.readModelProfiles()[0]; assert.equal(saved.apiKey,undefined); assert.equal(saved.allowExecution,undefined);
  assert.equal([...s.data.values()].some(value=>value.includes('secret-never-save')),false);
  s.deleteModelProfile(a); assert.equal(s.settings.readSettings().model,'workspace-model');
});
test('corrupt and invalid profile storage falls back safely',()=>{
  const s=setup(); s.data.set('reqlica.model-profiles.v1','{bad'); assert.equal(s.readModelProfiles().length,0);
  s.data.set('reqlica.model-profiles.v1',JSON.stringify([null,{...profile(),model:32},profile()])); assert.equal(s.readModelProfiles().length,1);
});
test('profiles retain reasoning and older profiles default to off',()=>{
  const s=setup();
  s.saveModelProfile({...profile(),reasoningMode:'low'});
  assert.equal(s.readModelProfiles()[0].reasoningMode,'low');
  s.data.set('reqlica.model-profiles.v1',JSON.stringify([profile()]));
  assert.equal(s.readModelProfiles()[0].reasoningMode,'off');
});


test('profile lists notify open project views and unsubscribe cleanly',()=>{
 const listeners=new Map();class Event { constructor(type){this.type=type;} }
 const window={addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,new Set());listeners.get(type).add(fn);},removeEventListener(type,fn){listeners.get(type)?.delete(fn);},dispatchEvent(event){for(const fn of listeners.get(event.type)||[])fn(event);}};
 const s=setup({window,Event});let changed=0;const unsubscribe=s.subscribeModelProfiles(()=>changed++);
 s.saveModelProfile(profile());assert.equal(changed,1);
 window.dispatchEvent({type:'storage',key:'reqlica.model-profiles.v1'});assert.equal(changed,2);
 window.dispatchEvent({type:'storage',key:'unrelated'});assert.equal(changed,2);
 unsubscribe();s.deleteModelProfile(a);assert.equal(changed,2);
});
