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
  for(const [start,end] of [['function failureCause(','function renderDraft('],['function repairAttemptsMarkup(','function renderRepair('],['function clarificationMarkup(','function syncDecisionProgress('],['function renderDatabaseSetup(','async function editDatabasePlan('],['function renderEnvironment(','async function loadEnvironment(']])vm.runInContext(section(start,end),context);
  return {context,$};
}
test('failure summaries show causes without interpreting logs as HTML and ignore stale checks',()=>{
  const {context:c}=fixture();
  const draft={revision:'current',test:{revision:'current',status:'failed',failures:[{test:'test_auth <img src=x>',output:'Traceback...\nAttributeError: Depends has no headers <script>bad</script>'}]}};
  const markup=c.checkFailureMarkup(draft);
  assert.match(markup,/AttributeError/);assert.match(markup,/&lt;script&gt;/);assert.doesNotMatch(markup,/<script>|<img/);
  draft.test.revision='old';assert.equal(c.checkFailureMarkup(draft),'');
});
test('repair history offers escaped diagnoses, changed files, diffs and blockers',()=>{
  const {context:c}=fixture();
  const markup=c.repairAttemptsMarkup([{status:'repaired',summary:'Fixed <b>injection</b>',changed_files:['endpoints/auth.py'],diff:'- <script>old</script>\n+ request: Request',test:{status:'passed',tests_passed:10,tests_run:10}},{status:'needs_information',summary:'Need a decision',blockers:['Choose <key> name']}]);
  assert.match(markup,/10 \/ 10 tests passed/);assert.match(markup,/data-file="endpoints\/auth.py"/);assert.match(markup,/Review implementation diff/);assert.match(markup,/&lt;key&gt;/);assert.doesNotMatch(markup,/<script>|<b>/);
});
test('clarification cards expose answer progress and preserve safe editable inputs',()=>{
  const {context:c}=fixture();
  const review={summary:'Confirm fields',questions:[{id:'fields',kind:'field',question:'Which <fields>?',options:['Email only']}],answers:{},prior_decisions:[]};
  let markup=c.clarificationMarkup(review,'generate');assert.match(markup,/0 \/ 1 answered/);assert.match(markup,/data-decision-card="generate:fields"/);assert.match(markup,/&lt;fields&gt;/);
  review.answers.fields='Email only';markup=c.clarificationMarkup(review,'generate');assert.match(markup,/1 \/ 1 answered/);assert.match(markup,/data-answered="true"/);
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
