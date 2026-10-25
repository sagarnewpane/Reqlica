const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("../frontend/node_modules/typescript");
const projectId = "a".repeat(32),
  runId = "b".repeat(32);
function load(file, extra = {}) {
  const exports = {};
  const js = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(js, {
    exports,
    URL,
    AbortSignal,
    process: { env: {} },
    ...extra,
  });
  return exports;
}
async function call(path, method, fetch, headers = {}, id = projectId) {
  const handler = load(
    "frontend/src/pages/api/projects/[projectId]/[...path].ts",
    { fetch },
  ).default;
  const result = {};
  const res = {
    setHeader() {},
    status(status) {
      result.status = status;
      return this;
    },
    json(body) {
      result.body = body;
      return this;
    },
  };
  await handler(
    {
      query: { projectId: id, path },
      method,
      headers: {
        host: "localhost:3000",
        "content-type": "application/json",
        ...headers,
      },
      body: { message: "Hello" },
    },
    res,
  );
  return result;
}
const success = (data = {}) => ({
  ok: true,
  status: 200,
  json: async () => data,
});
test("project endpoints use runtime; history uses console", async () => {
  for (const [path, port] of [
    [["endpoints"], "4000"],
    [["agent-runs"], "3001"],
  ]) {
    const result = await call(path, "GET", async (url) => {
      assert.equal(url.port, port);
      assert.match(url.pathname, new RegExp(projectId));
      return success();
    });
    assert.equal(result.status, 200);
  }
});
test("chat mutations preserve the idempotent run and reply payload", async () => {
  for (const [path, method] of [
    [["agent-runs", runId], "PUT"],
    [["agent-runs", runId, "replies"], "POST"],
  ]) {
    const result = await call(path, method, async (url, init) => {
      assert.match(url.pathname, new RegExp(runId));
      assert.equal(init.method, method);
      assert.equal(JSON.parse(init.body).message, "Hello");
      return success();
    });
    assert.equal(result.status, 200);
  }
});
test("reasoning reset is forwarded to the saved conversation", async () => {
  const result = await call(
    ["agent-runs", runId, "reasoning-default"],
    "POST",
    async (url, init) => {
      assert.match(url.pathname, /reasoning-default$/);
      assert.equal(init.method, "POST");
      return success();
    },
  );
  assert.equal(result.status, 200);
});
test("interrupted conversation recovery is a scoped action without a forwarded key", async () => {
  const result = await call(
    ["agent-runs", runId, "recover"],
    "POST",
    async (url, init) => {
      assert.equal(url.port, "3001");
      assert.match(url.pathname, /\/recover$/);
      assert.equal(init.headers["X-Reqlica-Groq-Key"], undefined);
      return success({ status: "ready" });
    },
    { "x-reqlica-groq-key": "private" },
  );
  assert.equal(result.status, 200);
});
test("keys forwarded only to a step and redirects rejected", async () => {
  for (const [path, method, expected] of [
    [["agent-runs", runId, "step"], "POST", "test-only"],
    [["agent-runs"], "GET", undefined],
  ]) {
    await call(
      path,
      method,
      async (_, init) => {
        assert.equal(init.headers["X-Reqlica-Groq-Key"], expected);
        assert.equal(init.redirect, "error");
        return success();
      },
      { "x-reqlica-groq-key": "test-only" },
    );
  }
});
test("reject arbitrary paths, invalid IDs and cross-origin mutations", async () => {
  const never = () => assert.fail("must not contact backend");
  for (const path of [
    ["..", "secret"],
    ["drafts", runId, "publish"],
    ["agent-runs", runId, "context"],
  ])
    assert.equal((await call(path, "POST", never)).status, 405);
  assert.equal(
    (await call(["endpoints"], "GET", never, {}, "../other")).status,
    400,
  );
  assert.equal(
    (
      await call(["agent-runs", runId], "PUT", never, {
        origin: "https://external.example",
      })
    ).status,
    403,
  );
});
test("provider errors and unconfirmed responses are actionable", async () => {
  const rejected = await call(
    ["agent-runs", runId, "step"],
    "POST",
    async () => ({
      ok: false,
      status: 503,
      json: async () => ({ detail: { message: "Configure a provider key" } }),
    }),
  );
  assert.equal(rejected.body.error, "Configure a provider key");
  const lost = await call(["agent-runs", runId, "step"], "POST", async () => {
    throw Error("lost");
  });
  assert.equal(lost.status, 502);
  assert.match(lost.body.error, /Refresh the conversation before retrying/);
});
test("transcript keeps tool outcomes between the messages they belong to", () => {
  const { conversationMessages } = load("frontend/src/lib/chat.ts");
  const result = conversationMessages({
    message: "First question",
    events: [
      { kind: "tool", name: "get_project_context" },
      { kind: "assistant", text: "First answer" },
      { kind: "user", text: "Follow-up" },
    ],
    answer: "Second answer",
  });
  assert.equal(
    JSON.stringify(result),
    JSON.stringify([
      { kind: "user", text: "First question" },
      { kind: "tool", name: "get_project_context" },
      { kind: "assistant", text: "First answer" },
      { kind: "user", text: "Follow-up" },
      { kind: "assistant", text: "Second answer" },
    ]),
  );
});
test("environment edits stay scoped and package operations carry reviewed UI marker", async () => {
  for (const [path, method] of [
    [["environment", "SERVICE_URL"], "PUT"],
    [["environment", "SERVICE_URL"], "DELETE"],
    [["packages", "preview"], "POST"],
    [["packages", "jobs", runId, "install"], "POST"],
  ]) {
    const result = await call(path, method, async (url, init) => {
      assert.equal(url.port, "3001");
      if (path[0] === "packages")
        assert.equal(init.headers["X-Reqlica-Package-Action"], "reviewed-ui");
      return success();
    });
    assert.equal(result.status, 200);
  }
});
