const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');

function load(file, globals = {}) {
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(source, { exports, URL, URLSearchParams, AbortSignal, process: { env: {} }, ...globals });
  return exports;
}

const projectId = 'a'.repeat(32);
const resourceId = 'b'.repeat(32);
const operations = [
  ['endpoints', ['GET']],
  ['deletion', ['GET']],
  ['deletion-files', ['GET']],
  ['registration', ['DELETE']],
  ['start', ['POST']],
  ['stop', ['POST']],
  ['activate', ['POST']],
  ['drafts', ['GET', 'POST']],
  [`drafts/${resourceId}`, ['GET', 'PATCH', 'DELETE']],
  [`drafts/${resourceId}/validate`, ['POST']],
  [`drafts/${resourceId}/test`, ['POST']],
  [`drafts/${resourceId}/approve`, ['POST']],
  ['environment', ['GET']],
  ['environment/SERVICE_API_KEY', ['PUT', 'DELETE']],
  ['packages', ['GET']],
  ['packages/preview', ['POST']],
  [`packages/jobs/${resourceId}`, ['GET']],
  [`packages/jobs/${resourceId}/install`, ['POST']],
  ['agent-runs', ['GET']],
  [`agent-runs/${resourceId}`, ['GET', 'PUT']],
  [`agent-runs/${resourceId}/step`, ['POST']],
  [`agent-runs/${resourceId}/replies`, ['POST']],
  ['publish', []],
  [`drafts/${resourceId}/publish`, []],
  ['environment/INVALID-NAME', []],
  ['agent-runs/invalid', []],
  ['logs', ['GET']],
  ['logs/1', ['GET']],
  ['logs/settings', ['GET', 'PUT']],
  ['logs/0', []],
  ['logs/arbitrary', []],
];

test('project proxy preserves the exact method allowlist for every route family', async () => {
  for (const [route, allowedMethods] of operations) {
    for (const method of ['GET', 'PUT', 'PATCH', 'DELETE', 'POST', 'HEAD', 'OPTIONS']) {
      let forwarded = false;
      const { default: handler } = load('frontend/src/pages/api/projects/[projectId]/[...path].ts', {
        fetch: async () => {
          forwarded = true;
          return { ok: true, status: 200, json: async () => ({}) };
        },
      });
      const result = {};
      const response = {
        setHeader() {},
        status(code) { result.status = code; return this; },
        json(body) { result.body = body; return this; },
      };
      await handler({
        method,
        query: { projectId, path: route.split('/') },
        headers: { host: 'localhost:3000', 'content-type': 'application/json' },
        body: { confirm: true, delete_files: false, draft_id: resourceId, expected_revision: 'c'.repeat(64), capture_payloads: false, capture_output: false, max_body_bytes: 1024 },
      }, response);

      const allowed = allowedMethods.includes(method);
      assert.equal(result.status, allowed ? 200 : 405, `${method} ${route}`);
      assert.equal(forwarded, allowed, `${method} ${route} upstream forwarding`);
    }
  }
});

test('shared project ID guard rejects missing, array, and malformed query values', () => {
  const { isProjectId } = load('frontend/src/lib/project-route.ts');
  assert.equal(isProjectId(projectId), true);
  for (const value of [undefined, null, [], [projectId], 123, '', 'a'.repeat(31), 'A'.repeat(32), `${projectId}/drafts`]) {
    assert.equal(isProjectId(value), false);
  }
});
