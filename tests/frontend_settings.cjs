const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
function settings() {
  const data = new Map(), exports = {};
  const code = ts.transpileModule(fs.readFileSync('frontend/src/lib/settings.ts', 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  vm.runInNewContext(code, { exports, URL, localStorage: { getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) } });
  return { ...exports, data };
}
test('API defaults sync across projects while chat behavior remains scoped', () => {
  const s = settings();
  s.saveSettings({ ...s.defaultSettings, model: 'workspace-model', sendWithEnter: false });
  assert.equal(s.readSettings('one').model, 'workspace-model');
  s.saveSettings({ ...s.defaultSettings, model: 'project-model' }, 'one');
  assert.equal(s.readSettings('one').model, 'project-model');
  assert.equal(s.readSettings('two').model, 'project-model');
  assert.equal(s.readSettings('one').sendWithEnter, true);
  assert.equal(s.readSettings('two').sendWithEnter, false);
  s.clearProjectSettings('one');
  assert.equal(s.readSettings('one').sendWithEnter, false);
});
test('saved preferences exclude credentials and unknown fields', () => {
  const s = settings();
  s.saveSettings({ ...s.defaultSettings, apiKey: 'must-not-persist' });
  assert.equal([...s.data.values()].some(value => value.includes('must-not-persist')), false);
});
test('invalid URLs and unsupported response limits cannot be saved', () => {
  const s = settings();
  for (const patch of [{ provider: 'compatible', baseUrl: 'https://key:secret@example.com' }, { timeoutSeconds: 601 }, { maxTokens: 255 }]) {
    assert.throws(() => s.saveSettings({ ...s.defaultSettings, ...patch }));
  }
});
test('corrupt or invalid saved settings safely fall back', () => {
  const s = settings();
  s.data.set('reqlica.settings.workspace', '{broken');
  assert.equal(s.readSettings().provider, 'groq');
  s.data.set('reqlica.settings.workspace', JSON.stringify({ provider: 'unknown' }));
  assert.equal(s.readSettings().provider, 'groq');
});
test('reasoning level persists and invalid levels are rejected', () => {
  const s = settings();
  assert.equal(s.defaultSettings.reasoningMode, 'off');
  assert.equal(s.readSettings().reasoningMode, 'off');
  s.data.set('reqlica.settings.connection.v1', JSON.stringify({provider:'openai',model:'example',baseUrl:''}));
  assert.equal(s.readSettings().reasoningMode, 'off');
  s.saveSettings({ ...s.defaultSettings, reasoningMode: 'high' });
  assert.equal(s.readSettings().reasoningMode, 'high');
  assert.throws(() => s.saveSettings({ ...s.defaultSettings, reasoningMode: 'unknown' }));
});
test('cumulative budgets persist per project and older settings inherit the default', () => {
  const s = settings();
  s.data.set('reqlica.settings.workspace', JSON.stringify({ model: 'legacy-model' }));
  assert.equal(s.readSettings().totalTokenLimit, 100000);
  s.saveSettings({ ...s.defaultSettings, totalTokenLimit: 25000 }, 'one');
  assert.equal(s.readSettings('one').totalTokenLimit, 25000);
  assert.equal(s.readSettings('two').totalTokenLimit, 100000);
  for (const totalTokenLimit of [999, 1000001, 1.5, NaN]) {
    assert.throws(() => s.saveSettings({ ...s.defaultSettings, totalTokenLimit }));
  }
});
