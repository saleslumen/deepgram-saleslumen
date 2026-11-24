export const STEP_HEIGHT = 80;
export const VERTICAL_SPACING = 40;
export const LEVEL_SPACING = 250;
export const CANVAS_PADDING = 40;
export const MIN_SCALE = 0.2;
export const MAX_SCALE = 2;
const TERMINAL_RING = new Set(["start", "return", "end", "interrupt", "newCycle"]);
const escapeHtml = (value) =>
  String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
export function calculateWorkflowLayout(nodes, edges) {
  const positions = new Map();
  if (!nodes.length) return positions;
  const children = new Map(nodes.map((step) => [step.id, []]));
  edges.forEach((transition) => {
    const list = children.get(transition.from);
    if (!list || !children.has(transition.to) || list.includes(transition.to)) return;
    list.push(transition.to);
  });
  const order = new Map(nodes.map((step, index) => [step.id, index]));
  const edgeKey = (from, to) => `${from}\0${to}`;
  const backEdges = new Set();
  const visiting = new Set();
  const visited = new Set();
  const findBackEdges = (id) => {
    if (visited.has(id)) return;
    visiting.add(id);
    for (const child of children.get(id) || []) {
      if (visiting.has(child)) backEdges.add(edgeKey(id, child));
      else findBackEdges(child);
    }
    visiting.delete(id);
    visited.add(id);
  };
  for (const step of nodes) findBackEdges(step.id);
  const indegree = new Map(nodes.map((step) => [step.id, 0]));
  for (const [from, targets] of children) {
    for (const to of targets) {
      if (!backEdges.has(edgeKey(from, to))) indegree.set(to, (indegree.get(to) ?? 0) + 1);
    }
  }
  const rootIds = nodes
    .filter((step) => (indegree.get(step.id) ?? 0) === 0)
    .sort((a, b) => (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0))
    .map((step) => step.id);
  const ready = [...rootIds];
  const level = new Map();
  for (const id of rootIds) level.set(id, 0);
  while (ready.length) {
    const id = ready.shift();
    const depth = level.get(id);
    for (const child of children.get(id) || []) {
      if (backEdges.has(edgeKey(id, child))) continue;
      if (depth !== undefined) level.set(child, Math.max(level.get(child) ?? 0, depth + 1));
      const remaining = (indegree.get(child) ?? 0) - 1;
      indegree.set(child, remaining);
      if (remaining === 0) {
        ready.push(child);
        ready.sort((left, right) => (order.get(left) ?? 0) - (order.get(right) ?? 0));
      }
    }
  }
  const height = (id, trail) => {
    if (trail.has(id)) return STEP_HEIGHT;
    const next = children.get(id) || [];
    if (!next.length) return STEP_HEIGHT;
    trail.add(id);
    const total = next.reduce((sum, child) => sum + height(child, trail), 0);
    trail.delete(id);
    return Math.max(STEP_HEIGHT, total + (next.length - 1) * VERTICAL_SPACING);
  };
  const place = (id, y, trail) => {
    if (positions.has(id) || trail.has(id)) return;
    trail.add(id);
    const depth = level.get(id) ?? 0;
    const next = (children.get(id) || []).filter((child) => level.has(child));
    if (!next.length) {
      positions.set(id, { x: depth * LEVEL_SPACING, y });
      trail.delete(id);
      return;
    }
    let cursor = y;
    const childTops = [];
    for (const child of next) {
      childTops.push({ id: child, y: cursor });
      cursor += height(child, new Set(trail)) + VERTICAL_SPACING;
    }
    const first = childTops[0].y;
    const last = childTops[childTops.length - 1];
    const lastBottom = last.y + height(last.id, new Set(trail));
    positions.set(id, { x: depth * LEVEL_SPACING, y: first + (lastBottom - first - STEP_HEIGHT) / 2 });
    for (const child of childTops) place(child.id, child.y, trail);
    trail.delete(id);
  };
  let rootY = 0;
  for (const rootId of rootIds) {
    if (positions.has(rootId)) continue;
    place(rootId, rootY, new Set());
    if (positions.has(rootId)) {
      const pos = positions.get(rootId);
      rootY = pos.y + height(rootId, new Set()) + VERTICAL_SPACING;
    }
  }
  let overflow = 0;
  for (const step of nodes) {
    if (positions.has(step.id)) continue;
    positions.set(step.id, { x: LEVEL_SPACING, y: overflow });
    overflow += STEP_HEIGHT + VERTICAL_SPACING;
  }
  return positions;
}
export function calculateColumnNodePositions(columns, spacing = 16) {
  const measured = columns.map((column) => [...column].sort((left, right) => left.top - right.top));
  const heights = measured.map(
    (column) => column.reduce((sum, node) => sum + node.topInset + node.bottomExtent, 0) + Math.max(0, column.length - 1) * spacing,
  );
  const maxHeight = Math.max(0, ...heights);
  const positions = new Map();
  measured.forEach((column, columnIndex) => {
    let cursor = (maxHeight - heights[columnIndex]) / 2;
    for (const node of column) {
      positions.set(node.id, cursor + node.topInset);
      cursor += node.topInset + node.bottomExtent + spacing;
    }
  });
  return positions;
}
export function bezierPath(sx, sy, tx, ty) {
  const controlOffset = (distance) => (distance >= 0 ? 0.5 * distance : 0.25 * 25 * Math.sqrt(-distance));
  const scx = sx + controlOffset(tx - sx);
  const tcx = tx - controlOffset(tx - sx);
  return {
    d: `M${sx},${sy} C${scx},${sy} ${tcx},${ty} ${tx},${ty}`,
    labelX: 0.125 * sx + 0.375 * scx + 0.375 * tcx + 0.125 * tx,
    labelY: 0.125 * sy + 0.375 * sy + 0.375 * ty + 0.125 * ty,
  };
}
export const TRAVEL_SEGMENT = 28;
export const TRAVEL_MS = 900;
export function travelOffsets(length, reverse) {
  const offsets = [TRAVEL_SEGMENT, -length];
  return reverse ? offsets.reverse() : offsets;
}
export function zoomView(view, x, y, factor) {
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, view.scale * factor));
  const ratio = scale / view.scale;
  return { x: x - (x - view.x) * ratio, y: y - (y - view.y) * ratio, scale };
}
const curvedLabelHtml = (label) => {
  const letters = label.split("");
  const radius = 50;
  const letterSpacing = 12;
  const totalAngularWidth = (((letters.length - 1) * letterSpacing) / radius) * (180 / Math.PI);
  const startAngle = -90 - totalAngularWidth / 2;
  return letters
    .map((letter, index) => {
      const angle = (startAngle + ((index * letterSpacing) / radius) * (180 / Math.PI)) * (Math.PI / 180);
      const x = radius * Math.cos(angle);
      const y = radius * Math.sin(angle);
      return `<span class="letter" style="transform:translate(${x - 4}px, ${y - 8}px) rotate(${angle + Math.PI / 2}rad)">${escapeHtml(letter)}</span>`;
    })
    .join("");
};
const iconSpan = (glyph) => (glyph ? `<span class="material-symbols-outlined icon" aria-hidden="true">${escapeHtml(glyph)}</span>` : "");
const nodeBodyHtml = (node) => {
  if (node.kind === "start" || node.kind === "return" || node.kind === "end" || node.kind === "interrupt" || node.kind === "newCycle") {
    return escapeHtml(node.label);
  }
  if (node.kind === "fail" || node.kind === "invalid") {
    return `${iconSpan(node.icon)}${escapeHtml(node.label)}`;
  }
  return `${iconSpan(node.icon)}`;
};
const ringHtml = (node) => {
  if (TERMINAL_RING.has(node.kind) || node.kind === "fail" || node.kind === "invalid") return "";
  return `<div class="ring">${curvedLabelHtml(node.label)}</div>`;
};
export function mountGraph(container, graph) {
  const positions = calculateWorkflowLayout(graph.nodes, graph.edges);
  const paths = graph.edges
    .map(
      (edge, index) =>
        `<path data-edge="${index}" data-from="${escapeHtml(edge.from)}" data-to="${escapeHtml(edge.to)}" class="edge stroke-${escapeHtml(edge.role)}"/>`,
    )
    .join("");
  const labels = graph.edges
    .map((edge, index) => {
      if (!edge.label) return "";
      return `<g class="edge-label label-${escapeHtml(edge.role)}" data-edge="${index}"><rect x="-2" y="-4" rx="2" ry="2" class="edge-label-bg"/><text dy="0.3em">${escapeHtml(edge.label)}</text></g>`;
    })
    .join("");
  const cards = graph.nodes
    .filter((node) => positions.has(node.id))
    .map((node) => {
      const pos = positions.get(node.id);
      const compact = node.kind === "return" || node.kind === "end";
      const caption = node.caption ? `<div class="caption">${escapeHtml(node.caption)}</div>` : "";
      return `<div class="node" data-id="${escapeHtml(node.id)}" style="left:${pos.x}px;top:${pos.y}px"><div class="circle${compact ? " compact" : ""}">${nodeBodyHtml(node)}${ringHtml(node)}</div>${caption}</div>`;
    })
    .join("");
  container.replaceChildren();
  const canvas = document.createElement("div");
  canvas.className = "canvas";
  canvas.innerHTML = `<svg class="edges" xmlns="http://www.w3.org/2000/svg">${paths}${labels}</svg>${cards}`;
  canvas.style.transformOrigin = "0 0";
  container.appendChild(canvas);
  let resizeObserver;
  let view = null;
  let moved = false;
  let drag = null;
  const listeners = new AbortController();
  const applyView = () => {
    canvas.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
  };
  const layoutPreview = () => {
    canvas.style.transform = "none";
    const origin = canvas.getBoundingClientRect();
    const columns = new Map();
    canvas.querySelectorAll(".node").forEach((node) => {
      const circle = node.querySelector(".circle").getBoundingClientRect();
      const bounds = node.getBoundingClientRect();
      const ring = node.querySelector(".ring")?.getBoundingClientRect();
      const letters = [...node.querySelectorAll(".ring .letter")].map((letter) => letter.getBoundingClientRect());
      const top = Math.min(bounds.top, ring?.top ?? bounds.top, ...letters.map((letter) => letter.top));
      const bottom = Math.max(bounds.bottom, ring?.bottom ?? bounds.bottom, ...letters.map((letter) => letter.bottom));
      const column = node.style.left;
      if (!columns.has(column)) columns.set(column, []);
      columns.get(column).push({ id: node.dataset.id, top: Number.parseFloat(node.style.top), topInset: circle.top - top, bottomExtent: bottom - circle.top });
    });
    const nodePositions = calculateColumnNodePositions([...columns.values()]);
    canvas.querySelectorAll(".node").forEach((node) => {
      node.style.top = `${nodePositions.get(node.dataset.id)}px`;
    });
    const circles = new Map(
      [...canvas.querySelectorAll(".node")].map((node) => {
        const rect = node.querySelector(".circle").getBoundingClientRect();
        return [
          node.dataset.id,
          { left: rect.left - origin.left, right: rect.right - origin.left, top: rect.top - origin.top, y: rect.top - origin.top + rect.height / 2 },
        ];
      }),
    );
    canvas.querySelectorAll(".edges path").forEach((path) => {
      const from = circles.get(path.dataset.from);
      const to = circles.get(path.dataset.to);
      if (!from || !to) return;
      const curve = bezierPath(from.right, from.y, to.left, to.y);
      path.setAttribute("d", curve.d);
      const label = canvas.querySelector(`.edge-label[data-edge="${path.dataset.edge}"]`);
      if (!label) return;
      const text = label.querySelector("text");
      const background = label.querySelector("rect");
      const textBounds = text.getBBox();
      label.setAttribute("transform", `translate(${curve.labelX - textBounds.width / 2} ${curve.labelY - textBounds.height / 2})`);
      text.setAttribute("y", textBounds.height / 2);
      background.setAttribute("width", textBounds.width + 4);
      background.setAttribute("height", textBounds.height + 8);
    });
    const bounds = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
    canvas.querySelectorAll(".node, .edges path, .edge-label").forEach((element) => {
      const rect = element.getBoundingClientRect();
      bounds.minX = Math.min(bounds.minX, rect.left - origin.left);
      bounds.minY = Math.min(bounds.minY, rect.top - origin.top);
      bounds.maxX = Math.max(bounds.maxX, rect.right - origin.left);
      bounds.maxY = Math.max(bounds.maxY, rect.bottom - origin.top);
    });
    if (!Number.isFinite(bounds.minX)) return;
    const contentW = bounds.maxX - bounds.minX + 2 * CANVAS_PADDING;
    const contentH = bounds.maxY - bounds.minY + 2 * CANVAS_PADDING;
    const availW = container.clientWidth;
    const availH = container.clientHeight;
    const scale = Math.min(1, availW / contentW, availH / contentH) || 1;
    const tx = CANVAS_PADDING - bounds.minX * scale + Math.max(0, (availW - contentW * scale) / 2);
    const ty = CANVAS_PADDING - bounds.minY * scale + Math.max(0, (availH - contentH * scale) / 2);
    if (!moved) view = { x: tx, y: ty, scale };
    applyView();
  };
  const resize = () => {
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(layoutPreview);
    else layoutPreview();
  };
  const { signal } = listeners;
  container.addEventListener("pointerdown", (ev) => {
    if (!view || ev.button !== 0) return;
    drag = { x: ev.clientX - view.x, y: ev.clientY - view.y };
    container.setPointerCapture(ev.pointerId);
    container.classList.add("is-panning");
  }, { signal });
  container.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    moved = true;
    view = { ...view, x: ev.clientX - drag.x, y: ev.clientY - drag.y };
    applyView();
  }, { signal });
  const endDrag = () => {
    drag = null;
    container.classList.remove("is-panning");
  };
  container.addEventListener("pointerup", endDrag, { signal });
  container.addEventListener("pointercancel", endDrag, { signal });
  container.addEventListener("wheel", (ev) => {
    if (!view) return;
    ev.preventDefault();
    const origin = container.getBoundingClientRect();
    moved = true;
    view = zoomView(view, ev.clientX - origin.left, ev.clientY - origin.top, Math.exp(-ev.deltaY * 0.002));
    applyView();
  }, { passive: false, signal });
  container.addEventListener("dblclick", () => {
    moved = false;
    resize();
  }, { signal });
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(container);
  }
  if (typeof document !== "undefined" && document.fonts?.ready) document.fonts.ready.then(resize);
  else resize();
  return {
    travel(from, to) {
      if (!from || !to || globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
      const forward = canvas.querySelector(`.edges path[data-from="${CSS.escape(from)}"][data-to="${CSS.escape(to)}"]`);
      const edge = forward ?? canvas.querySelector(`.edges path[data-from="${CSS.escape(to)}"][data-to="${CSS.escape(from)}"]`);
      if (!edge?.getAttribute("d")) return;
      const length = edge.getTotalLength();
      const pulse = document.createElementNS("http://www.w3.org/2000/svg", "path");
      pulse.setAttribute("d", edge.getAttribute("d"));
      pulse.setAttribute("class", forward ? "travel" : "travel is-back");
      pulse.style.strokeDasharray = `${TRAVEL_SEGMENT} ${length}`;
      edge.parentNode.insertBefore(pulse, canvas.querySelector(".edges .edge-label"));
      const [start, end] = travelOffsets(length, !forward);
      pulse.animate([{ strokeDashoffset: start }, { strokeDashoffset: end }], { duration: TRAVEL_MS, easing: "ease-in-out" }).finished.then(() => pulse.remove());
    },
    setActive(id) {
      canvas.querySelectorAll(".node.is-active").forEach((node) => node.classList.remove("is-active"));
      if (!id) return;
      canvas.querySelector(`.node[data-id="${CSS.escape(id)}"]`)?.classList.add("is-active");
    },
    setVisited(ids) {
      const keep = new Set(ids);
      canvas.querySelectorAll(".node.is-visited").forEach((node) => {
        if (!keep.has(node.dataset.id)) node.classList.remove("is-visited");
      });
      for (const vid of keep) canvas.querySelector(`.node[data-id="${CSS.escape(vid)}"]`)?.classList.add("is-visited");
    },
    setCaption(id, text) {
      const node = canvas.querySelector(`.node[data-id="${CSS.escape(id)}"]`);
      if (!node) return;
      let caption = node.querySelector(".caption");
      const body = text ?? "";
      if (body) {
        if (!caption) {
          caption = document.createElement("div");
          caption.className = "caption";
          node.appendChild(caption);
        }
        caption.textContent = body;
      } else if (caption) caption.remove();
      resize();
    },
    resize,
    destroy() {
      resizeObserver?.disconnect();
      listeners.abort();
    },
  };
}