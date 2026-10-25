const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("../frontend/node_modules/typescript");

const projectId = "a".repeat(32);
const draftId = "b".repeat(32);
const revision = "c".repeat(64);
const fingerprint = "d".repeat(64);
const digest = "e".repeat(64);

function handler(fetch) {
  const exports = {};
  const code = ts.transpileModule(
    fs.readFileSync(
      "frontend/src/pages/api/projects/[projectId]/[...path].ts",
      "utf8",
    ),
    { compilerOptions: { module: ts.ModuleKind.CommonJS } },
  ).outputText;
  vm.runInNewContext(code, {
    exports,
    URL,
    URLSearchParams,
    AbortSignal,
    process: { env: {} },
    fetch,
  });
  return exports.default;
}

async function call(route, method, body, fetch, query = {}) {
  const result = { headers: {} };
  const response = {
    setHeader(name, value) {
      result.headers[name] = value;
    },
    status(value) {
      result.status = value;
      return this;
    },
    json(value) {
      result.body = value;
      return this;
    },
    send(value) {
      result.body = value;
      return this;
    },
  };
  await handler(fetch)(
    {
      method,
      query: { projectId, path: route.split("/"), ...query },
      headers: { host: "localhost:3000", "content-type": "application/json" },
      body,
    },
    response,
  );
  return result;
}

test("published and draft contracts reach the correct backend with a revision pin", async () => {
  for (const [route, port] of [
    ["openapi.json", "4000"],
    [`drafts/${draftId}/openapi.json`, "3001"],
  ]) {
    const response = await call(
      route,
      "GET",
      undefined,
      async (url) => {
        assert.equal(url.port, port);
        assert.equal(url.searchParams.get("revision"), revision);
        return { ok: true, status: 200, json: async () => ({ paths: {} }) };
      },
      { revision },
    );
    assert.equal(response.status, 200);
    assert.equal(response.headers["Cache-Control"], "no-store");
    assert.equal(
      (
        await call(
          route,
          "GET",
          undefined,
          () => assert.fail("invalid revision forwarded"),
          { revision: "bad" },
        )
      ).status,
      400,
    );
  }
});

test("Swagger HTML points at the browser-visible schema URL", async () => {
  const route = `drafts/${draftId}/docs`;
  const result = await call(route, "GET", undefined, async (url) => {
    assert.equal(url.port, "3001");
    return {
      ok: true,
      status: 200,
      text: async () =>
        `<script>url: 'http://127.0.0.1:3001/api/projects/${projectId}/drafts/${draftId}/openapi.json?revision=${revision}'</script>`,
    };
  });
  assert.equal(result.status, 200);
  assert.match(
    result.body,
    new RegExp(
      `url: '/api/projects/${projectId}/drafts/${draftId}/openapi.json\\?revision=${revision}'`,
    ),
  );
});

test("draft calls and database setup forward only reviewed fields", async () => {
  const cases = [
    [
      `drafts/${draftId}/call`,
      {
        expected_revision: revision,
        method: "POST",
        path: "/items",
        json_body: { name: "A" },
        headers: {},
      },
      {
        expected_revision: revision,
        method: "POST",
        path: "/items",
        json_body: { name: "A" },
        headers: {},
      },
    ],
    [
      `drafts/${draftId}/database/preview`,
      { expected_revision: revision },
      { expected_revision: revision },
    ],
    [
      `drafts/${draftId}/database/apply`,
      {
        expected_revision: revision,
        expected_schema: fingerprint,
        expected_preview_digest: digest,
        confirm: true,
      },
      {
        expected_revision: revision,
        expected_schema: fingerprint,
        expected_preview_digest: digest,
        confirm: true,
      },
    ],
  ];
  for (const [route, payload, expected] of cases) {
    const response = await call(
      route,
      "POST",
      { ...payload, api_key: "discard" },
      async (url, init) => {
        assert.equal(url.port, "3001");
        assert.deepEqual(JSON.parse(init.body), expected);
        return {
          ok: true,
          status: 200,
          json: async () => ({ status: "passed" }),
        };
      },
    );
    assert.equal(response.status, 200);
  }
  assert.equal(
    (
      await call(
        `drafts/${draftId}/database/apply`,
        "POST",
        {
          expected_revision: revision,
          expected_schema: fingerprint,
          expected_preview_digest: digest,
          confirm: false,
        },
        () => assert.fail("unconfirmed setup forwarded"),
      )
    ).status,
    400,
  );
});
