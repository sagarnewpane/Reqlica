const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '../backend/reqlica/reqlicav1.html'), 'utf8');
const source = html.slice(html.indexOf('function chatStorageKey('),html.indexOf('function aiWorkspace('));
const limits = {storedTurns:20,storedBytes:512*1024,requestTurns:8,requestBytes:48*1024};
const turn = (i=0) => ({question:'Question '+i,answer:'Reply '+i,context_revision:'a'.repeat(64),sources:['endpoints/hello.py'],limitations:[],model:'openai/gpt-oss-20b'});
function fixture(config={console:'/console',gateway:'/gateway'}, storage=new Map()) {
  const session = {getItem:key=>storage.get(key) || null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
  const api = new Function('config','chatLimits','sessionStorage','TextEncoder','let chatStorageAvailable=true;'+source+'; return {chatStorageKey,cleanChatTurns,loadProjectChat,saveProjectChat,chatHistoryForRequest,available:()=>chatStorageAvailable};')(config,limits,session,TextEncoder);
  return {...api,storage,session};
}

test('completed exchanges survive reload through tab session storage only', () => {
  const first=fixture();
  first.saveProjectChat('project-a',{turns:[turn()]});
  const reloaded=fixture(undefined,first.storage);
  assert.deepEqual(reloaded.loadProjectChat('project-a'),[turn()]);
  assert.deepEqual(reloaded.loadProjectChat('project-b'),[]);
});

test('chat storage is separated by project and configured backend', () => {
  const first=fixture();
  first.saveProjectChat('project-a',{turns:[turn()]});
  const other=fixture({console:'http://localhost:3001',gateway:'/gateway'},first.storage);
  assert.deepEqual(other.loadProjectChat('project-a'),[]);
  assert.notEqual(first.chatStorageKey('project-a'),other.chatStorageKey('project-a'));
});

test('storage whitelists exchange fields, never whole provider/workspace objects', () => {
  const f=fixture();
  f.saveProjectChat('a',{
    turns:[{...turn(),api_key:'private-key',provider:{api_key:'private-key'}}],
    generationProvider:'private-provider',chatConsent:'private-consent',apiKey:'private-key',
  });
  const encoded=[...f.storage.values()][0];
  assert.doesNotMatch(encoded,/private|api_key|generationProvider|chatConsent/);
  assert.deepEqual(f.loadProjectChat('a'),[turn()]);
});

test('the display retains a bounded recent conversation', () => {
  const f=fixture(),workspace={turns:Array.from({length:25},(_,i)=>turn(i))};
  f.saveProjectChat('a',workspace);
  assert.equal(workspace.turns.length,20);
  assert.equal(workspace.turns[0].question,'Question 5');
  assert.equal(f.loadProjectChat('a').at(-1).question,'Question 24');
});

test('follow-ups use the last eight complete exchanges in chronological order', () => {
  const f=fixture();
  const history=f.chatHistoryForRequest(Array.from({length:12},(_,i)=>turn(i)));
  assert.equal(history.length,8);
  assert.equal(history[0].question,'Question 4');
  assert.equal(history.at(-1).question,'Question 11');
  assert.deepEqual(Object.keys(history[0]),['question','answer','context_revision']);
});

test('history size is bounded by UTF-8 bytes, never split mid-exchange', () => {
  const f=fixture();
  const history=f.chatHistoryForRequest([
    {...turn(1),answer:'x'.repeat(32000)},
    {...turn(2),answer:'界'.repeat(12000)},
    turn(3),
  ]);
  assert.equal(history.length,2);
  assert.equal(history[0].question,'Question 2');
  assert.ok(new TextEncoder().encode(JSON.stringify(history)).length<=limits.requestBytes);
  assert.equal(history[0].answer,'界'.repeat(12000));
  assert.deepEqual(f.chatHistoryForRequest([{...turn(),answer:'界'.repeat(20000)}]),[]);
});

test('malformed or oversized saved conversations are ignored safely', () => {
  const f=fixture(),key=f.chatStorageKey('a');
  for(const saved of ['not json',JSON.stringify({version:999,turns:[turn()]}),JSON.stringify({version:1,turns:[{...turn(),context_revision:'invalid'}]}),'x'.repeat(limits.storedBytes+1)]) {
    f.storage.set(key,saved);
    assert.deepEqual(f.loadProjectChat('a'),[]);
  }
  assert.deepEqual(f.cleanChatTurns([{...turn(),sources:['x'.repeat(4097)]}]),[]);
  assert.deepEqual(f.cleanChatTurns([{...turn(),answer:' '}]),[]);
});

test('clearing one chat does not affect another project', () => {
  const f=fixture();
  f.saveProjectChat('a',{turns:[turn(1)]});f.saveProjectChat('b',{turns:[turn(2)]});
  f.session.removeItem(f.chatStorageKey('a'));
  assert.deepEqual(f.loadProjectChat('a'),[]);
  assert.deepEqual(f.loadProjectChat('b'),[turn(2)]);
});

test('storage failures preserve the in-memory conversation', () => {
  const storage={get(){throw Error('denied');},set(){throw Error('quota');}};
  const f=fixture(undefined,storage),workspace={turns:[turn()]};
  f.saveProjectChat('a',workspace);
  assert.equal(f.available(),false);
  assert.deepEqual(workspace.turns,[turn()]);
  assert.deepEqual(f.loadProjectChat('a'),[]);
});
