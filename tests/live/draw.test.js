import assert from "node:assert/strict";
import test from "node:test";
import { bezierPath, calculateColumnNodePositions, calculateWorkflowLayout, MAX_SCALE, MIN_SCALE, TRAVEL_SEGMENT, travelOffsets, zoomView } from "../../live/static/draw.js";

test("zoomView keeps the point under the cursor fixed", () => {
  const view = zoomView({ x: 10, y: 20, scale: 1 }, 110, 70, 2);
  assert.deepEqual(view, { x: -90, y: -30, scale: 2 });
  assert.equal((110 - view.x) / view.scale, 110 - 10);
});

test("travelOffsets runs the pulse from the edge start, or from its end when reversed", () => {
  assert.deepEqual(travelOffsets(200, false), [TRAVEL_SEGMENT, -200]);
  assert.deepEqual(travelOffsets(200, true), [-200, TRAVEL_SEGMENT]);
});

test("zoomView clamps the scale", () => {
  assert.equal(zoomView({ x: 0, y: 0, scale: 1 }, 0, 0, 100).scale, MAX_SCALE);
  assert.equal(zoomView({ x: 0, y: 0, scale: 1 }, 0, 0, 0.001).scale, MIN_SCALE);
});

test("bezierPath horizontal segment", () => {
  const curve = bezierPath(0, 0, 100, 0);
  assert.equal(curve.d, "M0,0 C50,0 50,0 100,0");
  assert.equal(curve.labelX, 50);
  assert.equal(curve.labelY, 0);
});

test("calculateColumnNodePositions centers shorter column", () => {
  const node = { id: "solo", top: 0, topInset: 0, bottomExtent: 10 };
  const positions = calculateColumnNodePositions([[node], [{ ...node, id: "upper" }, { ...node, id: "pair" }]], 16);
  assert.ok(positions.get("solo") > 0);
  assert.equal(positions.get("upper"), 0);
  assert.ok(positions.get("pair") > positions.get("upper"));
});

test("calculateWorkflowLayout orders nodes left to right", () => {
  const positions = calculateWorkflowLayout([{ id: "a" }, { id: "b" }], [{ from: "a", to: "b", label: "", role: "next" }]);
  assert.ok(positions.get("b").x > positions.get("a").x);
});

test("calculateWorkflowLayout centers child on parent", () => {
  const positions = calculateWorkflowLayout(
    [{ id: "child" }, { id: "parent" }],
    [{ from: "parent", to: "child", label: "", role: "next" }],
  );
  assert.ok(positions.get("parent").x < positions.get("child").x);
  assert.equal(positions.get("parent").y, positions.get("child").y);
});