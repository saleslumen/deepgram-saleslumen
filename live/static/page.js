import { mountGraph } from "./draw.js";
import { nodeIdForStepPath, projectWorkflow } from "./project.js";

const statusEl = document.getElementById("status");
const executionEl = document.getElementById("execution");
const transcriptEl = document.getElementById("transcript");
const graphsEl = document.getElementById("graphs");
const pipecatSurface = document.getElementById("pipecat-surface");
const saleslumenSurface = document.getElementById("saleslumen-surface");
const divider = document.getElementById("graphs-divider");
const transcriptDivider = document.getElementById("transcript-divider");
const mainEl = document.querySelector("main");
const minPane = 80;
const minTranscript = 260;

let pipecatGraph = null;
let saleslumenGraph = null;
let workflowsMap = {};
let currentWorkflowName = null;
let workflowSteps = [];
const saleslumenNodes = new Map();
let saleslumenVisited = new Set();
let saleslumenActiveId = null;
let pipecatVisited = new Set();
let pipecatActiveId = null;
let pipecatCaptionNodeId = null;
let graphsFailed = false;
let callStatus = "Waiting for a call";

const setVisibleStatus = (text) => {
  if (!graphsFailed) statusEl.textContent = text;
};

const firstWorkflowName = () => Object.keys(workflowsMap)[0] ?? null;

const mountSaleslumen = (steps, workflowName) => {
  workflowSteps = steps ?? [];
  currentWorkflowName = workflowName ?? null;
  const projected = projectWorkflow(workflowSteps);
  saleslumenNodes.clear();
  for (const node of projected.nodes) saleslumenNodes.set(node.id, node);
  saleslumenGraph?.destroy?.();
  saleslumenVisited = new Set();
  saleslumenActiveId = null;
  if (projected.nodes.length) {
    saleslumenGraph = mountGraph(saleslumenSurface, projected);
  } else {
    saleslumenGraph = null;
    saleslumenSurface.replaceChildren();
  }
};

const redrawFirstWorkflow = () => {
  const name = firstWorkflowName();
  if (name) mountSaleslumen(workflowsMap[name], name);
  else {
    workflowSteps = [];
    currentWorkflowName = null;
    saleslumenGraph?.destroy?.();
    saleslumenGraph = null;
    saleslumenSurface.replaceChildren();
    saleslumenNodes.clear();
    saleslumenVisited = new Set();
    saleslumenActiveId = null;
  }
};

const setPipecatCaption = (text) => {
  if (!pipecatCaptionNodeId) return;
  pipecatGraph?.setCaption(pipecatCaptionNodeId, text);
};

