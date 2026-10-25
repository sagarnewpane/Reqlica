const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
const id = 'a'.repeat(32), draftId = 'b'.repeat(32), revision='c'.repeat(64);
const draft = {id:draftId, revision, name:'Draft', files:['endpoints/a.py'], changes:{}, checks:{}};
const flush = () => new Promise(setImmediate);
function component(file, props, request, exposeWorkspace=false) {
 let slots=[],index=0,effects=[];
 const react={
  useState(initial){const i=index++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v}];},
  useRef(initial){const i=index++;if(!(i in slots))slots[i]={current:initial};return slots[i];},
  useEffect(callback,deps){const i=index++;const old=slots[i];if(!old||deps.some((v,j)=>v!==old.deps[j]))effects.push(()=>{old?.cleanup?.();slots[i]={deps,callback,cleanup:callback()}});},
 };
 const jsx=(type,props,key)=>({type,props:props||{},key});
 const imports = name=>{
  if(name==='react')return react;
  if(name==='react/jsx-runtime')return{jsx,jsxs:jsx,Fragment:'Fragment'};
  if(name==='@/lib/chat')return{workspaceRequest:request};
  if(name==='@radix-ui/react-tabs')return{Root:'Tabs.Root',Content:'Tabs.Content',List:'Tabs.List',Trigger:'Tabs.Trigger'};
  if(name==='@/hooks/use-unsaved-draft-navigation')return{useUnsavedDraftNavigation(){}};
  if(name==='@/lib/draft-files')return{draftFileRoute:()=>`drafts/${draftId}/files`};
  return new Proxy({__esModule:true,default:name},{get:(target,key)=>key in target?target[key]:key});
 };
 const exports={};let source=fs.readFileSync(file,'utf8');if(exposeWorkspace)source+='\nexport {Workspace};';
 vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,{exports,require:imports,Promise,window:{confirm:()=>true},TextEncoder});
 const fn=exposeWorkspace?exports.Workspace:exports.default;
 return{render(){index=0;const tree=fn(props);const jobs=effects;effects=[];jobs.forEach(f=>f());return tree;},replay(){slots.forEach(s=>{if(s?.callback){s.cleanup?.();s.cleanup=s.callback();}});},unmount(){slots.forEach(s=>s?.cleanup?.());}};
}
function all(tree){if(!tree||typeof tree!=='object')return[];const children=tree.props?.children;return[tree,...(Array.isArray(children)?children:[children]).flatMap(all)];}
function node(tree,type){return all(tree).find(n=>n.type===type);}
test('project metadata refresh failure retains the mounted chat',async()=>{
 let fail=false;const app=component('frontend/src/pages/projects/[projectId].tsx',{projectId:id},async()=>{if(fail)throw Error('offline');return{id,name:'Project'};},true);
 app.render();await flush();assert.ok(node(app.render(),'@/components/chat/ProjectChat'));
 fail=true;app.replay();await flush();const tree=app.render();assert.ok(node(tree,'@/components/chat/ProjectChat'));assert.ok(all(tree).some(n=>n.props.role==='alert'));app.unmount();
});
test('draft refresh and effect replay keep editor mounted while metadata is pending',async()=>{
 let hold=false;const app=component('frontend/src/components/drafts/DraftWorkspace.tsx',{projectId:id,initialDraftId:draftId},async(_p,path)=>hold?new Promise(()=>{}):path==='drafts'?{drafts:[draft]}:draft);
 app.render();await flush();let tree=app.render();assert.ok(node(tree,'@/components/drafts/DraftSourceEditor') || node(tree,'./DraftSourceEditor'));
 hold=true;app.replay();await flush();tree=app.render();assert.ok(node(tree,'./DraftSourceEditor'));assert.equal(node(tree,'Tabs.Root').key,draftId);app.unmount();
});
test('Fast Refresh does not reload initial file over unsaved text or selected file',async()=>{
 let reads=0;const app=component('frontend/src/components/drafts/DraftSourceEditor.tsx',{projectId:id,draft,disabled:false,onStateChange(){},onSaved(){}},async()=>{reads++;return{path:'endpoints/a.py',content:'saved',revision};});
 app.render();await flush();let tree=app.render();const editor=all(tree).find(n=>typeof n.props.onChange==='function' && n.props.value==='saved');assert.ok(editor);editor.props.onChange({target:{value:'unsaved correction'}});app.render();app.replay();await flush();tree=app.render();assert.ok(all(tree).some(n=>n.props.value==='unsaved correction'));assert.equal(reads,1);app.unmount();
});
