import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import test from "node:test";
import { nodeIdForStepPath, projectBlock, projectWorkflow } from "../../live/static/project.js";

const refundSteps = JSON.parse(
  readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../../workflows/refund/revision.json"), "utf8"),
).definition.steps;

test("linear call and awaitSignal projection", () => {
  const steps = [
    { id: "assess", call: {} },
    { id: "confirm", label: "Confirm it", awaitSignal: { signal: "confirm", result: "vars.ok" } },
  ];
  const { steps: nodes, transitions } = projectBlock(steps);
  assert.deepEqual(
    nodes.map((n) => n.id),
    ["__start", "assess", "confirm"],
  );
  assert.ok(transitions.some((e) => e.from === "__start" && e.to === "assess" && e.role === "entry"));
  assert.ok(transitions.some((e) => e.from === "assess" && e.to === "confirm" && e.role === "next"));
});

test("switch draws only case and default edges", () => {
  const steps = [
    { id: "pick", call: { next: "done" } },
    {
      id: "gate",
      switch: {
        cases: [{ when: "true", next: "yes" }],
        default: "no",
      },
      next: "done",
    },
    { id: "yes", return: {} },
    { id: "no", return: {} },
    { id: "done", end: {} },
  ];
  const { transitions } = projectBlock(steps);
  const fromGate = transitions.filter((e) => e.from === "gate");
  assert.equal(fromGate.length, 2);
  assert.ok(fromGate.every((e) => e.role === "case" || e.role === "default"));
  assert.ok(!transitions.some((e) => e.from === "gate" && e.to === "done" && e.role === "next"));
});

test("nodeIdForStepPath mappings", () => {
  const linear = [{ id: "confirm", awaitSignal: { signal: "confirm", result: "x" } }];
  assert.equal(nodeIdForStepPath(linear, "confirm"), "confirm");
  assert.equal(nodeIdForStepPath(linear, "missing"), null);
  const foreachSteps = [
    {
      id: "batch",
      for_each: {
        steps: [{ id: "ask", label: "Ask", awaitSignal: { signal: "confirm", result: "vars.x" } }],
      },
    },
  ];
  assert.equal(nodeIdForStepPath(foreachSteps, "batch[0]/ask"), "ask");
  const parallelSteps = [
    {
      id: "split",
      parallel: {
        branches: [
          {
            id: "a",
            steps: [{ id: "ask", label: "Ask a", awaitSignal: { signal: "confirm", result: "vars.x" } }],
          },
          {
            id: "b",
            steps: [{ id: "ask", label: "Ask b", awaitSignal: { signal: "confirm", result: "vars.y" } }],
          },
        ],
      },
    },
  ];
  assert.equal(nodeIdForStepPath(parallelSteps, "split.a/ask"), "ask");
  assert.equal(nodeIdForStepPath(parallelSteps, "split.b/ask"), "ask:1");
});

test("nodeIdForStepPath refund workflow awaits", () => {
  const cases = {
    confirm: "confirm",
    review_notice: "review_notice",
    review_choice: "review_choice",
    email_notice: "email_notice",
    transfer_notice: "transfer_notice",
    transfer_failed_notice: "transfer_failed_notice",
  };
  for (const [stepPath, expected] of Object.entries(cases)) {
    assert.equal(nodeIdForStepPath(refundSteps, stepPath), expected);
  }
  assert.equal(nodeIdForStepPath(refundSteps, "missing-step"), null);
});

test("projectWorkflow confirm node", () => {
  const { nodes } = projectWorkflow([
    { id: "confirm", label: "Confirm it", awaitSignal: { signal: "confirm", result: "vars.ok" } },
  ]);
  const confirm = nodes.find((n) => n.id === "confirm");
  assert.equal(confirm.icon, "flag");
  assert.equal(confirm.caption, "Confirm it");
});