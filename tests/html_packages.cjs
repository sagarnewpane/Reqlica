const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../backend/reqlica/reqlicav1.html'),'utf8');
function fixture(){
  const nodes=new Map(),calls=[],confirmations=[],timers=[];
  const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,dataset:{},scrollIntoView(){}});return nodes.get(id);};
  const info={project_id:'p',python:'/runtime/bin/python',prefix:'/runtime',revision:'env-hash',installer:{available:true},installed:{core:'1.0'},scan:{issues:[]},jobs:[]};
  const f={packageInfo:info};
  const c={$,state:{view:'settings',project:{id:'p'},draft:null,drafts:[],busy:false},featureState:()=>f,root:()=>'/api/projects/p',
    document:{querySelectorAll:()=>[]},pretty:v=>JSON.stringify(v,null,2),esc:v=>String(v??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x])),
    clearTimeout(){},setTimeout:fn=>{timers.push(fn);return timers.length;},clearError(){},errorMessage:e=>e.message,renderDraft(){},setView:v=>c.state.view=v,
    confirmAction:async(...args)=>{confirmations.push(args);return true;},api:async(...args)=>{calls.push(args);return c.result;}};
  vm.createContext(c);vm.runInContext(html.slice(html.indexOf('// Explicit package resolution'),html.indexOf('const actions={')),c);
  return {c,$,calls,confirmations,timers,f,info};
}
function review(){return {id:'job',package:'Demo',version:null,requirement:'Demo',status:'review',message:'Review',plan_digest:'digest',prepared_at:123,packages:[{name:'demo',version:'1.2',action:'install'},{name:'core',version:'1.0',action:'keep'}]};}
test('package previews reject shell, URL, and range inputs before sending requests',async()=>{
  const {c,$,calls}=fixture();for(const name of ['--upgrade','pkg;rm','https://example.test/a.whl','./pkg']){$('package-name').value=name;await assert.rejects(c.resolvePackage,/distribution name/);}
  $('package-name').value='Demo';$('package-version').value='>=1';await assert.rejects(c.resolvePackage,/distribution name/);assert.equal(calls.length,0);
});
test('resolution pins the environment and never installs before a separate action',async()=>{
  const {c,$,calls,f,timers}=fixture();$('package-name').value='Demo';c.result={...review(),status:'resolving'};await c.resolvePackage();assert.equal(calls.length,1);assert.match(calls[0][1],/packages\/preview$/);assert.equal(calls[0][3].expected_revision,'env-hash');assert.equal(calls[0][4]['X-Reqlica-Package-Action'],'reviewed-ui');assert.equal(f.packageJob.status,'resolving');assert.equal(timers.length,1);
});
test('cancelled install preserves the reviewed plan and makes no request',async()=>{
  const {c,$,calls,confirmations,f}=fixture();f.packageJob=review();$('package-name').value='Demo';c.confirmAction=async(...args)=>{confirmations.push(args);return false;};await c.installReviewedPackage();assert.equal(calls.length,0);assert.equal(f.packageJob.status,'review');assert.match(confirmations[0][1],/demo==1.2/);assert.match(confirmations[0][1],/Target: \/runtime/);assert.match(confirmations[0][1],/shared environment/);
});
test('install pins the saved job digest and invalidates client check badges',async()=>{
  const {c,$,calls,f}=fixture();f.packageJob=review();$('package-name').value='Demo';c.state.draft={id:'draft',validation:{status:'passed'},test:{status:'passed'},approval:{}};c.result={...review(),status:'installing'};await c.installReviewedPackage();assert.equal(calls.length,1);assert.match(calls[0][1],/jobs\/job\/install$/);assert.equal(calls[0][3].expected_plan_digest,'digest');assert.equal(calls[0][3].confirm,true);assert.equal(c.state.draft.validation,null);assert.equal(c.state.draft.approval,null);
});
test('editing the package request invalidates the install control',async()=>{
  const {c,$,calls,f}=fixture();f.packageJob=review();$('package-name').value='Different';await assert.rejects(c.installReviewedPackage,/Resolve/);assert.equal(calls.length,0);c.packageRequestChanged();assert.equal(f.packageJob,null);assert.equal($('package-install').disabled,true);
});
test('lost install acknowledgement only reads saved state, never replays installation',async()=>{
  const {c,$,calls,f,info}=fixture();f.packageJob=review();$('package-name').value='Demo';c.api=async(...args)=>{calls.push(args);if(args[2]==='POST')throw Error('timed out');return {...info,jobs:[{...review(),status:'installing'}]};};
  await assert.rejects(c.installReviewedPackage,/timed out/);assert.equal(calls.filter(call=>call[2]==='POST').length,1);assert.equal(f.packageJob.status,'installing');
});
test('reloading recovers an in-progress job and schedules only GET polling',async()=>{
  const {c,$,calls,timers,info,f}=fixture();c.result={...info,jobs:[{...review(),status:'installing'}]};await c.loadPackages();assert.equal(f.packageJob.id,'job');assert.equal($('package-name').value,'Demo');assert.equal(timers.length,1);assert.equal(calls[0][2],undefined);
});
test('polling errors stop polling and leave explicit recovery guidance',async()=>{
  const {c,$,calls,f,timers}=fixture();f.packageJob={...review(),status:'installing'};c.api=async(...args)=>{calls.push(args);throw Error('offline');};await c.pollPackageJob();assert.match($('package-job-status').textContent,/Polling stopped.*Reload packages/);assert.equal(timers.length,0);assert.equal(calls.length,1);assert.equal(calls[0][2],undefined);
});
test('a poll response from a different project is discarded',async()=>{
  const {c,$,f}=fixture();f.packageJob={...review(),status:'installing'};c.api=async()=>{c.state.project={id:'other'};return {...review(),status:'succeeded'};};await c.pollPackageJob();assert.equal(f.packageJob.status,'installing');assert.equal($('package-restart').hidden,false);assert.equal($('package-job-status').textContent,'');
});
test('untrusted dependency names, package plans, paths, and logs render as text',()=>{
  const {c,$,info,f}=fixture();info.prefix='<script>bad</script>';info.scan.issues=[{module:'<img>',path:'<script>',line:1,code:'missing_dependency',suggested_package:'<img>'}];f.packageJob={...review(),packages:[{name:'<img>',version:'<script>',action:'install'}],log:'<script>code</script>'};c.renderPackages();assert.doesNotMatch($('packages-environment').innerHTML,/<script>/);assert.match($('packages-missing').innerHTML,/&lt;img&gt;/);assert.doesNotMatch($('package-plan-rows').innerHTML,/<img>/);assert.match($('package-job-output').textContent,/<script>code/);
});
