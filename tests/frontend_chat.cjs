// Server adapter coverage moved to test_dashboard.py. Browser helper tests remain here.
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
const success = (data = {}) => ({
  ok: true,
  status: 200,
  json: async () => data,
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
