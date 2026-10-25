const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');

function ids(browserCrypto) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync('frontend/src/lib/ids.ts', 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(code, { exports, crypto: browserCrypto, Uint8Array });
  return exports;
}

test('request IDs work when randomUUID is unavailable', () => {
  let next = 0;
  const { newUuid, newRequestId } = ids({
    getRandomValues(bytes) {
      for (let index = 0; index < bytes.length; index++) bytes[index] = next++;
      return bytes;
    },
  });
  const uuid = newUuid();
  assert.match(uuid, /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
  assert.match(newRequestId(), /^[a-f0-9]{32}$/);
});

test('unavailable browser crypto fails with an actionable message', () => {
  assert.throws(() => ids(undefined).newRequestId(), /cannot generate request IDs/);
});
