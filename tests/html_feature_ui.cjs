const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../backend/reqlica/reqlicav1.html'),'utf8');
const section=(start,end)=>html.slice(html.indexOf(start),html.indexOf(end));
function fixture(){
  const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',innerHTML:'',hidden:false,dataset:{}});return nodes.get(id);};
  const context={$,state:{},pretty:JSON.stringify};vm.createContext(context);
  vm.runInContext(html.match(/^const esc =.*$/m)[0],context);
  vm.runInContext(html.match(/^function badge\(.*$/m)[0],context);
  for(const [start,end] of [['function failureCause(','function renderDraft('],['function renderDatabaseSetup(','async function editDatabasePlan('],['function renderEnvironment(','async function loadEnvironment(']])vm.runInContext(section(start,end),context);
  return {context,$};
}
test('failure summaries show causes without interpreting logs as HTML and ignore stale checks',()=>{
  const {context:c}=fixture();
  const draft={revision:'current',test:{revision:'current',status:'failed',failures:[{test:'test_auth <img src=x>',output:'Traceback...\nAttributeError: Depends has no headers <script>bad</script>'}]}};
  const markup=c.checkFailureMarkup(draft);
  assert.match(markup,/AttributeError/);assert.match(markup,/&lt;script&gt;/);assert.doesNotMatch(markup,/<script>|<img/);
  draft.test.revision='old';assert.equal(c.checkFailureMarkup(draft),'');
});
test('database preview status and table names are scoped to the selected saved revision',()=>{
  const {context:c,$}=fixture();
  c.state.draft={id:'draft',revision:'current',files:['database/schema.json']};
  c.state.databaseSetupPreview={draft_id:'draft',revision:'current',plan:{},pending_tables:['users<script>'],sql:['CREATE TABLE users(id INTEGER)']};
  c.renderDatabaseSetup();assert.equal($('setup-preview-step').dataset.state,'done');assert.equal($('setup-apply-step').dataset.state,'current');assert.match($('database-table-list').innerHTML,/&lt;script&gt;/);assert.match($('database-setup-hint').textContent,/Pass validation and tests/);
  c.state.databaseSetupPreview.revision='old';c.renderDatabaseSetup();assert.equal($('database-table-list').hidden,true);assert.equal($('database-sql-details').hidden,true);assert.equal($('setup-preview-step').dataset.state,'current');
});
test('environment list stays write-only and distinguishes restart-needed state',()=>{
  const {context:c,$}=fixture();c.state.project={id:'project',name:'Auth',status:'running',environment_revision:'old'};
  c.state.environment={project_id:'project',revision:'new',names:['JWT_SECRET'],value:'must-never-be-rendered'};
  c.renderEnvironment();assert.equal($('environment-status').dataset.tone,'warning');assert.match($('environment-list').innerHTML,/JWT_SECRET/);assert.doesNotMatch($('environment-list').innerHTML,/must-never-be-rendered/);assert.match($('environment-list').innerHTML,/data-replace-environment/);
});
