export const LABELS = {
  call: "Call",
  assign: "Assign",
  switch: "Switch",
  wait: "Wait",
  awaitSignal: "Wait for signal",
  parallel: "Parallel",
  for_each: "For each",
  callWorkflow: "Run workflow",
  agent: "AI Agent",
  return: "Return",
  fail: "Raise error",
  end: "End",
  interrupt: "Interrupt",
  newCycle: "Start next cycle",
};
export const ICONS = {
  call: "task",
  assign: "settings",
  switch: "call_split",
  wait: "schedule",
  awaitSignal: "flag",
  parallel: "account_tree",
  for_each: "loop",
  callWorkflow: "alt_route",
  agent: "auto_awesome",
  fail: "error_outline",
  invalid: "warning",
};
const KINDS = Object.keys(LABELS);
const START_ID = "__start";
const stepKind = (step) => KINDS.find((kind) => step[kind] !== undefined) || "";
const uniqueViewId = (used, preferred) => {
  let id = preferred;
  let suffix = 1;
  while (used.has(id)) {
    id = `${preferred}:${suffix}`;
    suffix += 1;
  }
  used.add(id);
  return id;
};
/**
 * end, return, fail, interrupt, and newCycle draw no successor, including when next is set. The End node is the word End in the circle.
 * A switch draws only its cases and its default. Its own next is not an edge, and it does not fall through to the next step in the list.
 * onError is a solid edge. Only a parallel branch is dashed.
 */
export function projectBlock(steps) {
  const used = new Set([START_ID]);
  const projected = [{ id: START_ID, kind: "start", label: "Start", authoredLabel: "" }];
  const transitions = [];
  const projectSteps = (block, entry) => {
    const viewIds = [];
    const authoredView = new Map();
    const localIds = new Set();
    (block || []).forEach((step, index) => {
      const authoredId = step.id ?? "";
      const validId = Boolean(authoredId) && authoredId !== START_ID && !localIds.has(authoredId);
      if (authoredId) localIds.add(authoredId);
      const id = uniqueViewId(used, validId ? authoredId : `__view:${index}`);
      const kind = stepKind(step) || "invalid";
      const authoredLabel = typeof step.label === "string" ? step.label.trim() : "";
      viewIds.push(id);
      if (validId) authoredView.set(authoredId, id);
      projected.push({
        id,
        kind,
        label: kind === "invalid" ? "Invalid" : authoredLabel || LABELS[kind] || "Invalid",
        authoredLabel,
      });
    });
    if (entry && viewIds[0]) transitions.push({ from: entry.source, to: viewIds[0], label: entry.label, role: entry.role });
    const resolve = (target) => (target ? authoredView.get(target) : undefined);
    (block || []).forEach((step, index) => {
      const id = viewIds[index];
      const kind = projected.find((item) => item.id === id)?.kind;
      const terminal = kind === "return" || kind === "fail" || kind === "end" || kind === "interrupt" || kind === "newCycle";
      const successor = terminal || kind === "switch" ? undefined : step.next ? resolve(step.next) : viewIds[index + 1];
      if (successor) {
        const role = kind === "for_each" ? "after" : "next";
        transitions.push({ from: id, to: successor, label: role === "after" ? "after" : "", role });
      }
      (step.switch?.cases || []).forEach((item, caseIndex) => {
        const target = resolve(item.next);
        const when = (item.when ?? "").trim();
        if (target) transitions.push({ from: id, to: target, label: when === "true" ? "True" : `Case ${caseIndex + 1}`, role: "case" });
      });
      const fallback = resolve(step.switch?.default);
      if (fallback) transitions.push({ from: id, to: fallback, label: "default", role: "default" });
      const caught = resolve(
        step.call?.onError?.next ||
          step.awaitSignal?.onError?.next ||
          step.parallel?.onError?.next ||
          step.for_each?.onError?.next ||
          step.callWorkflow?.onError?.next ||
          step.newCycle?.onError?.next,
      );
      if (caught) transitions.push({ from: id, to: caught, label: "error", role: "error" });
      if (kind === "parallel") {
        (step.parallel?.branches || []).forEach((branch) => {
          if (!branch.id) return;
          projectSteps(branch.steps || [], { source: id, role: "parallel", label: branch.id });
        });
      }
      if (kind === "for_each") projectSteps(step.for_each?.steps || [], { source: id, role: "loop", label: "loop" });
    });
  };
  projectSteps(steps || [], { source: START_ID, role: "entry", label: "" });
  return { steps: projected, transitions };
}
const joinPath = (parent, child) => (parent === "" ? child : `${parent}/${child}`);
const INDEX_RE = /^(.+)\[(\d+)\]$/;
const samePath = (template, runtime) => {
  const left = template.split("/");
  const right = runtime.split("/");
  if (left.length !== right.length) return false;
  for (let i = 0; i < left.length; i += 1) {
    const item = left[i];
    const live = right[i];
    if (item === live) continue;
    const matched = INDEX_RE.exec(live);
    if (item.endsWith("[]") && matched !== null && matched[1] === item.slice(0, -2)) continue;
    return false;
  }
  return true;
};
const collectAwaitPaths = (block, frame, used, matches) => {
  if (!block?.length) return;
  const viewIds = [];
  const localIds = new Set();
  block.forEach((step, index) => {
    const authoredId = typeof step?.id === "string" ? step.id : "";
    const valid = Boolean(authoredId) && authoredId !== START_ID && !localIds.has(authoredId);
    if (authoredId) localIds.add(authoredId);
    viewIds.push(uniqueViewId(used, valid ? authoredId : `__view:${index}`));
  });
  block.forEach((step, index) => {
    if (!step || typeof step !== "object") return;
    const viewId = viewIds[index];
    const stepId = step.id;
    if (typeof stepId !== "string" || stepId === "") return;
    if (step.awaitSignal !== undefined) matches.push([viewId, joinPath(frame, stepId)]);
    if (step.parallel?.branches) {
      step.parallel.branches.forEach((branch) => {
        if (!branch?.id) return;
        collectAwaitPaths(branch.steps || [], joinPath(frame, `${stepId}.${branch.id}`), used, matches);
      });
    }
    if (step.for_each?.steps) {
      collectAwaitPaths(step.for_each.steps, joinPath(frame, `${stepId}[]`), used, matches);
    }
  });
};
export function nodeIdForStepPath(steps, stepPath) {
  const used = new Set([START_ID]);
  const matches = [];
  collectAwaitPaths(steps || [], "", used, matches);
  const found = matches.filter(([, path]) => samePath(path, stepPath)).map(([viewId]) => viewId);
  if (found.length !== 1) return null;
  return found[0];
}
const ringLabel = (step) => {
  if (step.kind === "start") return "Start";
  if (step.kind === "return") return "Return";
  if (step.kind === "fail") return LABELS.fail;
  if (step.kind === "end") return "End";
  if (step.kind === "interrupt") return "Interrupt";
  if (step.kind === "newCycle") return "Start next cycle";
  if (step.kind === "invalid") return "Invalid";
  return LABELS[step.kind] || step.label;
};
export function projectWorkflow(steps) {
  const { steps: projected, transitions } = projectBlock(steps);
  const nodes = projected.map((step) => ({
    id: step.id,
    kind: step.kind,
    label: ringLabel(step),
    caption: step.kind === "start" ? "" : step.authoredLabel || "",
    icon: ICONS[step.kind] || "",
  }));
  const edges = transitions.map((t) => ({ from: t.from, to: t.to, label: t.label, role: t.role }));
  return { nodes, edges };
}