const loadGraphs = async () => {
  try {
    const response = await fetch("/live/graphs");
    if (!response.ok) throw new Error(`graphs HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload?.pipecat?.nodes || !payload.workflows || typeof payload.workflows !== "object") {
      throw new Error("graphs payload unusable");
    }
    graphsFailed = false;
    workflowsMap = payload.workflows;
    pipecatCaptionNodeId = payload.pipecat.nodes.find((node) => node.kind === "workflow_step")?.id ?? null;
    pipecatGraph?.destroy?.();
    if (payload.pipecat.nodes.length) {
      pipecatGraph = mountGraph(pipecatSurface, payload.pipecat);
    } else {
      pipecatGraph = null;
      pipecatSurface.replaceChildren();
    }
    const firstName = firstWorkflowName();
    if (firstName) mountSaleslumen(workflowsMap[firstName], firstName);
    else mountSaleslumen([], null);
    setVisibleStatus(callStatus);
  } catch (error) {
    graphsFailed = true;
    statusEl.textContent = "Graphs unavailable";
    workflowsMap = {};
    workflowSteps = [];
    currentWorkflowName = null;
    console.error(error);
  }
};

const scrollTranscript = () => {
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
};

const clearCallUi = () => {
  transcriptEl.replaceChildren();
  pipecatVisited = new Set();
  pipecatActiveId = null;
  pipecatGraph?.setActive(null);
  pipecatGraph?.setVisited([]);
  setPipecatCaption("");
  executionEl.textContent = "";
  redrawFirstWorkflow();
};

const ensureWorkflow = (workflowName) => {
  if (!workflowName || !workflowsMap[workflowName] || workflowName === currentWorkflowName) return;
  mountSaleslumen(workflowsMap[workflowName], workflowName);
};

const handleEvent = (event) => {
  if (!event?.kind) return;
  if (event.kind === "call_started") {
    callStatus = "Live";
    setVisibleStatus("Live");
    clearCallUi();
    return;
  }
  if (event.kind === "call_ended") {
    callStatus = "Ended";
    setVisibleStatus("Ended");
    return;
  }
  if (event.kind === "transcript") {
    const item = document.createElement("li");
    item.className = event.role === "user" ? "user" : "assistant";
    item.textContent = event.text ?? "";
    transcriptEl.appendChild(item);
    scrollTranscript();
    return;
  }
  if (event.kind === "flow_node") {
    const id = typeof event.graph_node === "string" ? event.graph_node : "";
    if (!id) {
      pipecatGraph?.setActive(null);
      pipecatActiveId = null;
      return;
    }
    if (pipecatActiveId && pipecatActiveId !== id) {
      pipecatVisited.add(pipecatActiveId);
      pipecatGraph?.travel(pipecatActiveId, id);
    }
    pipecatActiveId = id;
    pipecatGraph?.setActive(id);
    pipecatGraph?.setVisited([...pipecatVisited]);
    return;
  }
  if (event.kind === "workflow_step") {
    ensureWorkflow(event.workflow);
    const execId = event.execution_id ?? "";
    if (event.state === "awaiting") {
      executionEl.textContent = execId;
      const nodeId = nodeIdForStepPath(workflowSteps, event.step_path ?? "");
      if (saleslumenActiveId && saleslumenActiveId !== nodeId) saleslumenVisited.add(saleslumenActiveId);
      saleslumenActiveId = nodeId;
      saleslumenGraph?.setActive(nodeId);
      saleslumenGraph?.setVisited([...saleslumenVisited]);
      const caption = nodeId ? saleslumenNodes.get(nodeId)?.caption ?? "" : "";
      setPipecatCaption(caption);
      return;
    }
    if (event.state === "running" || event.state === "succeeded" || event.state === "failed") {
      saleslumenActiveId = null;
      saleslumenGraph?.setActive(null);
      setPipecatCaption("");
      const stateWord = event.state.charAt(0).toUpperCase() + event.state.slice(1);
      executionEl.textContent = execId ? `${execId} · ${stateWord}` : stateWord;
    }
  }
};

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

const bindDivider = (handle, place) => {
  handle.addEventListener("pointerdown", (ev) => handle.setPointerCapture(ev.pointerId));
  handle.addEventListener("pointermove", (ev) => {
    if (handle.hasPointerCapture(ev.pointerId)) place(ev);
  });
};

const bindDividers = () => {
  bindDivider(divider, (ev) => {
    const box = graphsEl.getBoundingClientRect();
    const top = clamp(ev.clientY - box.top, minPane, box.height - divider.offsetHeight - minPane);
    graphsEl.style.setProperty("--graphs-top", `${top}px`);
  });
  bindDivider(transcriptDivider, (ev) => {
    const box = mainEl.getBoundingClientRect();
    const width = clamp(box.right - ev.clientX, minTranscript, box.width - transcriptDivider.offsetWidth - minPane);
    mainEl.style.setProperty("--transcript-width", `${width}px`);
  });
};

const bindEvents = () => {
  const source = new EventSource("/live/events");
  source.onopen = () => {
    if (!graphsFailed) setVisibleStatus(callStatus);
  };
  source.onerror = (error) => {
    if (!graphsFailed) statusEl.textContent = "Reconnecting";
    console.error(error);
  };
  source.onmessage = (message) => {
    try {
      handleEvent(JSON.parse(message.data));
    } catch (error) {
      console.error(error);
    }
  };
};

loadGraphs().finally(() => {
  bindDividers();
  bindEvents();
});