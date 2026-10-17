const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// Test the shipped single-file implementation, not a duplicate of its logic.
const html = fs.readFileSync(path.join(__dirname, '../backend/reqlica/reqlicav1.html'), 'utf8');
const source = html.slice(html.indexOf('function resolveExampleRef('), html.indexOf('async function insertRequestExample('));
const {buildRequestExample, exampleValue} = new Function(source + '; return {buildRequestExample, exampleValue};')();
const route = {method:'POST',path:'/books/{book_id:int}'};
function contract(operation={}, parameters=[]) {
  return {paths:{'/books/{book_id}':{parameters:[{in:'path',name:'book_id',schema:{type:'integer',minimum:10}},...parameters],post:operation}}};
}
const body = schema => ({requestBody:{content:{'application/json':{schema}}}});

test('path converters, query defaults, enum values and repeated arrays', () => {
  const doc = contract({parameters:[
    {in:'query',name:'limit',schema:{type:'integer',default:20}},
    {in:'query',name:'status',schema:{enum:['in stock','sold out']}},
    {in:'query',name:'tags',schema:{type:'array',default:['fiction','books & more']}},
  ]});
  assert.equal(buildRequestExample(doc,route).path,'/books/10?limit=20&status=in+stock&tags=fiction&tags=books+%26+more');
});

test('operation parameters override inherited path-item parameters', () => {
  const doc = contract({parameters:[{in:'path',name:'book_id',example:42}]});
  assert.equal(buildRequestExample(doc,route).path,'/books/42');
});

test('optional nullable query parameters are omitted instead of sending literal null', () => {
  const doc = contract({parameters:[{in:'query',name:'limit',schema:{anyOf:[{type:'integer'},{type:'null'}],default:null}}]});
  assert.equal(buildRequestExample(doc,route).path,'/books/10');
});

test('referenced nested JSON models, arrays, unions and read-only fields', () => {
  const doc = contract(body({$ref:'#/components/schemas/Book'}));
  doc.components = {schemas:{Book:{type:'object',properties:{
    id:{type:'integer',readOnly:true},
    title:{type:'string',examples:['A reader’s guide']},
    price:{type:'number',minimum:2.5},
    published:{type:'boolean',default:false},
    details:{type:'object',properties:{code:{type:'string',const:'BOOK'}}},
    tags:{type:'array',items:{type:'string',enum:['fiction']}},
    note:{anyOf:[{type:'null'},{type:'string',default:'optional note'}]},
  }}}};
  assert.deepEqual(JSON.parse(buildRequestExample(doc,route).body),{
    title:'A reader’s guide',price:2.5,published:false,details:{code:'BOOK'},tags:['fiction'],note:'optional note',
  });
});

test('media examples take precedence over schema examples and defaults', () => {
  const doc = contract({requestBody:{content:{'application/json':{
    example:{title:'Documented example'},schema:{type:'object',default:{title:'Default'}},
  }}}});
  assert.deepEqual(JSON.parse(buildRequestExample(doc,route).body),{title:'Documented example'});
});

test('referenced parameters, bodies and named examples are resolved locally', () => {
  const doc = contract({requestBody:{$ref:'#/components/requestBodies/Book'}},[{$ref:'#/components/parameters/Search'}]);
  doc.components = {
    parameters:{Search:{in:'query',name:'search',examples:{sample:{$ref:'#/components/examples/Term'}}}},
    examples:{Term:{value:'books & stories'},Book:{value:{title:'Named example'}}},
    requestBodies:{Book:{content:{'application/problem+json':{examples:{sample:{$ref:'#/components/examples/Book'}}}}}},
  };
  const result = buildRequestExample(doc,route);
  assert.equal(result.path,'/books/10?search=books+%26+stories');
  assert.deepEqual(JSON.parse(result.body),{title:'Named example'});
});

test('false, zero, null and empty-string examples are retained', () => {
  for(const value of [false,0,null,'']) {
    assert.equal(exampleValue({type:'string',example:value},{}),value);
    assert.equal(exampleValue({type:'string',default:value},{}),value);
  }
});

test('allOf object samples are combined and refs use escaped JSON pointers', () => {
  const doc = {components:{schemas:{'A/B':{type:'object',properties:{title:{default:'Book'}}}}}};
  assert.deepEqual(exampleValue({allOf:[{$ref:'#/components/schemas/A~1B'},{type:'object',properties:{count:{default:2}}}]},doc),{title:'Book',count:2});
});

test('missing operations, undefined path parameters and non-JSON bodies explain failure', () => {
  assert.throws(()=>buildRequestExample({paths:{}},route),/No saved OpenAPI operation/);
  assert.throws(()=>buildRequestExample({paths:{'/books/{book_id}':{post:{}}}},route),/missing a path parameter/);
  assert.throws(()=>buildRequestExample(contract({requestBody:{content:{'multipart/form-data':{}}}}),route),/non-JSON/);
});

test('external, missing and circular refs never trigger network access', () => {
  for(const $ref of ['https://example.com/schema.json','#/missing']) {
    assert.throws(()=>exampleValue({$ref},{}),/reference/);
  }
  const doc = {components:{schemas:{Loop:{$ref:'#/components/schemas/Loop'}}}};
  assert.throws(()=>exampleValue({$ref:'#/components/schemas/Loop'},doc),/circular/);
  const recursive = {type:'object',properties:{child:{$ref:'#/components/schemas/Tree'}}};
  assert.throws(()=>exampleValue(recursive,{components:{schemas:{Tree:recursive}}}),/deeply nested/);
});

