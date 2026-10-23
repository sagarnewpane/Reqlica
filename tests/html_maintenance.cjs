const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../backend/reqlica/reqlicav1.html'),'utf8');
function fixture(){
  const nodes=new Map(),calls=[],confirmations=[],storage=[];
  const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',checked:false,disabled:false,dataset:{},files:[]});return nodes.get(id);};
  const context={$,state:{project:{id:'p',name:'Example',active_version:'v'},drafts:[],busy:false},URLSearchParams,AbortController,setTimeout,clearTimeout,URL,Date,JSON,
    pretty:v=>JSON.stringify(v,null,2),root:()=>'/api/projects/p',draftName:d=>d.name || d.id,errorMessage:e=>e.message,
    esc:v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    document:{querySelectorAll:()=>[]},toast(){},renderDraftList(){},refreshProject:async()=>{},loadProjects:async()=>{},updateDraft:d=>context.state.draft=d,
    config:{gateway:'/gateway'},apiURL:(service,p)=>'/'+service+p,
    confirmAction:async(...args)=>{confirmations.push(args);return true;},api:async(...args)=>{calls.push(args);return {};},
    localStorage:{setItem:(...args)=>storage.push(args)}
  };
  vm.createContext(context);vm.runInContext(html.slice(html.indexOf('// Backend feature controls.'),html.indexOf('const actions={')),context);context.resetFeatureState();
  context.loadMaintenance=async()=>{};
  return {c:context,$,calls,confirmations,storage,f:context.featureState()};
}
function draft(){const rev='a'.repeat(64);return {id:'d',name:'Migration',revision:rev,base_version:'v',validation:{status:'passed',revision:rev},test:{status:'passed',revision:rev,tests_passed:1,tests_run:1}};}
function migrationFixture(){const x=fixture(),d=draft();x.c.state.drafts=[d];x.$('migration-draft').value=d.id;x.f.preview={plan:{id:'plan',from_schema:'schema',operations:[]},plan_digest:'digest'};x.f.previewOperations=x.$('migration-operations').value;return {...x,d};}
test('all added views have navigation, unique IDs, and JavaScript parses',()=>{
  const ids=[...html.split('<script>')[0].matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);assert.equal(new Set(ids).size,ids.length);
  for(const v of ['traffic','inspection','maintenance']){assert.ok(ids.includes(v+'-view'));assert.ok(html.includes('data-view="'+v+'"'));assert.ok(html.includes('value="'+v+'"'));}
  new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);assert.doesNotMatch(html,/No scenario API yet/);
});
test('auth metadata is write-only; replacing pins loaded revision and clears values after success',async()=>{
  const {c,$,calls,f,storage}=fixture();c.fillAuthentication({revision:'old',mode:'bearer',header_name:'X-API-Key',credentials:[{name:'old'}],value:'hidden'});
  assert.doesNotMatch($('auth-saved').textContent,/hidden/);
  $('credential-name').value='tester';$('credential-value').value='example-test-token';c.addPendingCredential();assert.equal($('credential-value').value,'');assert.doesNotMatch($('credential-pending').textContent,/example-test-token/);
  c.api=async(...args)=>{calls.push(args);return {revision:'new',mode:'bearer',header_name:'X-API-Key',credentials:[{name:'tester'}]};};
  await c.saveMockAuth();assert.equal(calls[0][3].expected_revision,'old');assert.equal(calls[0][3].credentials[0].value,'example-test-token');assert.equal(f.credentials.length,0);assert.equal(storage.length,0);
});
test('auth cancellation and rejection preserve pending credentials without automatic retry',async()=>{
  const {c,$,calls,f}=fixture();f.auth={revision:'r'};$('auth-mode').value='api_key';$('auth-header').value='X-Key';f.credentials=[{name:'a',value:'value'}];
  c.confirmAction=async()=>false;await c.saveMockAuth();assert.equal(calls.length,0);assert.equal(f.credentials.length,1);
  c.confirmAction=async()=>true;c.api=async(...args)=>{calls.push(args);throw Error('409');};await assert.rejects(c.saveMockAuth,/409/);assert.equal(calls.length,1);assert.equal(f.credentials.length,1);
});
test('controls initialize before any project is selected',()=>{const {c}=fixture();delete c.state.features;c.state.project=null;c.syncFeatureControls();assert.equal(c.featureState().credentials.length,0);});
test('project switching removes previous credentials, previews, logs, and archive secret opt-in',()=>{
  const {c,$,f}=fixture();f.credentials=[{value:'secret'}];f.preview={};f.events=[{id:1}];$('archive-secrets').checked=true;c.state.project={id:'other'};
  const next=c.featureState();assert.equal(next.projectId,'other');assert.equal(next.credentials.length,0);assert.equal(next.preview,undefined);assert.equal(next.events.length,0);assert.equal($('archive-secrets').checked,false);
});
test('scenario editor drops read-only counters, reset preserves unsaved rule text',async()=>{
  const {c,$,calls,f}=fixture();c.fillScenarios({revision:'r',rules:[{method:'GET',path:'/hello',remaining_failures:2}]});assert.doesNotMatch($('scenario-rules').value,/remaining_failures/);
  $('scenario-rules').value='[{"method":"GET","path":"/changed"}]';c.api=async(...args)=>{calls.push(args);return {revision:'r',rules:[]};};await c.resetScenarioCounters();
  assert.equal(calls[0][3].expected_revision,'r');assert.match($('scenario-rules').value,/changed/);assert.equal(f.scenarios.revision,'r');
});
test('invalid JSON and manual revision injection fail before writes',async()=>{
  const {c,$,calls,f}=fixture();f.cors={revision:'saved'};$('cors-settings').value='{"expected_revision":"override"}';await assert.rejects(c.saveCors,/policy fields/);
  f.scenarios={revision:'r',rules:[]};$('scenario-rules').value='{}';await assert.rejects(c.saveScenarios,/array/);assert.equal(calls.length,0);
});
test('partial traffic load allows only available settings and reports each failure',async()=>{
  const {c,$,f}=fixture();c.api=async(_s,p)=>{if(p.endsWith('mock-auth'))return {revision:'r',mode:'none',header_name:'X-Key',credentials:[]};throw Error('unavailable');};
  await c.loadTraffic();assert.ok(f.auth);assert.equal(f.scenarios,null);assert.equal(f.cors,null);assert.match($('traffic-status').textContent,/Scenarios: unavailable.*CORS: unavailable/);
});
test('log pagination keeps its original filters and escapes event fields',async()=>{
  const {c,$,calls}=fixture();$('log-kind').value='request';$('log-request-id').value='';c.api=async(...args)=>{calls.push(args);return {events:[{id:1,timestamp:'now',kind:'request',path:'<img src=x>',status:200}],next_before_id:1};};
  await c.loadLogs();assert.match($('log-events').innerHTML,/&lt;img/);assert.doesNotMatch($('log-events').innerHTML,/<img/);
  $('log-kind').value='stderr';await c.loadLogs(true);assert.match(calls[1][1],/kind=request/);assert.match(calls[1][1],/before_id=1/);
});
test('migration save sends exact reviewed plan and pins draft revision',async()=>{
  const {c,calls,f,d}=migrationFixture();c.api=async(...args)=>{calls.push(args);return d;};await c.saveMigration();assert.equal(calls[0][3].expected_revision,d.revision);assert.match(calls[0][1],/files\?path=database%2Fmigration\.json$/);assert.equal('path' in calls[0][3],false);assert.equal(calls[0][3].content,JSON.stringify(f.preview.plan,null,2));
});
test('changed operations and unsaved source block migration save and apply',async()=>{
  const {c,$,calls}=migrationFixture();$('migration-operations').value='[{}]';await assert.rejects(c.saveMigration,/Preview/);assert.equal(calls.length,0);
  $('migration-operations').value='[]';c.state.dirty=true;await assert.rejects(c.applyMigration,/unsaved/);assert.equal(calls.length,0);
});
test('migration apply rejects stale checks or a different saved plan',async()=>{
  const {c,calls,d}=migrationFixture();c.api=async(...args)=>{calls.push(args);return {...d,revision:'changed'};};await assert.rejects(c.applyMigration,/revision changed/);assert.equal(calls.length,1);
  c.api=async(_s,p)=>p.includes('/files?')?{revision:d.revision,content:'{}'}:d;await assert.rejects(c.applyMigration,/differs/);
});
test('migration apply verifies saved content and confirms all revision pins',async()=>{
  const {c,f,d,calls,confirmations}=migrationFixture();c.api=async(_s,p)=>p.includes('/files?')?{revision:d.revision,content:JSON.stringify(f.preview.plan)}:d;c.maintenanceOperation=async(...args)=>calls.push(args);
  await c.applyMigration();assert.equal(calls.length,1);assert.equal(calls[0][1].expected_revision,d.revision);assert.equal(calls[0][1].expected_plan_digest,'digest');assert.equal(calls[0][1].expected_schema,'schema');assert.equal(confirmations.length,1);
});
test('uncertain maintenance result refreshes state and never retries the write',async()=>{
  const {c,calls}=fixture();let refreshed=0;c.api=async(...args)=>{calls.push(args);throw Error('timed out');};c.refreshProject=async()=>refreshed++;c.loadMaintenance=async()=>refreshed++;
  await assert.rejects(()=>c.maintenanceOperation('data-reset',{confirm:true}),/timed out/);assert.equal(calls.length,1);assert.equal(refreshed,2);
});
test('baseline reset and relocation pin reviewed state; cancellation sends nothing',async()=>{
  const {c,$,f,calls}=fixture();f.baseline={baseline:{},revision:'baseline',current_schema_fingerprint:'schema'};c.maintenanceOperation=async(...args)=>calls.push(args);await c.resetData();assert.equal(calls[0][1].expected_revision,'baseline');assert.equal(calls[0][1].expected_schema,'schema');
  f.maintenance={active_revision:'source',directory:'/old'};$('relocate-directory').value='/new';await c.relocateProject();assert.equal(calls[1][1].expected_directory,'/old');assert.equal(calls[1][1].expected_revision,'source');
  c.confirmAction=async()=>false;await c.resetData();assert.equal(calls.length,2);
});
test('preflight probe uses private diagnostic headers and cannot target a management path',async()=>{
  const {c,$,calls}=fixture();$('cors-probe-path').value='/hello';$('cors-probe-origin').value='http://localhost:5173';$('cors-probe-method').value='GET';$('cors-probe-headers').value='Content-Type';await c.probeCors();assert.equal(calls[0][1],'/mock/p/hello');assert.equal(calls[0][2],'OPTIONS');assert.equal(calls[0][4]['X-Reqlica-Probe-Origin'],'http://localhost:5173');
  $('cors-probe-path').value='//api/projects';await assert.rejects(c.probeCors,/mock path/);assert.equal(calls.length,1);
});
test('ZIP transfers stay binary, retain UI guards and reject non-ZIP responses',async()=>{
  const {c}=fixture();let request;c.fetch=async(url,options)=>{request={url,options};return {ok:true,headers:{get:()=> 'application/zip'},blob:async()=>({size:4,bytes:'PK'})};};
  assert.equal((await c.archiveRequest('/archive')).bytes,'PK');assert.equal(request.options.credentials,'omit');assert.equal(request.options.redirect,'error');assert.equal(request.options.headers['X-Reqlica-UI'],'1');
  c.fetch=async()=>({ok:true,headers:{get:()=> 'text/html'}});await assert.rejects(()=>c.archiveRequest('/archive'),/ZIP/);
});
test('oversized or relative-directory imports are rejected before upload',async()=>{
  const {c,$}=fixture();$('archive-import-directory').value='relative';await assert.rejects(c.importArchive,/absolute/);
  $('archive-import-directory').value='/new';$('archive-import-file').files=[{name:'x.zip',size:33*1024*1024}];await assert.rejects(c.importArchive,/32 MiB/);
});
