const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'../backend/reqlica/reqlicav1.html'),'utf8');
function fixture(){
 const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:'',textContent:'',hidden:false});return nodes.get(id);};
 const w={dataTarget:'related',dataSchema:{fingerprint:'a'.repeat(64),tables:[{name:'users',storage_type:'table'},{name:'posts',storage_type:'table'}],relationships:[]},dataTable:'',dataRawPreview:'',dataPreview:null};
 const provider={id:'p',kind:'compatible',model:'test',baseUrl:'http://localhost:1234/v1'};
 const calls=[],errors=[],c={$,TextEncoder,structuredClone,state:{project:{id:'p',name:'Social'}},aiWorkspace:()=>w,pretty:JSON.stringify,root:()=>'/api/projects/p',aiConfig:{providers:[provider],defaults:{temperature:0,maxTokens:8192}},providerKeys:new Map(),clearError(){},showError:e=>errors.push(e),confirmAction:async()=>true,syncControls(){},api:async(...args)=>{calls.push(args);return c.result;}};
 vm.createContext(c);vm.runInContext(html.match(/^const esc =.*$/m)[0],c);
 for(const [a,b] of [['function dataInputs(','function resolveChainValue('],['async function generateDataWithGroq(','function downloadData(']])vm.runInContext(html.slice(html.indexOf(a),html.indexOf(b)),c);
 $('data-provider').value='p';$('ai-data-prompt').value='Create 2 users and 2 posts, one post per user';
 c.result={target:'related',records_by_table:{users:[{name:'Ava'},{name:'Milo'}],posts:[{user_id:{$ref:'users.0.id'},title:'First'},{user_id:{$ref:'users.1.id'},title:'Second'}]},validation:{status:'passed'},schema_fingerprint:w.dataSchema.fingerprint,preview_digest:'d'.repeat(64),summary:'Two users and two posts',counts:{users:2,posts:2}};
 return {c,w,$,calls,errors};
}
test('related generation sends one description without a single-table count or template',async()=>{
 const {c,w,$,calls,errors}=fixture();$('ai-data-count').value='not-a-number';$('ai-data-template').value='not JSON';
 await c.generateDataWithGroq();assert.deepEqual(errors,[]);assert.equal(calls.length,1);assert.equal(calls[0][1],'/api/projects/p/data/related/generate');
 const body=calls[0][3];assert.equal(body.requirements,'Create 2 users and 2 posts, one post per user');assert.equal(body.count,undefined);assert.equal(body.table,undefined);assert.equal(body.template,undefined);
 assert.equal(w.dataPreview.posts[1].user_id.$ref,'users.1.id');assert.equal($('ai-data-preview-count').textContent,'4 records · 2 tables');assert.equal($('data-count-field').hidden,true);assert.equal($('data-template-field').hidden,true);
});
test('related preview validates all tables then submits one atomic insert',async()=>{
 const {c,w,calls,errors}=fixture();w.dataRawPreview=JSON.stringify(c.result.records_by_table);
 await c.validateDataPreview();assert.equal(calls[0][1],'/api/projects/p/data/related/validate');assert.equal(calls[0][3].records_by_table.posts.length,2);
 c.result={inserted_count:4,inserted_counts:{users:2,posts:2}};await c.insertDataPreview();assert.deepEqual(errors,[]);
 assert.equal(calls.length,2);assert.equal(calls[1][1],'/api/projects/p/data/related/insert');assert.equal(calls[1][3].confirm,true);assert.equal(calls[1][3].expected_preview_digest,'d'.repeat(64));
 assert.equal(calls[1][3].records_by_table.posts[0].user_id.$ref,'users.0.id');
});
test('invalid or excessive table previews are rejected before requests',()=>{
 const {c,w}=fixture();for(const data of [[],{}, {users:[]},{users:[{}]},{users:Array(101).fill({name:'A'})}]){
 w.dataRawPreview=JSON.stringify(data);assert.throws(()=>c.readDataRecords());
 }
});
test('related counts and table names are rendered as text, not markup',()=>{
 const {c,w,$}=fixture();w.dataSchema.tables=[{name:'<img src=x>',storage_type:'table'}];w.dataPreview={'<script>':[ {id:1} ]};w.dataRawPreview=JSON.stringify(w.dataPreview);c.renderDataWorkspace();
 assert.match($('data-related-tables').innerHTML,/&lt;img/);assert.doesNotMatch($('data-related-tables').innerHTML,/<img/);assert.match($('data-related-counts').innerHTML,/&lt;script&gt;/);
});