test('required headers and cookies are reported, never inserted into the body or path', () => {
  const doc = contract({parameters:[
    {in:'header',name:'X-Token',required:true,example:'not-inserted'},
    {in:'cookie',name:'session',required:true,example:'not-inserted'},
  ]});
  const result = buildRequestExample(doc,route);
  assert.equal(result.notes.length,2);
  assert.equal(result.path,'/books/10');
  assert.equal(result.body,'');
  assert.doesNotMatch(JSON.stringify(result),/not-inserted/);
});

test('Reqlica draft revision headers are managed by the disposable call API', () => {
  const doc = contract({parameters:[{in:'header',name:'X-Reqlica-Expected-Revision',required:true,schema:{default:'revision'}}]});
  doc['x-reqlica-draft-id']='draft';
  assert.deepEqual(buildRequestExample(doc,route).notes,[]);
});

test('unsupported parameter serialization is not silently guessed', () => {
  assert.throws(()=>buildRequestExample(contract({parameters:[{in:'query',name:'filter',schema:{type:'object'}}]}),route),/manual input/);
  assert.throws(()=>buildRequestExample(contract({parameters:[{in:'query',name:'ids',style:'pipeDelimited',schema:{type:'array',items:{type:'integer'}}}]}),route),/unsupported example style/);
});

test('GET examples have no body and GET request bodies are explicitly unsupported', () => {
  assert.deepEqual(buildRequestExample({paths:{'/hello':{get:{}}}},{method:'GET',path:'/hello'}),{method:'GET',path:'/hello',body:'',notes:[]});
  assert.throws(()=>buildRequestExample({paths:{'/hello':{get:body({type:'string'})}}},{method:'GET',path:'/hello'}),/GET\/HEAD/);
});

test('automatic example sizes are bounded', () => {
  assert.throws(()=>exampleValue({type:'array',minItems:100,items:{type:'string'}},{}),/five items/);
  assert.throws(()=>exampleValue({type:'string',minLength:1000},{}),/long value/);
});

function exampleUI({target='live',confirm=true,error=null}={}) {
  const nodes = Object.fromEntries(Object.entries({
    'request-target':target,'request-method':'POST','request-path':route.path,
    'request-body':'','request-headers':'{"Authorization":"keep-this"}',
  }).map(([id,value])=>[id,{value}]));
  nodes['request-example-status']={textContent:''};
  const calls=[], confirmations=[];
  const insertSource = html.slice(html.indexOf('async function insertRequestExample('),html.indexOf('function parseHeaders('));
  const insert = new Function('buildRequestExample','selectedRequestRoute','$','state','api','root','draftRoot','confirmAction','toast',insertSource+'; return insertRequestExample;')(
    buildRequestExample,()=>route,id=>nodes[id],{draft:{revision:'pinned-revision'}},
    async (...args)=>{calls.push(args);if(error)throw Error(error);return contract(body({type:'object',properties:{title:{default:'Book'}}}));},
    ()=>'/api/projects/test',()=>'/api/projects/test/drafts/draft',
    async (...args)=>{confirmations.push(args);return confirm;},()=>{},
  );
  return {nodes,calls,confirmations,insert};
}

test('live insertion only fetches the saved contract and preserves headers', async () => {
  const ui = exampleUI();
  await ui.insert();
  assert.deepEqual(ui.calls,[['gateway','/api/projects/test/openapi.json']]);
  assert.equal(ui.nodes['request-path'].value,'/books/10');
  assert.deepEqual(JSON.parse(ui.nodes['request-body'].value),{title:'Book'});
  assert.equal(ui.nodes['request-headers'].value,'{"Authorization":"keep-this"}');
  assert.match(ui.nodes['request-example-status'].textContent,/No request was sent/);
});

test('draft insertion pins its schema read to the selected revision', async () => {
  const ui = exampleUI({target:'draft'});
  await ui.insert();
  assert.deepEqual(ui.calls,[['console','/api/projects/test/drafts/draft/openapi.json?revision=pinned-revision']]);
  assert.match(ui.nodes['request-example-status'].textContent,/pinned draft/);
});

test('cancelled replacement keeps every input unchanged', async () => {
  const ui = exampleUI({confirm:false});
  ui.nodes['request-body'].value='{"my":"unsent work"}';
  await ui.insert();
  assert.equal(ui.confirmations.length,1);
  assert.equal(ui.nodes['request-path'].value,route.path);
  assert.equal(ui.nodes['request-body'].value,'{"my":"unsent work"}');
  assert.match(ui.nodes['request-example-status'].textContent,/cancelled/);
});

test('explicit replacement requires confirmation; failed contract reads never clear inputs', async () => {
  const ui = exampleUI();
  ui.nodes['request-path'].value='/books/123?mine=yes';
  await ui.insert();
  assert.equal(ui.confirmations.length,1);
  assert.equal(ui.nodes['request-path'].value,'/books/10');
  const failed = exampleUI({error:'Schema is missing or stale'});
  failed.nodes['request-body'].value='{"my":"unsent work"}';
  await assert.rejects(failed.insert(),/missing or stale/);
  assert.equal(failed.nodes['request-body'].value,'{"my":"unsent work"}');
  assert.match(failed.nodes['request-example-status'].textContent,/Inputs kept/);
});
