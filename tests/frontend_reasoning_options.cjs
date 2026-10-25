const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');

test('reasoning options proxy forwards only model settings', async () => {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync('frontend/src/pages/api/ai/reasoning-options.ts', 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(code, { exports, URL, AbortSignal, process: { env: {} }, fetch: async (url, init) => {
    assert.equal(url.pathname, '/api/ai/reasoning-options');
    assert.deepEqual(JSON.parse(init.body), { provider: 'compatible', model: 'vendor/model', base_url: 'https://openrouter.ai/api/v1' });
    return { status: 200, ok: true, json: async () => ({ modes: ['default', 'low'] }) };
  } });
  const output = {};
  await exports.default({ method: 'POST', headers: { host: 'localhost:3000', 'content-type': 'application/json' }, body: {
    provider: 'compatible', model: 'vendor/model', base_url: 'https://openrouter.ai/api/v1', api_key: 'never-forward',
  } }, { setHeader() {}, status(code) { output.status = code; return this; }, json(body) { output.body = body; return this; } });
  assert.equal(output.status, 200);
  assert.equal(output.body.modes[1], 'low');
});
