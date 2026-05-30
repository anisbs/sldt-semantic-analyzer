// Application script for web/index.html.
// Extracted verbatim from the former inline <script> block.
// Classic (non-module) script: runs at end of <body> in global
// scope, exactly as before. D3 is loaded in <head>.

const KIND_COLORS = {
  // Characteristic is an azure (#0288d1) rather than the old #4363d8, which was
  // the exact UI accent — a Characteristic node now reads distinct from the
  // accent-blue selection rings / links in the graph.
  Aspect: "#e6194b", Property: "#3cb44b", Characteristic: "#0288d1",
  Entity: "#f58231", AbstractEntity: "#911eb4", Operation: "#46f0f0",
  Event: "#f032e6", Constraint: "#bcf60c",
};
const color = k => KIND_COLORS[k] || "#999";

// Legend = filter. Toggling a checkbox hides nodes of that kind (and any
// link whose endpoint becomes hidden). Defaults match the kinds users
// typically want to see first.
const kindFilter = new Set(["Aspect", "Property", "Entity", "AbstractEntity"]);

const STATUS_COLORS = {
  release: "#2e7d32", deprecated: "#b00020", draft: "#f9a825", undefined: "#9e9e9e",
};
const statusColor = s => STATUS_COLORS[s] || "#9e9e9e";
const STATUS_ORDER = ["release", "draft", "deprecated", "undefined"];

// Catalog source (origin repository) — Feature 4b. SOURCE_COLORS must mirror
// the CSS --src-* tokens so the rendered badges match the dark/light theme.
const SOURCE_COLORS = {
  catenax: "#0e7c66", idta: "#6a4fbf", external: "#8b929c",
};
const SOURCE_LABELS = {
  catenax: "Catena-X", idta: "IDTA", external: "external",
};
const SOURCE_ORDER = ["catenax", "idta"];
const sourceColor = s => SOURCE_COLORS[s] || SOURCE_COLORS.external;
const sourceLabel = s => SOURCE_LABELS[s] || s || "?";

// Per-source jsDelivr CDN bases — used by the Generated docs sub-tab to fetch
// the upstream gen/<Stem>.html (and the companion schema/payload/openapi
// artifacts) from the right repo. Falls back to Catena-X for unknown sources.
const CDN_BY_SOURCE = {
  catenax: "https://cdn.jsdelivr.net/gh/eclipse-tractusx/sldt-semantic-models@main/",
  idta:    "https://cdn.jsdelivr.net/gh/admin-shell-io/smt-semantic-models@main/",
};
const cdnFor = entry => CDN_BY_SOURCE[entry && entry.source] || CDN_BY_SOURCE.catenax;

// Public GitHub repo per source — used to build a "View on GitHub" deep link
// to the model's version folder (entry.repo_path, set by graph.py).
const REPO_BY_SOURCE = {
  catenax: "eclipse-tractusx/sldt-semantic-models",
  idta:    "admin-shell-io/smt-semantic-models",
};
function githubUrlFor(entry) {
  const repo = REPO_BY_SOURCE[entry && entry.source];
  return repo && entry.repo_path
    ? `https://github.com/${repo}/tree/main/${entry.repo_path}` : null;
}

// Release notes are raw markdown that can contain <, &, > (e.g. urn:<...>).
const escapeHtml = s => String(s).replace(/[&<>]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

// ---- Routing (deep links via location.hash) -------------------------------
// URLs: #/home  #/search?q=…  #/model/<id>[/<sub>]  #/issues[?type=<id>]
// Push for major navigation (tab / model / issue type) ; replace for live
// input (Search query). All UI handlers go through setUrl() ; applyHash()
// does the reverse direction when the URL changes (back/forward, manual edit,
// initial load).
const VIEW_BY_URL = { home: "home", search: "searchview",
                      model: "modelviewer", standards: "standardsview",
                      issues: "issues" };
const URL_BY_VIEW = { home: "home", searchview: "search",
                      modelviewer: "model", standardsview: "standards",
                      issues: "issues" };
let _routing = false;   // true while applyHash mutates state — suppress push

function parseHash() {
  const h = (location.hash || "").replace(/^#/, "");
  const [pathPart, queryPart = ""] = h.split("?");
  const parts = pathPart.replace(/^\//, "").split("/").filter(Boolean);
  return { parts, params: new URLSearchParams(queryPart) };
}

function buildHash(viewUrl, path, params) {
  let h = "#/" + viewUrl;
  if (path) h += "/" + path;
  const qs = params && params.toString();
  if (qs) h += "?" + qs;
  return h;
}

function setUrl(hash, { replace = false } = {}) {
  if (_routing) return;                   // change initiated by applyHash itself
  if (hash === location.hash) return;
  if (replace) history.replaceState(null, "", hash);
  else history.pushState(null, "", hash);
}

// Reverse direction : URL → UI. Called at load, after every fetch (model/
// issues need data to resolve), and on hashchange (back/forward).
function applyHash() {
  const { parts, params } = parseHash();
  const urlView = parts[0] || "home";
  const view = VIEW_BY_URL[urlView] || "home";
  _routing = true;
  try {
    if (view !== activeView) showTab(view);

    if (view === "modelviewer" && parts[1]) {
      const id = parts[1];
      const entry = (allModels || []).find(e => e.id === id);
      if (entry) {
        // openEntry only if not already on this entry (avoids redundant fetch).
        if (!curEntry || curEntry.id !== id) openEntry(entry);
        const sub = parts[2] ? "sv-" + parts[2] : "sv-graph";
        if (activeSub !== sub) showSub(sub);
      }
      // else: index.json not loaded yet — applyHash will be re-called once it arrives.
    }
    if (view === "searchview") {
      const q = params.get("q") || "";
      const input = document.getElementById("sr-q");
      if (input && input.value !== q) {
        input.value = q;
        if (searchData) renderSearch();
      }
    }
    if (view === "issues") {
      const t = params.get("type") || null;
      if (t !== issSelType) {
        issSelType = t;
        if (issuesData) renderIssues();
      }
    }
    if (view === "standardsview") {
      const id = parts[1] || null;
      if (id !== stdSelId) {
        stdSelId = id;
        if (standardsData) renderStandards();
      }
    }
  } finally {
    _routing = false;
  }
}

window.addEventListener("hashchange", applyHash);
// GoatCounter analytics — manually count each internal navigation as a
// new pageview, since location.hash changes never reload the page (the
// auto-count only fires once, at initial load). `window.goatcounter` is
// populated by the async script in <head> ; we guard against it being
// undefined if the script hasn't loaded yet (the first few hashchanges
// after a slow CDN are then silently dropped — acceptable).
window.addEventListener("hashchange", () => {
  if (window.goatcounter && window.goatcounter.count) {
    window.goatcounter.count({
      path: location.pathname + location.search + location.hash,
    });
  }
});
// Early sync of the active tab so the user sees the right view immediately —
// data-dependent state (model entry, issue type) will be reapplied once the
// fetches resolve and call applyHash() again.
window.addEventListener("DOMContentLoaded", applyHash);

// ---- Top tabs -------------------------------------------------------------
let activeView = "home";
function showTab(view) {
  activeView = view;
  d3.selectAll(".tab").classed("active", function () { return this.dataset.view === view; });
  d3.selectAll(".view").classed("active", function () { return this.id === view; });
  if (view === "modelviewer") renderSub();   // svg needs to be visible to size
  else if (view === "issues") renderIssues();
  else if (view === "searchview") renderSearch();
  else if (view === "standardsview") renderStandards();
  // Push URL for this tab — applyHash sets _routing to skip the push on
  // reverse navigation. For tabs that carry extra state (model/sub for
  // modelviewer, type for issues, q for searchview), the dedicated handlers
  // push more specific URLs ; here we only push the bare tab when there's
  // nothing more to track yet.
  if (view === "modelviewer" && curEntry) {
    setUrl(buildHash(URL_BY_VIEW.modelviewer,
      `${curEntry.id}/${activeSub.replace(/^sv-/, "")}`));
  } else if (view === "issues") {
    const p = issSelType ? new URLSearchParams({ type: issSelType }) : null;
    setUrl(buildHash(URL_BY_VIEW.issues, "", p));
  } else if (view === "searchview") {
    const q = (document.getElementById("sr-q") || {}).value || "";
    const p = q ? new URLSearchParams({ q }) : null;
    setUrl(buildHash(URL_BY_VIEW.searchview, "", p));
  } else if (view === "standardsview") {
    setUrl(buildHash(URL_BY_VIEW.standardsview, stdSelId || ""));
  } else {
    setUrl(buildHash(URL_BY_VIEW[view] || view, ""));
  }
}
d3.selectAll(".tab").on("click", function () { showTab(this.dataset.view); });

// ---- Theme toggle (persisted; defaults to OS until an explicit choice) ----
const themeBtn = document.getElementById("theme-toggle");
if (themeBtn) themeBtn.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("theme", next); } catch (e) {}
});
try {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", e => {
    if (!localStorage.getItem("theme"))
      document.documentElement.dataset.theme = e.matches ? "dark" : "light";
  });
} catch (e) {}

// ---- Sub-tabs (inside Model Viewer) --------------------------------------
let activeSub = "sv-graph";
function showSub(sub) {
  activeSub = sub;
  // Sync URL when a model is open (sub-tab alone has no URL otherwise).
  if (curEntry)
    setUrl(buildHash(URL_BY_VIEW.modelviewer,
      `${curEntry.id}/${sub.replace(/^sv-/, "")}`));
  d3.selectAll(".subtab").classed("active", function () { return this.dataset.sub === sub; });
  d3.selectAll(".subview").classed("active", function () { return this.id === sub; });
  renderSub();
}
d3.selectAll(".subtab").on("click", function () { showSub(this.dataset.sub); });

// ---- Layout engine (used by the element graph AND the dependency graph)
// Modes :
//   "freeze" : force-directed computed once then frozen — nothing drifts, a
//              dragged node stays where you drop it
//   "force"  : live force-directed (keeps animating; drag releases)
//   "circle" : static ring     "grid": static grid   (no simulation at all)
//   "hier"   : layered BFS from root(s). On the element graph, roots =
//              Aspects. On the dep graph, root = currently selected
//              model+version ; in Incoming mode the orientation is flipped
//              (root sits on the right).
// Each graph has its own user-controlled mode ; callers (draw / drawDep)
// write the active value into the global `layoutMode` right before they
// invoke makeSim/dragger.
//   - Element graph : dropdown #mv-layout in #mv-head, defaults to "freeze"
//     (calmer for the element graph which is usually a small tree).
//   - Dep graph     : dropdown #dep-layout inside #dep-controls, defaults
//     to "hier" (much more readable than freeze on chains of 10+ deps).
let layoutMode = "freeze";
let elementLayoutMode = "freeze";
let depLayoutMode = "hier";

function placeStatic(nodes, mode, width, height) {
  const n = nodes.length || 1;
  if (mode === "circle") {
    const cx = width / 2, cy = height / 2;
    const R = Math.max(70, Math.min(width, height) / 2 - 70);
    nodes.forEach((d, i) => {
      const a = (2 * Math.PI * i) / n - Math.PI / 2;
      d.x = d.fx = cx + R * Math.cos(a);
      d.y = d.fy = cy + R * Math.sin(a);
    });
  } else {                                   // grid
    const cols = Math.ceil(Math.sqrt(n));
    const rows = Math.ceil(n / cols);
    const gx = width / (cols + 1), gy = height / (rows + 1);
    nodes.forEach((d, i) => {
      d.x = d.fx = gx * ((i % cols) + 1);
      d.y = d.fy = gy * (((i / cols) | 0) + 1);
    });
  }
}

// Layered (hierarchical) layout : multi-source BFS from `rootIds` along the
// arrow direction. Depth -> column ; nodes at the same depth are evenly
// stacked. Unreached nodes (cycles back, disconnected) land in the rightmost
// column. `directionFlip=true` mirrors columns horizontally (used for the
// dep graph in Incoming mode so the selected model sits on the right and
// "ancestors who use it" extend to the left).
// Returns `true` when applied, `false` if there are no usable roots (caller
// should fall back to freeze).
function placeHierarchical(nodes, links, width, height, opts) {
  const rootIds = (opts && opts.rootIds) || [];
  const present = new Set(nodes.map(n => n.id));
  const usableRoots = rootIds.filter(id => present.has(id));
  if (!usableRoots.length) return false;

  // Adjacency along arrow direction (source → target). Links are read AFTER
  // d3.forceLink has resolved its references — they may be either ids or
  // objects, so we normalize.
  const adj = new Map(nodes.map(n => [n.id, []]));
  links.forEach(e => {
    const s = typeof e.source === "object" ? e.source.id : e.source;
    const t = typeof e.target === "object" ? e.target.id : e.target;
    if (adj.has(s)) adj.get(s).push(t);
  });

  // BFS multi-source.
  const depth = new Map();
  const queue = [];
  usableRoots.forEach(r => { depth.set(r, 0); queue.push(r); });
  while (queue.length) {
    const u = queue.shift();
    const du = depth.get(u);
    for (const v of adj.get(u) || []) {
      if (!depth.has(v)) { depth.set(v, du + 1); queue.push(v); }
    }
  }
  // Stragglers go past the last column so they don't overlap reached nodes.
  let maxD = 0;
  depth.forEach(d => { if (d > maxD) maxD = d; });
  nodes.forEach(n => { if (!depth.has(n.id)) depth.set(n.id, maxD + 1); });
  maxD = Math.max(maxD, ...depth.values());

  // Bucket by depth.
  const byDepth = new Map();
  nodes.forEach(n => {
    const k = depth.get(n.id);
    if (!byDepth.has(k)) byDepth.set(k, []);
    byDepth.get(k).push(n);
  });

  const padX = 60, padY = 30;
  const usableW = Math.max(width - 2 * padX, 200);
  const usableH = Math.max(height - 2 * padY, 200);
  const nCols = maxD + 1;
  const colW = nCols > 1 ? usableW / (nCols - 1) : 0;

  byDepth.forEach((arr, d) => {
    const x = opts.directionFlip
      ? width - padX - d * colW
      : padX + d * colW;
    const m = arr.length;
    const rowH = usableH / Math.max(m, 1);
    // Slight name sort so the column order is stable.
    arr.sort((a, b) => (a.label || a.name || "").localeCompare(b.label || b.name || ""));
    arr.forEach((node, i) => {
      node.x = node.fx = x;
      node.y = node.fy = padY + rowH * (i + 0.5);
    });
  });
  return true;
}

// Build a simulation honoring layoutMode. For freeze/circle/grid it is
// returned already stopped with x/y/fx/fy set, so the graph never moves on
// its own and dragging a node only moves that node.
function makeSim(nodes, links, width, height, opt) {
  const sim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id(d => d.id).distance(opt.distance))
    .force("charge", d3.forceManyBody().strength(opt.charge))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide(opt.collide));
  // Mild gravity toward centre : keeps DISCONNECTED components (a model with
  // several aspects / orphan elements) from drifting into far corners under
  // the repulsion charge. Collision still prevents overlap, so the result is
  // compact-but-readable. `gravity` defaults to 0 (no pull) for graphs that
  // don't ask for it.
  if (opt.gravity) {
    sim.force("x", d3.forceX(width / 2).strength(opt.gravity))
       .force("y", d3.forceY(height / 2).strength(opt.gravity));
  }
  if (layoutMode === "circle" || layoutMode === "grid") {
    sim.stop();
    placeStatic(nodes, layoutMode, width, height);
  } else if (layoutMode === "hier") {
    sim.stop();
    const ok = placeHierarchical(nodes, links, width, height, opt.hierarchy || {});
    if (!ok) {                                          // no roots: freeze fallback
      for (let i = 0; i < 300; i++) sim.tick();
      nodes.forEach(d => { d.fx = d.x; d.fy = d.y; });
    }
  } else if (layoutMode === "freeze") {
    sim.stop();
    for (let i = 0; i < 300; i++) sim.tick();          // settle once…
    nodes.forEach(d => { d.fx = d.x; d.fy = d.y; });   // …then pin
  }
  return sim;                                          // "force": running
}

// Drag matched to the layout: live force releases the node; every other
// mode (freeze / hier / circle / grid) pins it where dropped.
function dragger(sim, ticked) {
  const live = layoutMode === "force";
  return d3.drag()
    .on("start", (e, d) => {
      if (live && !e.active) sim.alphaTarget(.3).restart();
      d.fx = d.x; d.fy = d.y;
    })
    .on("drag", (e, d) => {
      d.fx = e.x; d.fy = e.y;
      if (!live) { d.x = e.x; d.y = e.y; ticked(); }
    })
    .on("end", (e, d) => {
      if (live) { if (!e.active) sim.alphaTarget(0); d.fx = d.fy = null; }
      // freeze/circle/grid: keep fx/fy so the node stays put
    });
}

// Fit a laid-out graph into its viewport : compute the node bounding box and
// apply a zoom transform so the graph fills `svgNode` with padding, instead of
// sitting as a tiny cluster in the middle of a large canvas. `labelPad` reserves
// room on the right for node labels (drawn at x+12, ~6px/char at 10px font) so
// they aren't clipped. Caps the scale so a 1–2 node graph isn't blown up huge.
function fitView(svgNode, zoomB, nodes, opts) {
  if (!nodes.length) return;
  opts = opts || {};
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  let labelW = 0;
  nodes.forEach(d => {
    if (d.x < minX) minX = d.x;
    if (d.x > maxX) maxX = d.x;
    if (d.y < minY) minY = d.y;
    if (d.y > maxY) maxY = d.y;
    if (opts.labelPad) {
      const w = 12 + (d.label || d.name || "").length * 6;
      if (w > labelW) labelW = w;
    }
  });
  maxX += labelW;                                   // keep right-side labels in view
  const rect = svgNode.getBoundingClientRect();
  const pad = opts.pad == null ? 48 : opts.pad;
  const bw = Math.max(maxX - minX, 1), bh = Math.max(maxY - minY, 1);
  const scale = Math.max(0.1, Math.min(opts.maxScale || 1.5,
    (rect.width - 2 * pad) / bw, (rect.height - 2 * pad) / bh));
  const tx = rect.width / 2 - scale * (minX + maxX) / 2;
  const ty = rect.height / 2 - scale * (minY + maxY) / 2;
  d3.select(svgNode).transition().duration(opts.instant ? 0 : 300)
    .call(zoomB.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
}

// Layout selector applies to whichever graph sub-tab is showing.
// Layout selectors — one per graph. Each writes to its own variable, and
// the active draw() / drawDep() copies that into the global `layoutMode`
// right before invoking makeSim.
document.getElementById("mv-layout").addEventListener("change", function () {
  elementLayoutMode = this.value;
  if (activeSub === "sv-graph") renderSub();
});
document.getElementById("dep-layout").addEventListener("change", function () {
  depLayoutMode = this.value;
  if (activeSub === "sv-dep") renderSub();
});

// ---- Graph (D3 force-directed) -------------------------------------------
const svg = d3.select("#graph");
const tooltip = d3.select("#tooltip");
const container = svg.append("g");
const zoomEl = d3.zoom().scaleExtent([0.1, 4]).on("zoom",
  e => container.attr("transform", e.transform));
svg.call(zoomEl);

// Directed arrow for edges
svg.append("defs").append("marker")
  .attr("id", "arrow").attr("viewBox", "0 -5 10 10").attr("refX", 18)
  .attr("markerWidth", 6).attr("markerHeight", 6).attr("orient", "auto")
  .append("path").attr("d", "M0,-5L10,0L0,5").attr("fill", "#999");

// Legend is rebuilt when the loaded graph changes (different model+version),
// not on every redraw — toggling a checkbox should not steal its own focus.
let lastLegendGraph = null;
let sim;
function draw(g) {
  document.getElementById("mv-empty").style.display = "none";
  container.selectAll("*").remove();
  const { width, height } = svg.node().getBoundingClientRect();

  if (lastLegendGraph !== g) { drawLegend(g); lastLegendGraph = g; }

  // Apply legend filter: keep only nodes whose kind is checked, then keep
  // only links whose endpoints both survive.
  const keptNodes = g.nodes.filter(n => kindFilter.has(n.kind));
  const keepId = new Set(keptNodes.map(n => n.id));
  const keptLinks = g.links.filter(e => keepId.has(e.source) && keepId.has(e.target));

  // Copies (D3 mutates objects, injecting resolved x/y/source/target)
  const nodes = keptNodes.map(d => ({ ...d }));
  const links = keptLinks.map(d => ({ ...d }));

  if (sim) sim.stop();
  // Element graph honors its own selector (#mv-layout) — defaults to
  // "freeze". `hierarchy` payload always provided so the "Hierarchy"
  // option is functional too (roots = Aspects).
  layoutMode = elementLayoutMode;
  const aspectIds = nodes.filter(n => n.kind === "Aspect").map(n => n.id);
  // Stronger repulsion + a label-aware collision radius (wider for long
  // names) so nodes spread out and their labels stop overlapping ; fitView()
  // below then zooms the result to fill the canvas.
  sim = makeSim(nodes, links, width, height,
    { distance: 80, charge: -240, gravity: 0.16,
      collide: d => 18 + Math.min(64, (d.label || d.name || "").length * 3.2),
      hierarchy: { rootIds: aspectIds, directionFlip: false } });

  const link = container.append("g").selectAll("line").data(links).enter()
    .append("line")
    .attr("class", d => "link" + (d.optional ? " optional" : ""))
    .attr("stroke-width", 1.2).attr("marker-end", "url(#arrow)");

  const linkLabel = container.append("g").selectAll("text").data(links).enter()
    .append("text").attr("class", "link-label").text(d => d.label);

  function ticked() {
    link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
    linkLabel.attr("x", d => (d.source.x + d.target.x) / 2)
             .attr("y", d => (d.source.y + d.target.y) / 2);
    node.attr("transform", d => `translate(${d.x},${d.y})`);
  }

  const node = container.append("g").selectAll("g").data(nodes).enter()
    .append("g").attr("class", "node")
    .call(dragger(sim, ticked));

  node.append("circle").attr("r", 9).attr("fill", d => color(d.kind))
    .on("mouseover", (e, d) => {
      // External refs : arêtes sortantes vers un autre modèle (ou un
      // bloc tiers connu). Apparaît surtout sur les éléments IDTA qui
      // délèguent à shared@3.1.0 ou pointent vers Catena-X.
      let refs = "";
      if (d.external_refs && d.external_refs.length) {
        const items = d.external_refs.slice(0, 6).map(r =>
          `<div><span class="k">${r.predicate}</span> → ` +
          `<b>${escapeHtml(r.target_local)}</b>` +
          (r.target_model
            ? ` <span class="badge" style="background:` +
              `${sourceColor(r.target_source)};padding:0 5px;font-size:10px">` +
              `${escapeHtml(r.target_model)}</span>`
            : "") +
          `</div>`).join("");
        const more = d.external_refs.length > 6
          ? `<div style="opacity:.7;font-size:11px">+${d.external_refs.length - 6} more</div>`
          : "";
        refs = `<div style="margin-top:6px;border-top:1px solid #555;` +
               `padding-top:5px;font-size:12px"><b>External refs ` +
               `(${d.external_refs.length})</b>${items}${more}</div>`;
      }
      tooltip.style("opacity", 1).html(
        `<span class="k">${d.kind}</span> — <b>${d.label}</b><br>` +
        (d.preferredName ? `<i>${d.preferredName}</i><br>` : "") +
        (d.description ? d.description.slice(0, 280) : "") +
        refs);
    })
    .on("mousemove", e =>
      tooltip.style("left", (e.pageX + 14) + "px").style("top", (e.pageY + 8) + "px"))
    .on("mouseout", () => tooltip.style("opacity", 0));

  node.append("text").attr("x", 12).attr("y", 4).text(d => d.label);

  sim.on("tick", ticked);
  ticked();                       // initial paint (static layouts won't tick)

  // Frame the graph: static layouts are final now ; the live "force" layout
  // keeps moving, so fit once it settles (and roughly during the run).
  const fit = inst => fitView(svg.node(), zoomEl, nodes, { labelPad: true, instant: inst });
  if (layoutMode === "force") sim.on("end", () => fit(false));
  else fit(false);
}

function drawLegend(g) {
  const l = d3.select("#legend");
  l.html("");
  l.style("display", g ? "" : "none");
  if (!g) return;
  l.append("div").attr("class", "sep").text("Node type");
  // Always list every known kind (full reference), plus any unexpected kind
  // the graph might carry, so the user can toggle anything on/off.
  const present = new Set(g.nodes.map(n => n.kind));
  const kinds = Object.keys(KIND_COLORS)
    .concat([...present].filter(k => !(k in KIND_COLORS)));
  kinds.forEach(k => {
    const row = l.append("label").attr("class", "kbox");
    row.append("input").attr("type", "checkbox")
      .property("checked", kindFilter.has(k))
      .on("change", function () {
        if (this.checked) kindFilter.add(k); else kindFilter.delete(k);
        if (curGraph) draw(curGraph);
      });
    row.append("span").attr("class", "dot")
      .style("background", KIND_COLORS[k] || "#999");
    row.append("span").text(k);
  });
}

// ---- Dependency graph (D3 force, recursive, cycle-safe) ------------------
const DEP_COLORS = { root: "#4363d8", dep: "#3cb44b", missing: "#b00020" };
const depSvg = d3.select("#depgraph");
const depContainer = depSvg.append("g");
depSvg.call(d3.zoom().scaleExtent([0.1, 4]).on("zoom",
  e => depContainer.attr("transform", e.transform)));
// refX = path tip (x=10): the line is trimmed to the node ring edge in
// ticked(), so the arrowhead lands in the gap just outside the ring.
depSvg.append("defs").append("marker")
  .attr("id", "darrow").attr("viewBox", "0 -5 10 10").attr("refX", 10)
  .attr("markerWidth", 7).attr("markerHeight", 7).attr("orient", "auto")
  .append("path").attr("d", "M0,-5L10,0L0,5").attr("fill", "#777");

// Outer visual radius of a dep node (ring r=16 + ~half its 3.5px stroke),
// plus a small gap, so the trimmed link end clears the status ring.
const DEP_R = 20;
function depEndpoints(d) {
  const sx = d.source.x, sy = d.source.y, tx = d.target.x, ty = d.target.y;
  let dx = tx - sx, dy = ty - sy;
  const dist = Math.hypot(dx, dy) || 1;
  dx /= dist; dy /= dist;
  const r = Math.max(0, Math.min(DEP_R, dist / 2 - 1)); // never cross over
  return { x1: sx + dx * r, y1: sy + dy * r,
           x2: tx - dx * r, y2: ty - dy * r };
}

(function depLegend() {
  const l = d3.select("#dep-legend");
  l.append("div").attr("class", "sep").text("Role (fill)");
  [["Selected model", DEP_COLORS.root],
   ["Linked model (in catalog)", DEP_COLORS.dep],
   ["Not in catalog", DEP_COLORS.missing]].forEach(([t, c]) =>
    l.append("div").html(`<span class="dot" style="background:${c}"></span>${t}`));
  l.append("div").attr("class", "sep").text("Status (ring)");
  ["release", "draft", "deprecated", "undefined"].forEach(s =>
    l.append("div").html(
      `<span class="ring" style="border:3px solid ${statusColor(s)}"></span>${s}`));
  l.append("div").attr("class", "sep").text("Edge");
  l.append("div").html(
    `<span class="cross-dash"></span>Cross-source (e.g. IDTA → Catena-X)`);
})();

const depKey = e => (e ? `${e.model_name}@${e.version}` : null);

// "out" = follow deps (models this one USES) ; "in" = reverse (models that
// USE this one). Edges always mean source→target = "source uses target".
let depDir = "out";
let revMap = null;                 // built lazily : key -> [keys that use it]
let statusByKey = null;            // built lazily from index.json : key -> status
let sourceByKey = null;            // built lazily from index.json : key -> source

function buildRevMap() {
  const r = {};
  for (const [p, v] of Object.entries(depsMap || {}))
    v.deps.forEach(d => (r[d] = r[d] || []).push(p));
  return r;
}

// Status/source come from the catalog (index.json, already loaded). A key
// out of the catalog has neither (it's the "Not in catalog" case anyway).
function keyStatus(k) {
  if (!statusByKey) {
    statusByKey = {};
    allModels.forEach(e => {
      const kk = `${e.model_name}@${e.version}`;
      if (!(kk in statusByKey)) statusByKey[kk] = e.status;
    });
  }
  return statusByKey[k] || null;
}
function keySource(k) {
  if (!sourceByKey) {
    sourceByKey = {};
    allModels.forEach(e => {
      const kk = `${e.model_name}@${e.version}`;
      if (!(kk in sourceByKey)) sourceByKey[kk] = e.source;
    });
  }
  return sourceByKey[k] || null;
}

// BFS, transitive. `expanded` guards cycles: A→B and B→A both draw an edge,
// but each node is expanded once, so no infinite recursion.
function buildDepGraph(rootKey) {
  const nodes = new Map(), links = [], expanded = new Set(), queue = [rootKey];
  const ensure = k => {
    if (!nodes.has(k)) {
      const m = depsMap && depsMap[k];
      const at = k.lastIndexOf("@");
      nodes.set(k, {
        id: k, present: !!m,
        name: m ? m.name : k.slice(0, at),
        version: m ? m.version : k.slice(at + 1),
        status: m ? (keyStatus(k) || "undefined") : null,
        source: m ? keySource(k) : null,
        root: k === rootKey,
      });
    }
    return nodes.get(k);
  };
  const incoming = depDir === "in";
  if (incoming && !revMap) revMap = buildRevMap();
  ensure(rootKey);
  while (queue.length) {
    const k = queue.shift();
    if (expanded.has(k)) continue;
    expanded.add(k);
    // Neighbours + edge orientation per direction (arrow = "uses").
    const next = incoming
      ? (revMap[k] || []).map(p => [p, { source: p, target: k }])
      : ((depsMap && depsMap[k] ? depsMap[k].deps : [])
          .map(d => [d, { source: k, target: d }]));
    next.forEach(([nb, edge]) => {
      ensure(nb);
      links.push(edge);
      if (!expanded.has(nb)) queue.push(nb);   // cycle-safe
    });
  }
  return { nodes: [...nodes.values()], links };
}

d3.selectAll("#dep-controls button").on("click", function () {
  depDir = this.dataset.dir;
  d3.selectAll("#dep-controls button")
    .classed("on", function () { return this.dataset.dir === depDir; });
  if (activeSub === "sv-dep") renderSub();
});

let depSim;
function drawDep(rootKey) {
  const empty = document.getElementById("dep-empty");
  if (!depsMap) {
    empty.style.display = "";
    empty.textContent = "deps.json not found — run: python -m sldt_analyzer.graph";
    return;
  }
  const g = buildDepGraph(rootKey);
  empty.style.display = "none";
  depContainer.selectAll("*").remove();
  const { width, height } = depSvg.node().getBoundingClientRect();
  const nodes = g.nodes, links = g.links.map(d => ({ ...d }));

  if (depSim) depSim.stop();
  // The dep graph honors its own selector (#dep-layout) — defaults to
  // "hier". root = selected model+version. In Incoming, the arrows feed
  // INTO the root, so we mirror columns (root on the right, ancestors
  // extending to the left).
  layoutMode = depLayoutMode;
  depSim = makeSim(nodes, links, width, height,
    { distance: 110, charge: -320, collide: 34,
      hierarchy: { rootIds: [rootKey], directionFlip: depDir === "in" } });

  // Flag arêtes "cross-source" : source du nœud parent ≠ source du nœud cible
  // (ex. IDTA -> Catena-X). On le calcule AVANT que d3.forceLink remplace
  // d.source/d.target par les objets nœuds (la résolution arrive au 1er tick),
  // pour lire les ids en string et garder l'intention explicite.
  const nodeById = new Map(nodes.map(n => [n.id, n]));
  links.forEach(d => {
    const a = nodeById.get(typeof d.source === "object" ? d.source.id : d.source);
    const b = nodeById.get(typeof d.target === "object" ? d.target.id : d.target);
    d.crossSource = !!(a && b && a.source && b.source && a.source !== b.source);
  });

  const link = depContainer.append("g").selectAll("line").data(links).enter()
    .append("line").attr("class", d => "link" + (d.crossSource ? " cross" : ""))
    .attr("stroke-width", d => d.crossSource ? 2 : 1.4)
    .attr("marker-end", "url(#darrow)");

  function ticked() {
    link.each(function (d) {
      const e = depEndpoints(d);
      this.setAttribute("x1", e.x1); this.setAttribute("y1", e.y1);
      this.setAttribute("x2", e.x2); this.setAttribute("y2", e.y2);
    });
    node.attr("transform", d => `translate(${d.x},${d.y})`);
  }

  const node = depContainer.append("g").selectAll("g").data(nodes).enter()
    .append("g").attr("class", "node")
    .call(dragger(depSim, ticked));

  // Outer ring = status (release/draft/deprecated/undefined); fill = role.
  // Use .style() so it beats the `.node circle { stroke:#fff }` stylesheet
  // rule (presentation attributes lose the cascade to CSS rules).
  node.append("circle").attr("r", 16).attr("class", "dep-status")
    .style("fill", "none").style("stroke-width", "3.5px")
    .style("stroke", d => d.status ? statusColor(d.status) : "transparent");

  node.append("circle").attr("r", 12)
    .attr("class", d => d.root ? "dep-root" : (d.present ? "" : "dep-missing"))
    .attr("fill", d => d.root ? DEP_COLORS.root
                              : (d.present ? DEP_COLORS.dep : DEP_COLORS.missing))
    .style("cursor", d => d.present ? "pointer" : "default")
    .on("mouseover", (e, d) => tooltip.style("opacity", 1).html(
      `<b>${d.name}</b> <span class="k">v${d.version}</span><br>` +
      (d.source
        ? `source: <b style="color:${sourceColor(d.source)}">${sourceLabel(d.source)}</b><br>`
        : "") +
      (d.status
        ? `status: <b style="color:${statusColor(d.status)}">${d.status}</b><br>`
        : "") +
      (d.root ? "selected model<br><i>click to re-root here</i>"
       : d.present ? "in catalog<br><i>click to re-root here</i>"
       : "<i>not in catalog (unresolved dependency)</i>")))
    .on("mousemove", e =>
      tooltip.style("left", (e.pageX + 14) + "px").style("top", (e.pageY + 8) + "px"))
    .on("mouseout", () => tooltip.style("opacity", 0))
    .on("click", (e, d) => {
      if (!d.present) return;
      const ent = entryForKey(d.id);
      if (ent) openEntry(ent);     // re-roots here (we stay on this sub-tab)
    });

  node.append("text").attr("x", 15).attr("y", 4)
    .text(d => `${d.name} v${d.version}`);

  depSim.on("tick", ticked);
  ticked();                       // initial paint (static layouts won't tick)
}

// ---- Model Viewer state ---------------------------------------------------
let allModels = [];          // index.json (one entry per version file)
let groups = new Map();      // model_name -> entries sorted by version desc
let curModel = null;         // selected model_name (notes are model-level)
let curEntries = [];         // sorted entries of the selected model
let curEntry = null;         // selected entry (version)
let curGraph = null;         // fetched <id>.json for curEntry
const notesCache = new Map();// model_name -> release_notes (version-independent)
let depsMap = null;          // deps.json : "name@version" -> {name,version,family,deps[]}

// descending semantic-ish version compare ("4.0.0" before "2.0.1")
function cmpVerDesc(a, b) {
  const pa = a.split(".").map(Number), pb = b.split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] || 0, y = pb[i] || 0;
    if (x !== y) return y - x;
  }
  return 0;
}

function buildGroups() {
  groups = new Map();
  [...d3.group(allModels, d => d.model_name).entries()]
    .forEach(([name, entries]) =>
      groups.set(name, entries.slice().sort((a, b) => cmpVerDesc(a.version, b.version))));
}

// ---- Model search (type-ahead over model_name) ---------------------------
const MV_MAX = 50;                 // cap rendered matches
let mvHl = -1;                     // highlighted index in the open list

function modelMatches(q) {
  q = q.trim().toLowerCase();
  const names = [...groups.keys()].sort();
  return q ? names.filter(n => n.toLowerCase().includes(q)) : names;
}

function closeModelList() {
  document.getElementById("mv-model-list").classList.remove("open");
  mvHl = -1;
}

function renderModelList() {
  const inp = document.getElementById("mv-model");
  const box = document.getElementById("mv-model-list");
  const all = modelMatches(inp.value);
  const shown = all.slice(0, MV_MAX);
  box.innerHTML = "";
  if (!shown.length) {
    box.innerHTML = `<div class="msg">No model matches.</div>`;
  } else {
    shown.forEach((name, i) => {
      const d = document.createElement("div");
      d.className = "opt" + (i === mvHl ? " hl" : "");
      d.textContent = name;
      // mousedown fires before the input's blur, so the pick is not lost.
      d.addEventListener("mousedown", e => { e.preventDefault(); chooseModel(name); });
      box.appendChild(d);
    });
    if (all.length > shown.length) {
      const m = document.createElement("div");
      m.className = "msg";
      m.textContent = `+${all.length - shown.length} more — keep typing to refine`;
      box.appendChild(m);
    }
  }
  box.classList.add("open");
}

function chooseModel(name) {
  document.getElementById("mv-model").value = name;
  closeModelList();
  selectModel(name);
}

function setupModelSearch() {
  const inp = document.getElementById("mv-model");
  inp.addEventListener("input", () => { mvHl = -1; renderModelList(); });
  inp.addEventListener("focus", () => { mvHl = -1; renderModelList(); });
  inp.addEventListener("blur", () => setTimeout(closeModelList, 120));
  inp.addEventListener("keydown", e => {
    const box = document.getElementById("mv-model-list");
    const opts = [...box.querySelectorAll(".opt")];
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!box.classList.contains("open")) { renderModelList(); return; }
      if (!opts.length) return;
      mvHl = e.key === "ArrowDown"
        ? (mvHl + 1) % opts.length
        : (mvHl - 1 + opts.length) % opts.length;
      opts.forEach((o, i) => o.classList.toggle("hl", i === mvHl));
      opts[mvHl].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      const pick = mvHl >= 0 ? opts[mvHl] : opts[0];
      if (pick && pick.classList.contains("opt")) chooseModel(pick.textContent);
    } else if (e.key === "Escape") {
      closeModelList();
    }
  });
}

function fillVersionSelect(entries) {
  const sel = document.getElementById("mv-version");
  sel.innerHTML = "";
  entries.forEach((e, i) => {
    const o = document.createElement("option");
    o.value = i;
    o.textContent = `v${e.version} · ${e.family} · ${e.status}`;
    sel.appendChild(o);
  });
}

function badges(e) {
  const multi = e.n_aspects > 1 ? `<span>· ${e.n_aspects} Aspects</span>` : "";
  return `<span class="badge" style="background:${sourceColor(e.source)}">` +
    `${sourceLabel(e.source)}</span>` +
    `<span class="badge" style="background:${statusColor(e.status)}">${e.status}</span>` +
    `<span class="badge" style="background:#607d8b">${e.family}</span>` +
    `<span>${e.meta_model}</span><span>· ${e.n_nodes} nodes · ${e.n_links} links</span>` +
    multi +
    (curGraph && curGraph.meta && curGraph.meta.release_date
      ? `<span>· released ${curGraph.meta.release_date}</span>` : "");
}

// Catena-X standards (current release) that reference this model+version.
// Data comes from standards.json (models map). Returns clickable chips, or "".
function standardsBadges(e) {
  if (!e || !standardsData || !standardsData.models) return "";
  const ids = standardsData.models[`${e.model_name}@${e.version}`] || [];
  if (!ids.length) return "";
  const chips = ids.map(cx =>
    `<span class="badge badge-std" data-std="${escapeHtml(cx)}" ` +
    `title="Used in Catena-X standard ${escapeHtml(cx)} — click to open">` +
    `${escapeHtml(cx)}</span>`).join(" ");
  return `<span class="mv-std-lbl">Standards:</span> ${chips}`;
}

// ---- Export PNG of the currently-visible graph (Feature 22) --------------
// Two routes : Graph (#graph, element graph) or Dependency graph (#depgraph),
// both are inline SVG. The CSS-driven appearance (KIND_COLORS, statusColor,
// crossSource dashed edges…) must be inlined into the cloned SVG before
// rendering it through a Canvas, otherwise the PNG would be unstyled.
const _EXPORT_STYLE_PROPS = [
  "fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-opacity",
  "fill-opacity", "opacity", "font-size", "font-family", "font-weight",
  "color",
];

function _inlineStyles(src, dst) {
  // Walk both trees in lockstep ; copy computed style of relevant props onto
  // the clone. Works because cloneNode preserves child order.
  const cs = window.getComputedStyle(src);
  for (const p of _EXPORT_STYLE_PROPS) {
    const v = cs.getPropertyValue(p);
    if (v) dst.style.setProperty(p, v);
  }
  const sc = src.children, dc = dst.children;
  for (let i = 0; i < sc.length; i++) _inlineStyles(sc[i], dc[i]);
}

function exportCurrentGraphPng() {
  const svgId = activeSub === "sv-graph" ? "graph"
              : activeSub === "sv-dep" ? "depgraph" : null;
  if (!svgId || !curEntry) return;
  const svg = document.getElementById(svgId);
  if (!svg) return;
  const { width, height } = svg.getBoundingClientRect();
  const w = Math.max(1, Math.round(width));
  const h = Math.max(1, Math.round(height));

  // Clone + inline styles so the rasterized image keeps its look.
  const clone = svg.cloneNode(true);
  _inlineStyles(svg, clone);
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", w);
  clone.setAttribute("height", h);
  clone.setAttribute("viewBox", `0 0 ${w} ${h}`);
  // Solid background so PNG isn't transparent on slides / shares.
  const bg = getComputedStyle(document.body).backgroundColor || "#fff";
  const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("width", "100%");
  rect.setAttribute("height", "100%");
  rect.setAttribute("fill", bg);
  clone.insertBefore(rect, clone.firstChild);

  const xml = new XMLSerializer().serializeToString(clone);
  const url = URL.createObjectURL(
    new Blob([xml], { type: "image/svg+xml;charset=utf-8" }));

  const img = new Image();
  img.onload = () => {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);  // cap at 2× to
                                                              // keep PNG sane
    const canvas = document.createElement("canvas");
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.drawImage(img, 0, 0, w, h);
    URL.revokeObjectURL(url);
    canvas.toBlob(blob => {
      const dl = document.createElement("a");
      dl.href = URL.createObjectURL(blob);
      dl.download =
        `${curEntry.id}_${svgId === "graph" ? "graph" : "dep"}.png`;
      document.body.appendChild(dl); dl.click(); dl.remove();
      setTimeout(() => URL.revokeObjectURL(dl.href), 1000);
    }, "image/png");
  };
  img.onerror = () => {
    URL.revokeObjectURL(url);
    alert("PNG export failed — could not rasterize the graph.");
  };
  img.src = url;
}

(function bindExport() {
  const btn = document.getElementById("mv-export");
  if (btn) btn.addEventListener("click", exportCurrentGraphPng);
})();

// Clicking a standard chip in the model header opens that Catena-X standard.
(function bindStdChips() {
  const box = document.getElementById("mv-badges");
  if (box) box.addEventListener("click", ev => {
    const chip = ev.target.closest(".badge-std");
    if (chip && chip.dataset.std) { selectStandard(chip.dataset.std); showTab("standardsview"); }
  });
})();

function loadEntry(e) {
  curEntry = e;
  document.getElementById("mv-badges").innerHTML = "";
  // "View on GitHub" — show only if we can build a URL (known source +
  // repo_path present in index.json). Repo_path = version folder.
  const gh = document.getElementById("mv-github");
  const url = githubUrlFor(e);
  if (url) { gh.href = url; gh.style.display = ""; }
  else gh.style.display = "none";
  fetch(`data/graph/${e.file}`).then(r => r.json()).then(g => {
    curGraph = g;
    // Release notes are identical across a model's versions: cache once.
    if (!notesCache.has(e.model_name))
      notesCache.set(e.model_name, (g.meta && g.meta.release_notes) || null);
    document.getElementById("mv-badges").innerHTML = badges(e) + standardsBadges(e);
    renderSub();
  });
}

// Render whatever sub-tab is active for the current selection.
function renderSub() {
  if (document.getElementById("modelviewer").className.indexOf("active") === -1) return;
  // The "Export PNG" button only makes sense for the two SVG-based sub-tabs.
  const exportBtn = document.getElementById("mv-export");
  if (exportBtn) {
    const onSvg = (activeSub === "sv-graph" || activeSub === "sv-dep");
    exportBtn.style.display = (onSvg && curEntry) ? "" : "none";
  }
  if (activeSub === "sv-graph") {
    if (curGraph) draw(curGraph);
    else document.getElementById("mv-empty").style.display = "";
  } else if (activeSub === "sv-notes") {
    const el = document.getElementById("notes-body");
    if (!curModel) { el.textContent = "Search and pick a model above."; return; }
    if (!notesCache.has(curModel)) { el.textContent = "Loading release notes…"; return; }
    const notes = notesCache.get(curModel);
    el.innerHTML = notes
      ? `<h2>Changelog — ${escapeHtml(curModel)} ` +
        `<span style="font-weight:400;color:#888;font-size:12px;">` +
        `(whole model — same for every version)</span></h2>` +
        `<pre>${escapeHtml(notes)}</pre>`
      : `<i>No RELEASE_NOTES.md for this model.</i>`;
  } else if (activeSub === "sv-dep") {
    const k = depKey(curEntry);
    if (!k) {
      const e = document.getElementById("dep-empty");
      e.style.display = ""; e.textContent = "Pick a model and version above.";
      return;
    }
    drawDep(k);
  } else if (activeSub === "sv-docs") {
    renderDocs();
  } else if (activeSub === "sv-issues") {
    renderIssuesSub();
  }
}

// ---- Issues : detail renderers (shared by the tab list & the sub-tab) -----
// Detail shapes by issue id:
//   deprecated_dep        : [{target, path[]}]
//   circular_dep          : [key, ...]                  (strings)
//   unresolved_dep        : [key, ...]                  (strings)
//   missing_files         : ["ttl"|"metadata.json"|"version", ...]
//   outdated_dependency   : [{target, latest_release}]
//   older_release_exists  : [{latest_release}]
//   dep_on_draft          : [key, ...]                  (strings)
//   dep_on_undefined      : [key, ...]                  (strings)
//   orphan_*              : [{name, kind, file}]
//   missing_*, empty_*    : [{name, kind, file}]
//   bad_naming_*          : [{name, kind, file}]
//   aspect_without_props  : [{name, kind, file}]
//   property_without_char : [{name, kind, file}]
//   characteristic_no_dt  : [{name, kind, file}]
//   trait_without_constr  : [{name, kind, file}]
//   unused_property       : [{name, kind, file}]
//   namespace_mismatch    : [{file, namespace, expected_segment}]
//   stem_name_mismatch    : [{file, aspect}]
//   meta_model_drift      : [{using, latest}]
//   missing_release_notes : [<path>]                    (strings)
//   missing_release_date  : [<version>]                 (strings)
//   missing_gen_docs      : [<stem>]                    (strings)
//   non_semver_version    : [<version_dir>]             (strings)

// Issue ids whose detail items have {name, kind, file}.
const ISSUE_ELEMENT_LIST = new Set([
  "orphan_isolated", "orphan_unreachable",
  "key_element_missing_description", "key_element_missing_preferred_name",
  "empty_description", "description_not_english",
  "aspect_without_properties", "property_without_characteristic",
  "characteristic_without_datatype", "trait_without_constraint",
  "unused_property", "bad_naming_property", "bad_naming_type",
]);
// Issue ids whose detail items are bare strings.
const ISSUE_STRING_LIST = new Set([
  "circular_dep", "unresolved_dep", "missing_files",
  "dep_on_draft", "dep_on_undefined",
  "missing_release_notes", "missing_release_date",
  "missing_gen_docs", "non_semver_version",
]);

// Short one-liner for a catalog row (Issues top-tab list).
function issueSummary(id, p) {
  if (id === "deprecated_dep") return p.detail.map(x => x.target).join(", ");
  if (id === "outdated_dependency")
    return p.detail.map(x => `${x.target} → ${x.latest_release}`).join(", ");
  if (id === "older_release_exists")
    return p.detail.map(x => `newer release: ${x.latest_release}`).join(", ");
  if (id === "circular_dep") return "cycle with " + p.detail.join(", ");
  if (id === "namespace_mismatch")
    return p.detail.map(x => `${x.namespace}`).join(", ");
  if (id === "stem_name_mismatch")
    return p.detail.map(x => `${x.file}.ttl vs Aspect ${x.aspect}`).join(", ");
  if (id === "meta_model_drift")
    return p.detail.map(x => `${x.using} (latest ${x.latest})`).join(", ");
  if (ISSUE_STRING_LIST.has(id)) return p.detail.join(", ");
  if (ISSUE_ELEMENT_LIST.has(id)) {
    const head = p.detail.slice(0, 6).map(x => `${x.kind} ${x.name}`).join(", ");
    return head + (p.detail.length > 6 ? ` … (+${p.detail.length - 6})` : "");
  }
  return "";
}

// Full <ul> for the sub-tab (everything escaped: names can hold urn:<…>).
function issueDetailList(id, p) {
  const E = escapeHtml;
  let items;
  if (id === "deprecated_dep")
    items = p.detail.map(x =>
      `<li><b>${E(x.target)}</b>` +
      (x.path && x.path.length > 1
        ? ` <span class="pth">via ${x.path.map(E).join(" › ")}</span>`
        : "") + `</li>`);
  else if (id === "outdated_dependency")
    items = p.detail.map(x =>
      `<li><b>${E(x.target)}</b> ` +
      `<span class="pth">→ latest release ${E(x.latest_release)}</span></li>`);
  else if (id === "older_release_exists")
    items = p.detail.map(x =>
      `<li>newer release available: <b>${E(x.latest_release)}</b></li>`);
  else if (id === "namespace_mismatch")
    items = p.detail.map(x =>
      `<li><b>${E(x.file)}.ttl</b> declares <code>${E(x.namespace)}</code> ` +
      `<span class="pth">(expected segment ${E(x.expected_segment)})</span></li>`);
  else if (id === "stem_name_mismatch")
    items = p.detail.map(x =>
      `<li>file <b>${E(x.file)}.ttl</b> vs Aspect <b>${E(x.aspect)}</b></li>`);
  else if (id === "meta_model_drift")
    items = p.detail.map(x =>
      `<li>uses <b>${E(x.using)}</b> ` +
      `<span class="pth">(latest in catalog: ${E(x.latest)})</span></li>`);
  else if (ISSUE_STRING_LIST.has(id))
    items = p.detail.map(x => `<li>${E(x)}</li>`);
  else if (ISSUE_ELEMENT_LIST.has(id))
    items = p.detail.map(x =>
      `<li>${E(x.kind)} <b>${E(x.name)}</b> ` +
      `<span class="pth">(${E(x.file)})</span></li>`);
  else
    items = p.detail.map(x => `<li>${E(typeof x === "string" ? x : JSON.stringify(x))}</li>`);
  return `<ul class="idet">${items.join("")}</ul>`;
}

// ---- Issues : Model Viewer sub-tab (selected model+version only) ---------
function renderIssuesSub() {
  const el = document.getElementById("issues-body");
  const k = depKey(curEntry);
  if (!k) { el.textContent = "Pick a model and version above."; return; }
  if (!issuesData) {
    el.textContent = "issues.json not found — run: python -m sldt_analyzer.graph";
    return;
  }
  const m = issuesData.models[k];
  if (!m || !Object.keys(m.issues).length) {
    el.innerHTML = `<h2>${escapeHtml(k)}</h2>` +
      `<div class="ok-msg">No issues detected ✓</div>`;
    return;
  }
  let html = `<h2>${escapeHtml(m.name)} ` +
    `<span style="font-weight:400;color:#888;font-size:12px;">` +
    `v${escapeHtml(m.version || "—")} · ${escapeHtml(m.status)}</span></h2>`;
  issuesData.issue_types.forEach(t => {
    const p = m.issues[t.id];
    if (!p) return;
    const col = t.severity === "warning" ? "#f9a825" : "#b00020";
    html += `<h3><span class="sev" style="background:${col}"></span>` +
      `${escapeHtml(t.label)} ` +
      `<span style="color:#888;font-weight:400;">(${p.count})</span></h3>`;
    if (t.description)
      html += `<div class="iss-dsc">${escapeHtml(t.description)}</div>`;
    html += issueDetailList(t.id, p);
  });
  el.innerHTML = html;
}

// ---- Generated docs (upstream gen/ HTML, fetched via jsDelivr) ------------
// jsDelivr serves repo files with CORS (access-control-allow-origin: *) but
// as text/plain, so we fetch the text and render it via the iframe's srcdoc
// (works in a sandboxed iframe; a blob: URL would be origin-blocked there).
// No self-hosting — the doc is self-contained (inline SVG). The Blob URL is
// only for the external "Open full page" link (real tab, renders fine).
// CDN base is picked per source (catenax vs idta) via `cdnFor(curEntry)`.
// docState.aspectBase = currently selected gen.aspects[] item (Feature 21).
// Cache key is `${entry.id}|${aspectBase}` so different aspects of the same
// model don't clobber each other.
let docState = { id: null, text: null, url: null, ctrl: null,
                 aspectBase: null, cacheKey: null };

// Picks which gen artifact (base + bools) to render. For 1-Aspect models we
// keep the legacy `g` object (base/html/schema/...). For multi-Aspect models
// we pick the selected aspect from `g.aspects` (or default to the first).
function pickAspect(g, selectedBase) {
  if (g && Array.isArray(g.aspects) && g.aspects.length > 1) {
    return g.aspects.find(a => a.base === selectedBase) || g.aspects[0];
  }
  return g;          // single-aspect / no-aspect : the legacy shape works
}

function renderDocs() {
  const msg = document.getElementById("docs-msg");
  const frame = document.getElementById("docs-frame");
  const open = document.getElementById("docs-open");
  const links = document.getElementById("docs-links");
  const title = document.getElementById("docs-title");
  const sel = document.getElementById("docs-aspect");
  const show = (el, on) => { el.style.display = on ? "" : "none"; };

  const g = curEntry && curEntry.gen;
  if (!curEntry) {
    title.textContent = ""; links.innerHTML = ""; show(open, false);
    show(sel, false); show(frame, false); show(msg, true);
    msg.textContent = "Pick a model and version above.";
    return;
  }
  title.textContent = `${curEntry.model_name} v${curEntry.version}`;
  const cdn = cdnFor(curEntry);

  // Build / update the Aspect picker for multi-Aspect models.
  const hasMulti = g && Array.isArray(g.aspects) && g.aspects.length > 1;
  if (hasMulti) {
    // (Re)build options only when the entry's aspect list changed —
    // otherwise we'd reset the user's selection on every re-render.
    if (sel.dataset.entryId !== curEntry.id) {
      sel.innerHTML = g.aspects.map(a =>
        `<option value="${a.base}">${escapeHtml(a.name)}</option>`).join("");
      sel.value = g.aspects[0].base;
      sel.dataset.entryId = curEntry.id;
      docState.aspectBase = sel.value;
    } else if (!docState.aspectBase) {
      docState.aspectBase = sel.value;
    }
    show(sel, true);
  } else {
    sel.innerHTML = "";
    sel.removeAttribute("data-entry-id");
    show(sel, false);
    docState.aspectBase = null;
  }

  const a = pickAspect(g, docState.aspectBase);
  const baseUrl = a && a.base;

  // Companion artifacts (open in a new tab on jsDelivr).
  const arts = [["schema", "JSON Schema", "-schema.json"],
                ["payload", "Sample payload", ".json"],
                ["openapi", "OpenAPI", ".yml"]];
  links.innerHTML = arts
    .filter(([k]) => a && a[k])
    .map(([, label, suf]) =>
      `<a href="${cdn}${baseUrl}${suf}" target="_blank" rel="noopener">${label}</a>`)
    .join("");

  if (!a || !a.html) {
    frame.srcdoc = ""; show(open, false); show(frame, false); show(msg, true);
    msg.textContent = "No generated documentation upstream for this model+version.";
    return;
  }

  // Cache key includes the aspect so switching between aspects re-fetches
  // (but switching back uses the cache).
  const cacheKey = `${curEntry.id}|${baseUrl}`;
  if (docState.cacheKey === cacheKey && docState.text != null) {
    frame.srcdoc = docState.text;
    open.href = docState.url; show(open, true);
    show(msg, false); show(frame, true);
    return;
  }

  if (docState.ctrl) docState.ctrl.abort();
  const ctrl = new AbortController();
  docState.ctrl = ctrl;
  const wantKey = cacheKey;
  frame.srcdoc = ""; show(open, false); show(frame, false); show(msg, true);
  msg.textContent = "Loading generated doc… (can be a few MB)";

  fetch(`${cdn}${baseUrl}.html`, { signal: ctrl.signal })
    .then(r => r.ok ? r.text() : Promise.reject("HTTP " + r.status))
    .then(htmlText => {
      // wantKey check guards against stale fetches when the user switches
      // entry / aspect mid-flight.
      if (ctrl.signal.aborted || !curEntry || cacheKey !== wantKey) return;
      if (docState.url) URL.revokeObjectURL(docState.url);
      docState.text = htmlText;
      docState.url = URL.createObjectURL(
        new Blob([htmlText], { type: "text/html" }));
      docState.id = curEntry.id;
      docState.cacheKey = wantKey;
      frame.srcdoc = htmlText;
      open.href = docState.url; show(open, true);
      show(msg, false); show(frame, true);
    })
    .catch(err => {
      if (ctrl.signal.aborted) return;
      show(frame, false); show(msg, true);
      msg.textContent = `Could not load the generated doc (${err}).`;
    });
}

// React to the user picking another Aspect — re-render with the new selection.
(function bindDocsAspect() {
  const sel = document.getElementById("docs-aspect");
  if (sel) sel.addEventListener("change", function () {
    docState.aspectBase = this.value;
    renderDocs();
  });
})();

// Select a model (auto-loads its latest version; notes are model-level).
function selectModel(name) {
  curModel = name;
  document.getElementById("mv-model").value = name;
  curEntries = groups.get(name) || [];
  fillVersionSelect(curEntries);
  document.getElementById("mv-version").value = 0;
  curEntry = curEntries[0] || null;  // so notes/dep render at once, no flash
  renderSub();                       // show notes at once (model-level)
  if (curEntries.length) loadEntry(curEntries[0]);
}
document.getElementById("mv-version").addEventListener("change", function () {
  if (curEntries[+this.value]) loadEntry(curEntries[+this.value]);
});

// From the Home catalog: open this exact model+version in Model Viewer.
function openEntry(e) {
  showTab("modelviewer");
  curModel = e.model_name;
  document.getElementById("mv-model").value = e.model_name;
  curEntries = groups.get(e.model_name) || [];
  fillVersionSelect(curEntries);
  const i = curEntries.findIndex(x => x.id === e.id);
  document.getElementById("mv-version").value = i < 0 ? 0 : i;
  curEntry = curEntries[i < 0 ? 0 : i] || null;
  // Sync URL : #/model/<id>/<sub>. showTab above pushed a stale URL (without
  // the id) ; this overrides with the precise one.
  if (curEntry)
    setUrl(buildHash(URL_BY_VIEW.modelviewer,
      `${curEntry.id}/${activeSub.replace(/^sv-/, "")}`));
  renderSub();
  loadEntry(curEntries[i < 0 ? 0 : i]);
}

// Resolve a "name@version" dep key to a catalog entry (prefer one with an
// Aspect) so a dependency node can be opened in the Model Viewer.
function entryForKey(key) {
  const at = key.lastIndexOf("@");
  const name = key.slice(0, at), ver = key.slice(at + 1);
  const cands = allModels.filter(e => e.model_name === name && e.version === ver);
  return cands.find(e => e.has_aspect) || cands[0] || null;
}

// ---- Home (KPI + catalog) ------------------------------------------------
let statusFilter = null;
let catSrcFilter = new Set(SOURCE_ORDER);   // both sources on by default
let catStdOnly = false;        // when on, keep only models used in a standard
const catOpen = new Set();     // model_name of expanded catalog rows

// True when this exact model+version is referenced by a Catena-X standard
// (standards.json `models` map). False if the standards data isn't loaded yet.
function inAnyStandard(e) {
  return !!(standardsData && standardsData.models
    && standardsData.models[`${e.model_name}@${e.version}`]);
}

// Last-modified is captured from the response headers (GitHub Pages and
// http.server both set it) — surfaced in the Overview strip.
let indexLastModified = null;

fetch("data/graph/index.json")
  .then(r => { indexLastModified = r.headers.get("last-modified"); return r.json(); })
  .then(idx => {
    allModels = idx;
    statusByKey = null;            // rebuild status/source lookups from fresh catalog
    sourceByKey = null;
    buildGroups();
    setupModelSearch();
    renderOverview();
    renderKpis();
    renderSourceFilter();
    renderCatalog();
    applyHash();                   // resolve #/model/<id> now that allModels is ready
  })
  .catch(() => {
    document.getElementById("catalog").textContent =
      "index.json not found — run: python -m sldt_analyzer.graph";
    document.getElementById("mv-empty").textContent =
      "index.json not found — generate it: python -m sldt_analyzer.graph";
  });

// Renders the 4 mini-stat cards above the KPIs. Re-called when issues.json
// arrives so the "Quality" card upgrades from "—" to its real count.
function renderOverview() {
  if (!allModels) return;
  const box = document.getElementById("overview");
  if (!box) return;

  // (1) Models : total + per-source breakdown.
  const total = allModels.length;
  const bySrc = {};
  SOURCE_ORDER.forEach(s => {
    bySrc[s] = allModels.filter(m => m.source === s).length;
  });

  // (2) Family : SAMM / BAMM tally. Note: in index.json the field is `family`
  // (the meta has `model_family`, but the index summary uses the short name).
  const fam = {};
  allModels.forEach(m => (fam[m.family] = (fam[m.family] || 0) + 1));

  // (3) Quality : total volume of issues across release models (the actionable
  // signal — it actually moves), not a "flagged / total" fraction that read as
  // a failing score. Depends on issues.json ; clicking opens the Issues tab.
  let quality = "—", qualitySub = "loading…";
  if (issuesData) {
    const relIssues = Object.values(issuesData.models)
      .filter(v => v.status === "release");
    const flaggedRelease = relIssues.length;
    const totalIssues = relIssues.reduce((s, v) => s + (v.total || 0), 0);
    quality = totalIssues.toLocaleString();
    qualitySub = `issues · ${flaggedRelease} release models flagged`;
  }

  // (4) Updated : Last-Modified header (raw HTTP date) prettified.
  const upd = indexLastModified ? new Date(indexLastModified) : null;
  const updStr = upd
    ? upd.toLocaleDateString(undefined,
        { year: "numeric", month: "short", day: "numeric" })
    : "—";

  // (5) Standards : count + how many catalog models are linked to a standard
  // (depends on standards.json). Clicking opens the Standards tab.
  let stdVal = "—", stdSub = "loading…";
  if (standardsData) {
    const nStd = Object.keys(standardsData.standards || {}).length;
    const nLinked = Object.keys(standardsData.models || {}).length;
    stdVal = nStd;
    stdSub = `${nLinked} models linked · release ${standardsData.release || "?"}`;
  }

  const famSub = Object.entries(fam).sort()
    .map(([k, v]) => `${k} ${v}`).join(" · ");
  const srcSub = SOURCE_ORDER.map(s =>
    `<span><span class="d" style="background:${sourceColor(s)}"></span>` +
    `${sourceLabel(s)} ${bySrc[s]}</span>`).join("");

  box.innerHTML =
    `<div class="ov"><div class="lbl">Models</div>` +
      `<div class="val">${total}</div>` +
      `<div class="sub">${srcSub}</div></div>` +
    `<div class="ov"><div class="lbl">Family</div>` +
      `<div class="val txt">${famSub || "—"}</div>` +
      `<div class="sub">meta-model breakdown</div></div>` +
    `<div class="ov ov-link" data-goto="issues"><div class="lbl">Quality</div>` +
      `<div class="val">${quality}</div>` +
      `<div class="sub">${qualitySub}</div></div>` +
    `<div class="ov ov-link" data-goto="standardsview"><div class="lbl">Standards</div>` +
      `<div class="val">${stdVal}</div>` +
      `<div class="sub">${stdSub}</div></div>` +
    `<div class="ov"><div class="lbl">Updated</div>` +
      `<div class="val txt">${updStr}</div>` +
      `<div class="sub">` +
        `<a href="https://github.com/${REPO_BY_SOURCE.catenax}" target="_blank" rel="noopener">Catena-X repo ↗</a>` +
        `<a href="https://github.com/${REPO_BY_SOURCE.idta}" target="_blank" rel="noopener">IDTA repo ↗</a>` +
        `<a href="https://github.com/catenax-eV/catenax-ev.github.io" target="_blank" rel="noopener">Standard Library ↗</a>` +
      `</div></div>`;

  // Overview cards flagged `.ov-link` are shortcuts to another tab
  // (Quality → Issues, Standards → Standards).
  box.querySelectorAll(".ov-link[data-goto]").forEach(card =>
    card.addEventListener("click", () => showTab(card.dataset.goto)));
}

// Dependency map (loaded once). If the Dependency tab is already open when it
// arrives, re-render it.
fetch("data/graph/deps.json")
  .then(r => r.json())
  .then(m => { depsMap = m; revMap = null; if (activeSub === "sv-dep") renderSub(); })
  .catch(() => { depsMap = null; });

// Standards ↔ models link (loaded once). Powers the Home "Standards" card,
// the Standards tab, and the model↔standards cross-links. Small (~56 KB) so
// loaded eagerly like deps.json.
let standardsData = null;          // standards.json { release, standards, deprecated_standards, models }
let stdSelId = null;               // currently selected CX-XXXX in the Standards tab
let stdRevMap = null;              // built lazily : CX-id -> [CX that reference it]
fetch("data/graph/standards.json")
  .then(r => r.json())
  .then(s => {
    standardsData = s;
    stdRevMap = null;
    renderOverview();              // upgrade the "Standards" card from loading…
    // Home depends on this data: the "used in a standard" filter and the
    // per-version standard chips only resolve once standards.json is in.
    if (allModels) { renderKpis(); renderCatalog(); }
    if (activeView === "standardsview") renderStandards();
    // Model header chips depend on this data — refresh if a model is already open.
    if (curEntry)
      document.getElementById("mv-badges").innerHTML =
        badges(curEntry) + standardsBadges(curEntry);
    applyHash();                   // resolve #/standards/<id> now that data is ready
  })
  .catch(() => { standardsData = null; });

// ---- Standards tab --------------------------------------------------------
const STD_COLORS = { root: "#4363d8", active: "#3cb44b",
                     deprecated: "#b00020", absent: "#9e9e9e",
                     model: "#00897b" };
let stdDir = "both";       // reference direction in the graph: both | out | in
let stdLayout = "ego";     // graph layout: ego (sides) | freeze | force | circle | grid | hier
// Which outgoing reference categories to draw. Normative on by default ; the
// user can add the others. "models" toggles the semantic-model nodes.
let stdCats = new Set(["normative", "models"]);
let stdIssuesOnly = false;     // Standards list: keep only flagged standards
let stdSelType = null;         // selected standard-issue type (filters the list)
let stdSim = null;

function buildStdRevMap() {
  const r = {};
  const st = (standardsData && standardsData.standards) || {};
  for (const [cx, s] of Object.entries(st))
    [...(s.normative || []), ...(s.non_normative || [])]
      .forEach(t => (r[t] = r[t] || []).push(cx));
  for (const k in r) r[k].sort();
  return r;
}
const stdIsActive = id =>
  !!(standardsData && standardsData.standards && standardsData.standards[id]);
const stdIsDeprecated = id =>
  !!(standardsData && standardsData.deprecated_standards
     && standardsData.deprecated_standards[id]);

// Standard-level quality issues, precomputed in standards.json. Returns the
// firing types as [{id, label, items[]}] (empty items dropped) so the list
// badge and the detail "Quality issues" section share one source of truth.
function stdIssues(id) {
  const s = standardsData && standardsData.standards
    && standardsData.standards[id];
  if (!s) return [];
  return (standardsData.standard_issue_types || [])
    .map(t => ({ id: t.id, label: t.label, items: s[t.field] || [] }))
    .filter(t => t.items.length);
}
// Total flagged references across all issue types (drives the ⚠ list badge).
const stdIssueCount = id =>
  stdIssues(id).reduce((n, t) => n + t.items.length, 0);
// Does a given standard fire a specific issue type? (drives the KPI filter)
function stdTypeFires(cxid, typeId) {
  const s = standardsData && standardsData.standards
    && standardsData.standards[cxid];
  if (!s) return false;
  const t = (standardsData.standard_issue_types || []).find(x => x.id === typeId);
  return !!(t && (s[t.field] || []).length);
}

// Aggregated standard-issues summary at the top of the Standards tab : one KPI
// card per type (count of standards in error) + a collapsible explanation
// panel. Clicking a card filters the list (left) to those standards.
function renderStdKpis() {
  const kbox = document.getElementById("std-kpis");
  if (!kbox || !standardsData) return;
  const types = standardsData.standard_issue_types || [];
  const ids = Object.keys(standardsData.standards);
  kbox.innerHTML = "";
  types.forEach(t => {
    const n = ids.filter(id =>
      ((standardsData.standards[id][t.field]) || []).length).length;
    kbox.appendChild(issueKpiCard({
      count: n, label: t.label, sev: t.severity, description: t.description,
      selected: stdSelType === t.id,
      onClick: () => {
        stdSelType = stdSelType === t.id ? null : t.id;
        renderStdKpis(); renderStdList();
      },
    }));
  });
  // "About these checks" panel — built once (shared helper).
  buildHelpPanel("#std-help", types);
}

// Strip the leading "CX-XXXX" from a standard title (the title repeats the id).
function stripCx(id, title) {
  if (!title) return "";
  return title.startsWith(id)
    ? title.slice(id.length).replace(/^[\s:.\-]+/, "") : title;
}

function renderStandards() {
  const rel = document.getElementById("std-release");
  if (rel) rel.textContent = (standardsData && standardsData.release) || "…";
  const list = document.getElementById("std-list");
  if (!standardsData) {
    if (list) list.innerHTML =
      '<div class="std-row std-empty">standards.json not found — run: '
      + 'python -m sldt_analyzer.graph</div>';
    return;
  }
  if (!stdRevMap) stdRevMap = buildStdRevMap();
  const q = document.getElementById("std-q");
  if (q && !q.dataset.wired) {
    q.dataset.wired = "1";
    q.addEventListener("input", renderStdList);
  }
  const issOnly = document.querySelector("#std-iss-only input");
  if (issOnly && !issOnly.dataset.wired) {
    issOnly.dataset.wired = "1";
    issOnly.checked = stdIssuesOnly;
    issOnly.addEventListener("change", function () {
      stdIssuesOnly = this.checked;
      renderStdList();
    });
  }
  renderStdKpis();
  renderStdList();
  if (stdSelId) selectStandard(stdSelId);
}

function renderStdList() {
  const box = document.getElementById("std-list");
  const cnt = document.getElementById("std-count");
  if (!box || !standardsData) return;
  const term = ((document.getElementById("std-q") || {}).value || "")
    .trim().toLowerCase();
  const ids = Object.keys(standardsData.standards).sort();
  const shown = ids
    .filter(id => !term || id.toLowerCase().includes(term)
      || (standardsData.standards[id].title || "").toLowerCase().includes(term))
    .filter(id => !stdIssuesOnly || stdIssueCount(id) > 0)
    .filter(id => !stdSelType || stdTypeFires(id, stdSelType));
  if (cnt) cnt.textContent = `${shown.length} / ${ids.length} standards`;
  box.innerHTML = "";
  if (!shown.length) {
    box.innerHTML = '<div class="std-row std-empty">No standard matches.</div>';
    return;
  }
  shown.forEach(id => {
    const s = standardsData.standards[id];
    const nM = (s.semantic_models || []).length;
    const nR = (s.normative || []).length + (s.non_normative || []).length;
    const nI = stdIssueCount(id);
    const row = document.createElement("div");
    row.className = "std-row" + (id === stdSelId ? " sel" : "");
    row.innerHTML =
      `<div class="top"><span class="id">${escapeHtml(id)}</span>` +
        (nI ? `<span class="ibadge" title="${nI} quality ` +
              `issue${nI === 1 ? "" : "s"}">⚠ ${nI}</span>` : "") + `</div>` +
      `<div class="ti">${escapeHtml(stripCx(id, s.title))}</div>` +
      `<div class="mt">${nM} model${nM === 1 ? "" : "s"} · ` +
        `${nR} ref${nR === 1 ? "" : "s"}</div>`;
    row.addEventListener("click", () => selectStandard(id));
    box.appendChild(row);
  });
}

function highlightStdRow(id) {
  document.querySelectorAll("#std-list .std-row").forEach(r => {
    const e = r.querySelector(".id");
    r.classList.toggle("sel", !!e && e.textContent === id);
  });
}

// Detail skeleton built once so the SVG (and its zoom binding) stays stable
// across selections.
let sgContainer = null;
function ensureStdDetailSkeleton() {
  const d = document.getElementById("std-detail");
  if (d.dataset.built) return;
  d.dataset.built = "1";
  d.classList.remove("std-ph");
  // References (text) live in #std-detail (top) ; the reference graph lives
  // in #std-graphwrap (full-width, below) so it gets all the room.
  d.innerHTML = '<div id="std-d-head"></div><div id="std-d-body"></div>';
  const gw = document.getElementById("std-graphwrap");
  gw.innerHTML =
    '<div id="std-gctrls">' +
      '<div class="seg">' +
        `<button data-sdir="both"${stdDir === "both" ? ' class="on"' : ""}>Both</button>` +
        `<button data-sdir="out"${stdDir === "out" ? ' class="on"' : ""}>Outgoing →</button>` +
        `<button data-sdir="in"${stdDir === "in" ? ' class="on"' : ""}>← Incoming</button>` +
      '</div>' +
      '<select id="std-layout-sel" title="Graph layout">' +
        '<option value="ego">Sides (in / out)</option>' +
        '<option value="freeze">Static (auto-layout)</option>' +
        '<option value="hier">Hierarchy (layered)</option>' +
        '<option value="force">Force (animated)</option>' +
        '<option value="circle">Circle</option>' +
        '<option value="grid">Grid</option>' +
      '</select>' +
      '<div class="catf"><span class="lbl">Show:</span>' +
        `<label class="fpill"><input type="checkbox" data-cat="normative"${stdCats.has("normative") ? " checked" : ""}><span class="dot"></span><span>Normative</span></label>` +
        `<label class="fpill"><input type="checkbox" data-cat="non_normative"${stdCats.has("non_normative") ? " checked" : ""}><span class="dot"></span><span>Non-norm.</span></label>` +
        `<label class="fpill" style="--c:${STD_COLORS.model}"><input type="checkbox" data-cat="models"${stdCats.has("models") ? " checked" : ""}><span class="dot"></span><span>Models</span></label>` +
      '</div>' +
    '</div>' +
    '<div class="std-glegend">' +
      '<div class="grp">' +
        '<span class="glab">Standards (▭):</span>' +
        `<span><span class="dot rect ring" style="border-color:${STD_COLORS.root}"></span>selected</span>` +
        `<span><span class="dot rect" style="background:${STD_COLORS.active}"></span>active</span>` +
        `<span><span class="dot rect" style="background:${STD_COLORS.deprecated}"></span>deprecated</span>` +
        `<span><span class="dot rect" style="background:${STD_COLORS.absent}"></span>other</span>` +
      '</div>' +
      '<div class="grp">' +
        '<span class="glab">Models (○, ring = status):</span>' +
        `<span><span class="dot" style="background:${STD_COLORS.model}"></span>model</span>` +
        `<span><span class="dot ring" style="border-color:${statusColor("release")}"></span>release</span>` +
        `<span><span class="dot ring" style="border-color:${statusColor("draft")}"></span>draft</span>` +
        `<span><span class="dot ring" style="border-color:${statusColor("deprecated")}"></span>deprecated</span>` +
        `<span><span class="dot ring" style="border-color:${statusColor("undefined")}"></span>undefined</span>` +
      '</div>' +
      '<div class="grp"><span class="gsep">← referenced by · references / models →</span></div>' +
    '</div>' +
    '<svg id="stdgraph"></svg>';
  const sg = d3.select("#stdgraph");
  sgContainer = sg.append("g");
  sg.call(d3.zoom().scaleExtent([0.1, 4]).on("zoom",
    e => sgContainer.attr("transform", e.transform)));
  sg.append("defs").append("marker")
    .attr("id", "sarrow").attr("viewBox", "0 -5 10 10").attr("refX", 10)
    .attr("markerWidth", 7).attr("markerHeight", 7).attr("orient", "auto")
    .append("path").attr("d", "M0,-5L10,0L0,5").attr("fill", "#777");

  // Direction toggle + layout selector — redraw the graph for the selection.
  gw.querySelectorAll("#std-gctrls .seg button").forEach(b =>
    b.addEventListener("click", () => {
      stdDir = b.dataset.sdir;
      gw.querySelectorAll("#std-gctrls .seg button")
        .forEach(x => x.classList.toggle("on", x.dataset.sdir === stdDir));
      if (stdSelId) drawStdGraph(stdSelId);
    }));
  const sel = gw.querySelector("#std-layout-sel");
  sel.value = stdLayout;
  sel.addEventListener("change", function () {
    stdLayout = this.value;
    if (stdSelId) drawStdGraph(stdSelId);
  });
  gw.querySelectorAll("#std-gctrls .catf input[data-cat]").forEach(cb =>
    cb.addEventListener("change", () => {
      if (cb.checked) stdCats.add(cb.dataset.cat);
      else stdCats.delete(cb.dataset.cat);
      if (stdSelId) drawStdGraph(stdSelId);
    }));
}

function selectStandard(id) {
  if (!standardsData) return;
  stdSelId = id;
  setUrl(buildHash(URL_BY_VIEW.standardsview, id));
  highlightStdRow(id);
  ensureStdDetailSkeleton();
  renderStdDetail(id);
}

// One reference chip. Clickable when the target is a known (active or
// deprecated) standard ; otherwise dimmed (referenced but unknown/withdrawn).
function stdChip(cx) {
  const dep = stdIsDeprecated(cx), active = stdIsActive(cx);
  const clickable = active || dep;
  const cls = "chip" + (dep ? " dep" : (clickable ? "" : " dim"));
  return `<span class="${cls}"${clickable ? ` data-cx="${escapeHtml(cx)}"` : ""}>`
    + `${escapeHtml(cx)}</span>`;
}
const CHIP_FOLD = 12;
// Wraps a list of chip-HTML strings ; beyond CHIP_FOLD the extras are hidden
// (CSS nth-child) behind a "+N more" button (wired in renderStdDetail).
function chipList(chipsHtml) {
  const foldable = chipsHtml.length > CHIP_FOLD;
  return `<div class="std-chips${foldable ? " foldable" : ""}">`
    + `${chipsHtml.join("")}</div>`
    + (foldable
      ? `<button type="button" class="std-more">+${chipsHtml.length - CHIP_FOLD} more</button>`
      : "");
}
function refGroup(title, arr, desc) {
  if (!arr || !arr.length) return "";
  return `<div class="std-grp"><div class="h">${title} (${arr.length})</div>`
    + (desc ? `<div class="std-note">${desc}</div>` : "")
    + chipList(arr.map(stdChip)) + `</div>`;
}
// One model chip ("name@version"). Clickable (data-model) when the model is in
// the catalog ; dimmed otherwise (cited but not ingested).
function modelChip(mk) {
  const at = mk.lastIndexOf("@");
  const name = mk.slice(0, at), ver = mk.slice(at + 1);
  const inCat = !!entryForKey(mk);
  return `<span class="chip${inCat ? "" : " dim"}"`
    + `${inCat ? ` data-model="${escapeHtml(mk)}"` : ""}>`
    + `${escapeHtml(name)} <span class="v">v${escapeHtml(ver)}</span></span>`;
}
function modelGroup(arr) {
  if (!arr || !arr.length) return "";
  return `<div class="std-grp"><div class="h">Semantic models (${arr.length})`
    + `</div>` + chipList(arr.map(modelChip)) + `</div>`;
}

// "Quality issues" section for a standard (Option A). Deprecated standard refs
// render with their deprecation note ; deprecated/BAMM model refs render as the
// usual model chips (clickable into the Model Viewer when in the catalog).
function qualityIssuesGroup(id) {
  const issues = stdIssues(id);
  if (!issues.length) return "";
  const block = issues.map(t => {
    let rows;
    if (t.id === "deprecated-standard-ref") {
      rows = t.items.map(cx => {
        const d = (standardsData.deprecated_standards || {})[cx] || {};
        return `<div class="std-iss-row"><b>${escapeHtml(cx)}</b> `
          + `${escapeHtml(d.name || "")} — deprecated in `
          + `${escapeHtml(d.deprecated_in || "?")}`
          + (d.reason ? `: ${escapeHtml(d.reason)}` : "") + `</div>`;
      }).join("");
    } else {
      rows = chipList(t.items.map(modelChip));
    }
    return `<div class="std-iss"><div class="ih">⚠ ${escapeHtml(t.label)} `
      + `(${t.items.length})</div>${rows}</div>`;
  }).join("");
  return `<div class="std-grp"><div class="h">Quality issues</div>${block}</div>`;
}

function renderStdDetail(id) {
  const s = standardsData.standards[id];
  const dep = (standardsData.deprecated_standards || {})[id];
  const head = document.getElementById("std-d-head");
  const body = document.getElementById("std-d-body");

  const title = s ? s.title : (dep ? `${id} ${dep.name}` : id);
  head.innerHTML =
    `<h2>${escapeHtml(title)}</h2>` +
    (s && s.link
      ? `<div class="lk"><a href="${escapeHtml(s.link)}" target="_blank" `
        + `rel="noopener">Open standard ↗</a></div>` : "") +
    (dep ? `<div class="std-dep-note"><b>Deprecated</b> in `
      + `${escapeHtml(dep.deprecated_in)} — ${escapeHtml(dep.reason)}</div>` : "");

  drawStdGraph(id);

  let html = "";
  if (s) {
    html += refGroup("Normative references", s.normative);
    html += refGroup("Non-normative references", s.non_normative,
      "Includes standards referenced outside the formal Non-normative "
      + "References section (e.g. inline text or a “standalone standards” "
      + "list) — everything cited that is not normative.");
    html += qualityIssuesGroup(id);
    html += modelGroup(s.semantic_models);
  }
  html += refGroup("Referenced by", stdRevMap[id] || []);
  body.innerHTML = html || '<div class="std-empty">No reference data.</div>';

  // "+N more" reveals the folded chips of its group, then removes itself.
  body.querySelectorAll(".std-more").forEach(btn =>
    btn.addEventListener("click", () => {
      const chips = btn.previousElementSibling;
      if (chips) chips.classList.add("show-all");
      btn.remove();
    }));

  body.querySelectorAll(".chip[data-cx]").forEach(c =>
    c.addEventListener("click", () => selectStandard(c.dataset.cx)));
  body.querySelectorAll(".chip[data-model]").forEach(c =>
    c.addEventListener("click", () => {
      const ent = entryForKey(c.dataset.model);
      if (ent) openEntry(ent);
    }));
}

// Ego reference graph : selected standard centered, outgoing references on the
// right, incoming (referenced-by) on the left. 1 hop ; click an active/
// deprecated node to re-center.
// Fill = identity/status (NOT the "selected" role) so a selected deprecated
// standard still reads red. The selected node is marked by a blue ring instead
// (see the stroke in drawStdGraph).
function stdNodeColor(d) {
  if (d.type === "model") return STD_COLORS.model;
  if (stdIsDeprecated(d.id)) return STD_COLORS.deprecated;
  if (stdIsActive(d.id)) return STD_COLORS.active;
  return STD_COLORS.absent;
}
function stdNodeClickable(d, rootId) {
  if (d.type === "model") return !!entryForKey(d.mk);
  return d.id !== rootId && (stdIsActive(d.id) || stdIsDeprecated(d.id));
}
function stdNodeTip(d, rootId) {
  if (d.type === "model") {
    const mk = d.mk, at = mk.lastIndexOf("@");
    return `<b>${escapeHtml(mk.slice(0, at))}</b> `
      + `<span class="k">v${escapeHtml(mk.slice(at + 1))}</span><br>semantic model`
      + (entryForKey(mk) ? "<br><i>click to open in Model Viewer</i>"
                         : "<br><i>not in catalog</i>");
  }
  const s2 = standardsData.standards[d.id];
  const dd = (standardsData.deprecated_standards || {})[d.id];
  return `<b>${escapeHtml(d.id)}</b><br>`
    + (s2 ? escapeHtml(stripCx(d.id, s2.title))
          : (dd ? escapeHtml(dd.name) : "<i>unknown standard</i>"))
    + (dd ? `<br><span style="color:${STD_COLORS.deprecated}">deprecated in `
        + `${escapeHtml(dd.deprecated_in)}</span>` : "")
    + (d.id !== rootId && (s2 || dd) ? "<br><i>click to focus</i>" : "");
}
function placeEgo(nodes, width, height) {
  const cx = width / 2, cy = height / 2;
  const colX = Math.max(110, Math.min(width / 2 - 50, 240));
  const spread = (arr, x) => {
    const n = arr.length;
    arr.forEach((d, i) => {
      d.x = x;
      d.y = n <= 1 ? cy
        : cy - height * 0.42 + (i * height * 0.84) / (n - 1);
    });
  };
  nodes.filter(n => n.side === "root").forEach(d => { d.x = cx; d.y = cy; });
  spread(nodes.filter(n => n.side === "in"), cx - colX);
  spread(nodes.filter(n => n.side === "out" || n.side === "both"), cx + colX);
}
function drawStdGraph(rootId) {
  if (!sgContainer) return;
  sgContainer.selectAll("*").remove();
  if (stdSim) { stdSim.stop(); stdSim = null; }
  const svgEl = d3.select("#stdgraph").node();
  if (!svgEl) return;
  const { width, height } = svgEl.getBoundingClientRect();
  if (!width || !height) return;       // tab not visible yet

  const s = standardsData.standards[rootId];
  const showOut = stdDir === "both" || stdDir === "out";
  const showIn = stdDir === "both" || stdDir === "in";
  // Outgoing references restricted to the selected categories (normative on by
  // default ; non-normative is opt-in).
  let out = [];
  if (showOut && s) {
    const norm = s.normative || [], nonn = s.non_normative || [];
    const set = new Set();
    if (stdCats.has("normative")) norm.forEach(x => set.add(x));
    if (stdCats.has("non_normative")) nonn.forEach(x => set.add(x));
    out = [...set];
  }
  const inc = showIn ? (stdRevMap[rootId] || []).filter(x => x !== rootId) : [];
  const models = (showOut && s && stdCats.has("models"))
    ? (s.semantic_models || []) : [];

  const nodeMap = new Map();
  const addStd = (id, side) => {
    if (!nodeMap.has(id))
      nodeMap.set(id, { id, side, type: "std", label: id });
    else if (side === "out" && nodeMap.get(id).side === "in")
      nodeMap.get(id).side = "both";
  };
  nodeMap.set(rootId, { id: rootId, side: "root", type: "std", label: rootId });
  inc.forEach(id => addStd(id, "in"));
  out.forEach(id => { if (id !== rootId) addStd(id, "out"); });
  models.forEach(mk => {
    const id = "model:" + mk;
    if (!nodeMap.has(id)) nodeMap.set(id, {
      id, side: "out", type: "model", mk,
      label: mk.slice(0, mk.lastIndexOf("@")),
    });
  });
  const nodes = [...nodeMap.values()];
  const byId = new Map(nodes.map(n => [n.id, n]));
  let links = [];
  out.forEach(id => { if (id !== rootId)
    links.push({ source: rootId, target: id }); });
  inc.forEach(id => links.push({ source: id, target: rootId }));
  models.forEach(mk => links.push({ source: rootId, target: "model:" + mk }));

  if (stdLayout === "ego") {
    placeEgo(nodes, width, height);
    links.forEach(l => { l.source = byId.get(l.source); l.target = byId.get(l.target); });
  } else {
    layoutMode = stdLayout;
    stdSim = makeSim(nodes, links, width, height,
      { distance: 95, charge: -300, collide: 30,
        hierarchy: { rootIds: [rootId], directionFlip: stdDir === "in" } });
  }

  // Trim radius per node : models carry a status ring (outer r≈15.5), so the
  // arrow must stop a bit further out than for the standard rectangles.
  const nodeR = d => d && d.type === "model" ? 16 : 14;
  function endpoints(d) {
    const sx = d.source.x, sy = d.source.y, tx = d.target.x, ty = d.target.y;
    let dx = tx - sx, dy = ty - sy;
    const dist = Math.hypot(dx, dy) || 1; dx /= dist; dy /= dist;
    const cap = dist / 2 - 1;
    const rs = Math.max(0, Math.min(nodeR(d.source), cap));
    const rt = Math.max(0, Math.min(nodeR(d.target), cap));
    return { x1: sx + dx * rs, y1: sy + dy * rs, x2: tx - dx * rt, y2: ty - dy * rt };
  }
  const link = sgContainer.append("g").selectAll("line").data(links).enter()
    .append("line").attr("class", "link").attr("stroke-width", 1.4)
    .attr("marker-end", "url(#sarrow)");
  function ticked() {
    link.each(function (d) {
      const e = endpoints(d);
      this.setAttribute("x1", e.x1); this.setAttribute("y1", e.y1);
      this.setAttribute("x2", e.x2); this.setAttribute("y2", e.y2);
    });
    node.attr("transform", d => `translate(${d.x},${d.y})`);
  }
  const drag = stdLayout === "ego"
    ? d3.drag()
        .on("start", (e, d) => { d.fx = d.x; d.fy = d.y; })
        .on("drag", (e, d) => { d.x = e.x; d.y = e.y; ticked(); })
        .on("end", () => {})
    : dragger(stdSim, ticked);
  const node = sgContainer.append("g").selectAll("g").data(nodes).enter()
    .append("g").attr("class", "node").call(drag)
    .style("cursor", d => stdNodeClickable(d, rootId) ? "pointer" : "default")
    .on("mouseover", (e, d) => tooltip.style("opacity", 1).html(stdNodeTip(d, rootId)))
    .on("mousemove", e => tooltip
      .style("left", (e.pageX + 14) + "px").style("top", (e.pageY + 8) + "px"))
    .on("mouseout", () => tooltip.style("opacity", 0))
    .on("click", (e, d) => {
      if (d.type === "model") {
        const ent = entryForKey(d.mk); if (ent) openEntry(ent); return;
      }
      if (d.id !== rootId && (stdIsActive(d.id) || stdIsDeprecated(d.id)))
        selectStandard(d.id);
    });
  // Shape encodes the kind : rounded rectangle = standard, circle = model. The
  // circle is kept for models so they look the SAME as in the Model/Dependency
  // graphs ; standards get the distinctive rectangle. The selected (root) node
  // gets a thick blue ring (its fill keeps the real status colour, e.g. red
  // when the selected standard is deprecated).
  const RW = 27, RH = 19;
  node.each(function (d) {
    const g = d3.select(this);
    if (d.type === "model") {
      // Like the Dependency graph : fill = role (teal), outer ring = the
      // model's catalog status. Ring drawn first (behind the fill circle) ;
      // transparent when the model isn't in the catalog (status unknown).
      const ent = entryForKey(d.mk);
      g.append("circle").attr("class", "std-mring").attr("r", 14)
        .style("fill", "none").style("stroke-width", "3px")
        .style("stroke", ent && ent.status
          ? statusColor(ent.status) : "transparent");
      g.append("circle").attr("class", "shape").attr("r", 11);
    } else
      g.append("rect").attr("class", "shape")
        .attr("x", -RW / 2).attr("y", -RH / 2)
        .attr("width", RW).attr("height", RH).attr("rx", 4);
  });
  node.select(".shape").attr("fill", stdNodeColor)
    .style("stroke", d => d.side === "root" ? STD_COLORS.root : "var(--surface)")
    .style("stroke-width", d => d.side === "root" ? "3px" : "2px");
  node.append("text").attr("x", d => d.type === "model" ? 14 : 17)
    .attr("y", 4).text(d => d.label);

  if (stdSim) stdSim.on("tick", ticked);
  ticked();
}

function renderKpis() {
  // KPIs honor the source filter (and the "used in a standard" toggle) so the
  // counts always match the visible catalog.
  const scoped = allModels.filter(m =>
    catSrcFilter.has(m.source) && (!catStdOnly || inAnyStandard(m)));
  const counts = d3.rollup(scoped, v => v.length, d => d.status);
  // Per-source breakdown (shown only when more than one source is checked,
  // otherwise the breakdown is just the total and the line is redundant).
  const showBreakdown = catSrcFilter.size > 1;
  const perSrc = {};
  if (showBreakdown) {
    SOURCE_ORDER.forEach(s => {
      perSrc[s] = d3.rollup(
        scoped.filter(m => m.source === s), v => v.length, d => d.status,
      );
    });
  }
  const order = ["release", "draft", "deprecated", "undefined"];
  const keys = order.filter(k => counts.has(k))
    .concat([...counts.keys()].filter(k => !order.includes(k)));
  const box = d3.select("#kpis");
  box.selectAll("*").remove();
  keys.forEach(k => {
    let html = `<div class="n">${counts.get(k)}</div><div class="s">${k}</div>`;
    if (showBreakdown) {
      const parts = SOURCE_ORDER.map(s => {
        const n = (perSrc[s].get(k) || 0);
        return `<span class="kbd-src"><span class="d" style="background:` +
               `${sourceColor(s)}"></span>${sourceLabel(s)} ${n}</span>`;
      }).join("");
      html += `<div class="kbd">${parts}</div>`;
    } else {
      // Keep the breakdown line's height reserved (empty) so single-source
      // cards stay as tall as two-source ones — no vertical jump below.
      html += `<div class="kbd"></div>`;
    }
    box.append("div")
      .attr("class", "kpi" + (statusFilter === k ? " sel" : ""))
      .style("border-left-color", statusColor(k))
      .html(html)
      .on("click", () => {
        statusFilter = statusFilter === k ? null : k;
        renderKpis(); renderCatalog();
      });
  });
}

// Build a row of multi-select filter pills (.fpill) into `box`, one per value,
// each reflecting/toggling membership in `set`. Factors the six identical
// "build a pill filter" blocks (Home source, Issues status/source, Search
// status/kind/source). Options:
//   color(v)   -> category color (inline --c on the pill)
//   label(v)   -> visible text (defaults to the value itself)
//   onToggle(v, checked) -> optional side effect run after the set is updated,
//                           before re-render (e.g. Search's IDTA→undefined link)
//   onChange() -> re-render callback run after each toggle
function buildPillFilter(box, values, set, opts) {
  const label = opts.label || (v => v);
  values.forEach(v => {
    const lab = document.createElement("label");
    lab.className = "fpill";
    lab.style.setProperty("--c", opts.color(v));
    lab.innerHTML =
      `<input type="checkbox" ${set.has(v) ? "checked" : ""}>` +
      `<span class="dot"></span><span>${label(v)}</span>`;
    lab.querySelector("input").addEventListener("change", function () {
      if (this.checked) set.add(v); else set.delete(v);
      if (opts.onToggle) opts.onToggle(v, this.checked);
      opts.onChange();
    });
    box.appendChild(lab);
  });
}

// Build a .kpi.iss summary card, shared by the Issues and Standards tabs.
// Severity drives the err/warn tint; a zero count is greyed (.zero); `selected`
// adds .sel. opts: {count, label, sev, description, selected, onClick}.
function issueKpiCard(opts) {
  const card = document.createElement("div");
  card.className = "kpi iss " +
    (opts.count === 0 ? "zero" : opts.sev === "warning" ? "warn" : "err") +
    (opts.selected ? " sel" : "");
  if (opts.description) card.title = opts.description;
  card.innerHTML =
    `<div class="n">${opts.count}</div><div class="s">${escapeHtml(opts.label)}</div>`;
  card.addEventListener("click", opts.onClick);
  return card;
}

// Build the collapsible "About these checks" help panel, shared by the Issues
// and Standards tabs. Fills `<selector> .body` once (preserving the user's
// open/closed state across re-renders) with one .item row per type
// (severity tint + label + description). Callers guard on their data being
// loaded before calling.
function buildHelpPanel(selector, types) {
  const body = document.querySelector(selector + " .body");
  if (!body || body.dataset.built) return;
  body.innerHTML = types.map(t =>
    `<div class="item ${t.severity === "warning" ? "warn" : "err"}">` +
    `<div class="lbl">${escapeHtml(t.label)}</div>` +
    `<div class="dsc">${escapeHtml(t.description || "")}</div></div>`
  ).join("");
  body.dataset.built = "1";
}

// Source filter (Home) — built once, toggles the catalog scope.
function renderSourceFilter() {
  const box = document.getElementById("cat-source");
  if (box.dataset.built) return;
  buildPillFilter(box, SOURCE_ORDER, catSrcFilter, {
    color: sourceColor, label: sourceLabel,
    onChange: () => { renderKpis(); renderCatalog(); },
  });
  // "Used in a standard" toggle — far right of the same line (margin-left:auto).
  const stdLab = document.createElement("label");
  stdLab.className = "switch std-only";
  stdLab.title = "Keep only models referenced by a Catena-X standard";
  stdLab.innerHTML =
    `<input type="checkbox" ${catStdOnly ? "checked" : ""}>` +
    `<span class="track"></span><span class="lbl">Used in a standard</span>`;
  stdLab.querySelector("input").addEventListener("change", function () {
    catStdOnly = this.checked;
    renderKpis(); renderCatalog();
  });
  box.appendChild(stdLab);
  box.dataset.built = "1";
}

function renderCatalog() {
  const q = (document.getElementById("search").value || "").toLowerCase();

  const list = [...groups.entries()]
    .map(([name, entries]) => ({ name, entries }))
    .sort((a, b) => a.name.localeCompare(b.name));

  // Source filter applies BEFORE everything else — a model is dropped from the
  // catalog if none of its versions come from a selected source. (Names don't
  // collide across sources, so in practice every model is single-source.)
  // Then status KPI filters the MODELS shown (those having >=1 version of
  // that status) but never removes versions from a model's dropdown.
  const visible = list
    .filter(g => g.entries.some(e => catSrcFilter.has(e.source)))
    .filter(g => !catStdOnly || g.entries.some(e => inAnyStandard(e)))
    .filter(g => !statusFilter || g.entries.some(e => e.status === statusFilter))
    .filter(g =>
      !q ||
      g.name.toLowerCase().includes(q) ||
      g.entries.some(e =>
        e.family.toLowerCase().includes(q) ||
        e.status.toLowerCase().includes(q) ||
        e.version.toLowerCase().includes(q) ||
        e.source.toLowerCase().includes(q) ||
        (e.name || "").toLowerCase().includes(q)));

  // The "used in a standard" toggle also prunes individual versions, so the
  // expanded dropdown only lists the versions a standard actually references
  // (unlike the status KPI, which keeps every version of a matching model).
  const shownEntries = g =>
    catStdOnly ? g.entries.filter(inAnyStandard) : g.entries;

  d3.select("#count").text(
    `${visible.length} models · ` +
    `${visible.reduce((n, g) => n + shownEntries(g).length, 0)} versions`);

  const cat = d3.select("#catalog");
  cat.selectAll("*").remove();
  visible.forEach(g => {
    const entries = shownEntries(g);
    const nRel = entries.filter(e => e.status === "release").length;
    const open = catOpen.has(g.name);
    const row = cat.append("div").attr("class", "crow" + (open ? " open" : ""));

    // Header: click anywhere to expand/collapse this model's versions.
    const head = row.append("div").attr("class", "ch");
    head.append("span").attr("class", "ct").text("▶");
    head.append("span").attr("class", "cn").text(g.name);
    // Source badge — model_name is single-source in practice, so we take it
    // from the first entry. Helps the user spot Catena-X vs IDTA at a glance.
    const src = entries[0].source;
    head.append("span").attr("class", "badge")
      .style("background", sourceColor(src))
      .text(sourceLabel(src));
    head.append("span").attr("class", "cs")
      .html(`${entries.length} version${entries.length > 1 ? "s" : ""}` +
            (nRel ? ` · <b>${nRel} release</b>` : ""));
    head.on("click", () => {
      if (catOpen.has(g.name)) catOpen.delete(g.name);
      else catOpen.add(g.name);
      renderCatalog();
    });

    // One clickable line per version (newest first) -> open in Model Viewer.
    // The .cvs-inner wrapper is required for the grid-template-rows accordion
    // animation (cf. CSS) — `.cvs` is the animated grid container, .cvs-inner
    // is the single grid item that clips its overflow during the transition.
    const vs = row.append("div").attr("class", "cvs")
                  .append("div").attr("class", "cvs-inner");
    entries.forEach(e => {
      const v = vs.append("div").attr("class", "cv").on("click", () => openEntry(e));
      v.append("span").attr("class", "vv").text("v" + e.version);
      v.append("span").attr("class", "vf").text(e.family);
      const st = v.append("span").attr("class", "vs");
      st.append("span").attr("class", "vd").style("background", statusColor(e.status));
      st.append("span").text(e.status);
      // Aspect count — only when > 1 (the common case is 1 Aspect or 0).
      // Useful for material_accounting@1.0.0 = 6 Aspects in one graph.
      if (e.n_aspects > 1)
        v.append("span").attr("class", "vc")
          .text(`${e.n_aspects} Aspects`);
      // Linked Catena-X standards for this exact version. Cap to a few chips
      // and collapse the rest into a hover-titled "+N" so a heavily-referenced
      // model doesn't blow up the row. Clicking a chip opens the Standards tab.
      const stds = (standardsData && standardsData.models
        && standardsData.models[`${e.model_name}@${e.version}`]) || [];
      if (stds.length) {
        const CAP = 3;
        const wrap = v.append("span").attr("class", "vstd");
        stds.slice(0, CAP).forEach(cx => {
          wrap.append("span").attr("class", "sc")
            .attr("title", `Used in ${cx} — open standard`)
            .text(cx)
            .on("click", ev => {
              ev.stopPropagation();          // don't also open the model
              selectStandard(cx); showTab("standardsview");
            });
        });
        if (stds.length > CAP)
          wrap.append("span").attr("class", "more")
            .attr("title", stds.join(", "))
            .text(`+${stds.length - CAP}`);
      }
      v.append("span").attr("class", "va").text("→");
    });
  });
}

d3.select("#search").on("input", renderCatalog);

// ---- Issues (top tab) -----------------------------------------------------
let issuesData = null;                 // issues.json { issue_types, models }
let issStatuses = new Set(["release"]);// status scope (release on by default)
let issSources = new Set(SOURCE_ORDER); // source scope (both on by default)
let issSelType = null;                 // selected issue type (drives the list)

fetch("data/graph/issues.json")
  .then(r => r.json())
  .then(j => {
    issuesData = j;
    renderOverview();             // upgrade "Quality" card from "—" to real count
    applyHash();                  // resolve #/issues?type=<id> now that data is ready
    if (activeView === "issues") renderIssues();
    else if (activeSub === "sv-issues") renderSub();
  })
  .catch(() => { issuesData = null; });

// Issue types grouped into a handful of domains so the 28 KPI cards read as a
// few prioritised sections instead of a flat wall. Any type not listed here
// (e.g. a new check added upstream) falls into an "Other" group, so nothing is
// ever silently hidden.
const ISSUE_CATEGORIES = [
  { title: "Structure & files", ids: [
    "aspect_without_properties", "property_without_characteristic",
    "characteristic_without_datatype", "trait_without_constraint",
    "unused_property", "orphan_isolated", "orphan_unreachable",
    "missing_files"] },
  { title: "Dependencies", ids: [
    "deprecated_dep", "circular_dep", "unresolved_dep", "outdated_dependency",
    "older_release_exists", "dep_on_draft", "dep_on_undefined"] },
  { title: "Naming & versioning", ids: [
    "bad_naming_property", "bad_naming_type", "namespace_mismatch",
    "stem_name_mismatch", "non_semver_version", "meta_model_drift"] },
  { title: "Documentation", ids: [
    "key_element_missing_description", "key_element_missing_preferred_name",
    "empty_description", "description_not_english", "missing_release_notes",
    "missing_release_date", "missing_gen_docs"] },
];

function renderIssues() {
  if (activeView !== "issues") return;
  const sbox = document.getElementById("iss-status");
  const srcbox = document.getElementById("iss-source");
  const kbox = document.getElementById("iss-kpis");
  const expl = document.getElementById("iss-explain");
  const help = document.getElementById("iss-help");
  const head = document.getElementById("iss-list-head");
  const list = document.getElementById("iss-list");

  if (!issuesData) {
    kbox.innerHTML = ""; head.textContent = "";
    expl.style.display = "none";
    help.querySelector(".body").innerHTML = "";
    list.innerHTML = `<div class="empty">issues.json not found — ` +
      `run: python -m sldt_analyzer.graph</div>`;
    return;
  }

  // Status checkboxes — built once, drive the KPI scope.
  if (!sbox.dataset.built) {
    buildPillFilter(sbox, STATUS_ORDER, issStatuses, {
      color: statusColor, onChange: renderIssues,
    });
    sbox.dataset.built = "1";
  }

  // Source checkboxes — built once (same pattern as status).
  if (!srcbox.dataset.built) {
    buildPillFilter(srcbox, SOURCE_ORDER, issSources, {
      color: sourceColor, label: sourceLabel, onChange: renderIssues,
    });
    srcbox.dataset.built = "1";
  }

  const types = issuesData.issue_types;
  const M = issuesData.models;
  const labelOf = {};
  types.forEach(t => (labelOf[t.id] = t.label));
  const inScope = k =>
    issStatuses.has(M[k].status) && issSources.has(M[k].source);
  const keysFor = id => Object.keys(M).filter(k => inScope(k) && M[k].issues[id]);

  // Pre-count every type once (in the current status/source scope).
  const countOf = {};
  types.forEach(t => (countOf[t.id] = keysFor(t.id).length));

  // Resolve the category groups, routing any uncategorised type into "Other".
  const byId = new Map(types.map(t => [t.id, t]));
  const seen = new Set();
  const cats = ISSUE_CATEGORIES.map(c => ({
    title: c.title, types: c.ids.map(id => byId.get(id)).filter(Boolean) }));
  cats.forEach(c => c.types.forEach(t => seen.add(t.id)));
  const leftover = types.filter(t => !seen.has(t.id));
  if (leftover.length) cats.push({ title: "Other", types: leftover });

  // One card per type, highlighted as soon as count > 0. `title` = native
  // tooltip with the full description.
  const makeCard = t =>
    issueKpiCard({
      count: countOf[t.id], label: t.label, sev: t.severity,
      description: t.description, selected: issSelType === t.id,
      onClick: () => {
        issSelType = issSelType === t.id ? null : t.id;
        // Push URL : #/issues?type=<id> (or bare #/issues if no selection).
        const p = issSelType ? new URLSearchParams({ type: issSelType }) : null;
        setUrl(buildHash(URL_BY_VIEW.issues, "", p));
        renderIssues();
      },
    });

  // KPI grid grouped by category ; within each group, sort by count desc
  // (most pressing first), errors before warnings on ties.
  kbox.innerHTML = "";
  cats.forEach(c => {
    if (!c.types.length) return;
    const total = c.types.reduce((s, t) => s + countOf[t.id], 0);
    const sorted = c.types.slice().sort((a, b) =>
      countOf[b.id] - countOf[a.id] ||
      (a.severity === "error" ? 0 : 1) - (b.severity === "error" ? 0 : 1) ||
      a.label.localeCompare(b.label));
    const sec = document.createElement("div");
    sec.className = "iss-cat";
    sec.innerHTML =
      `<div class="iss-cat-h">${escapeHtml(c.title)} ` +
      `<span class="ct">${total.toLocaleString()} issue${total === 1 ? "" : "s"}</span>` +
      `</div><div class="iss-cat-grid"></div>`;
    const grid = sec.querySelector(".iss-cat-grid");
    sorted.forEach(t => grid.appendChild(makeCard(t)));
    kbox.appendChild(sec);
  });

  // "About these checks" reference panel — built once (shared helper).
  buildHelpPanel("#iss-help", types);

  if (!issSelType) {
    expl.style.display = "none";
    head.textContent = "";
    list.innerHTML = `<div class="empty">Select an issue above to list ` +
      `the affected model+version.</div>`;
    return;
  }

  // Selected explanation banner — shows the full description above the list.
  const t = types.find(x => x.id === issSelType);
  if (t) {
    expl.className = t.severity === "warning" ? "warn" : "err";
    expl.style.display = "";
    expl.innerHTML =
      `<div class="head"><span class="sev"></span>${escapeHtml(t.label)}</div>` +
      `<div class="dsc">${escapeHtml(t.description || "")}</div>`;
  } else {
    expl.style.display = "none";
  }

  const rows = keysFor(issSelType)
    .map(k => M[k])
    .sort((a, b) =>
      a.name.localeCompare(b.name) || cmpVerDesc(a.version, b.version));
  head.textContent =
    `${rows.length} model+version · ${labelOf[issSelType]}`;
  list.innerHTML = "";
  if (!rows.length) {
    list.innerHTML =
      `<div class="empty">None with the selected status.</div>`;
    return;
  }
  rows.forEach(m => {
    const ent = entryForKey(`${m.name}@${m.version}`);
    const row = document.createElement("div");
    row.className = "row" + (ent ? " clk" : "");
    row.innerHTML =
      `<span class="nm">${escapeHtml(m.name)} ` +
      `<small>v${escapeHtml(m.version || "—")}</small></span>` +
      `<span class="badge" style="background:${sourceColor(m.source)}">` +
      `${sourceLabel(m.source)}</span>` +
      `<span class="badge" style="background:${statusColor(m.status)}">` +
      `${m.status}</span>` +
      `<span class="det">` +
      `${escapeHtml(issueSummary(issSelType, m.issues[issSelType]))}</span>`;
    if (ent)
      row.addEventListener("click", () => { openEntry(ent); showSub("sv-issues"); });
    list.appendChild(row);
  });
}

// ---- Search (cross-model element search, Feature 6C) ---------------------
// search.json is ~3.6 MB -> lazy-loaded on the first visit of the Search tab.
let searchData = null, srFetchStarted = false;
let srStatuses = new Set(["release", "undefined"]);   // model-status scope (release + undefined default)
let srSources = new Set(SOURCE_ORDER);   // source scope (both on by default)
const SR_KINDS = ["Aspect", "Property", "Entity"];
let srKinds = new Set(SR_KINDS);         // kind buckets (all on by default)
// Element kind -> one of the 3 user-facing buckets (Abstract* fold into
// Property/Entity so narrowing by type never silently drops results).
const kindBucket = k =>
  k === "Aspect" ? "Aspect" : k.endsWith("Property") ? "Property" : "Entity";
const SR_CAP = 300;                      // max rows rendered (perf)

function loadSearch() {
  if (srFetchStarted) return;
  srFetchStarted = true;
  fetch("data/graph/search.json")
    .then(r => r.json())
    .then(j => { searchData = j; if (activeView === "searchview") renderSearch(); })
    .catch(() => {
      srFetchStarted = false;
      if (activeView === "searchview") renderSearch();
    });
}

// Candidate example searches for the empty state. Filtered at render time to
// those that actually return ≥1 hit in the loaded index, so a clicked chip is
// never a dead end (the corpus can change as upstream models evolve).
const SR_EXAMPLES = ["battery", "address", "material", "identifier",
  "temperature", "certificate", "quantity", "serialNumber"];

// Rich empty state shown before any query : per-kind index breakdown + a row
// of clickable example searches. Returns the inner HTML for #sr-list.
function searchEmptyHint() {
  const counts = { Aspect: 0, Property: 0, Entity: 0 };
  searchData.forEach(e => { counts[kindBucket(e.kind)]++; });
  const lc = q => q.toLowerCase();
  const stat = (kind, label) =>
    `<span class="st"><span class="d" style="background:${color(kind)}"></span>` +
    `<b>${counts[kind].toLocaleString()}</b> ${label}</span>`;
  const examples = SR_EXAMPLES
    .filter(c => searchData.some(e => e.name.toLowerCase().includes(lc(c)) ||
      (e.preferredName && e.preferredName.toLowerCase().includes(lc(c)))))
    .slice(0, 6)
    .map(c => `<button type="button" class="sr-ex" data-q="${c}">${c}</button>`)
    .join("");
  return `<div class="sr-hint">` +
    `<div class="stats">${stat("Aspect", "aspects")}` +
      `${stat("Property", "properties")}${stat("Entity", "entities")}</div>` +
    (examples ? `<div class="ex"><span class="ttl">Try:</span>${examples}</div>` : "") +
    `</div>`;
}

function renderSearch() {
  if (activeView !== "searchview") return;
  const sbox = document.getElementById("sr-status");
  const kbox = document.getElementById("sr-kinds");
  const srcbox = document.getElementById("sr-source");
  const list = document.getElementById("sr-list");
  const cnt = document.getElementById("sr-count");

  // Status checkboxes — built once (same pattern as the Issues tab).
  if (!sbox.dataset.built) {
    buildPillFilter(sbox, STATUS_ORDER, srStatuses, {
      color: statusColor, onChange: renderSearch,
    });
    sbox.dataset.built = "1";
  }

  // Kind checkboxes — built once (Aspect / Property / Entity buckets).
  if (!kbox.dataset.built) {
    buildPillFilter(kbox, SR_KINDS, srKinds, {
      color: color, onChange: renderSearch,
    });
    kbox.dataset.built = "1";
  }

  // Source checkboxes — built once (catenax / idta).
  if (!srcbox.dataset.built) {
    buildPillFilter(srcbox, SOURCE_ORDER, srSources, {
      color: sourceColor, label: sourceLabel, onChange: renderSearch,
      onToggle: (v, checked) => {
        // Checking IDTA auto-checks "undefined" status (IDTA models have no
        // metadata.json upstream, so they all fall under "undefined").
        if (checked && v === "idta" && !srStatuses.has("undefined")) {
          srStatuses.add("undefined");
          const undefInput = sbox.querySelectorAll("label input")[
            STATUS_ORDER.indexOf("undefined")
          ];
          if (undefInput) undefInput.checked = true;
        }
      },
    });
    srcbox.dataset.built = "1";
  }

  if (!searchData) {
    loadSearch();
    cnt.textContent = "";
    list.innerHTML = `<div class="empty">` +
      (srFetchStarted ? "Loading search index…"
        : "search.json not found — run: python -m sldt_analyzer.graph") +
      `</div>`;
    return;
  }

  const q = (document.getElementById("sr-q").value || "").trim().toLowerCase();
  if (q.length < 2) {
    cnt.textContent = `${searchData.length.toLocaleString()} searchable elements`;
    list.innerHTML = searchEmptyHint();
    // Example chips prefill the query and run the search via the input handler.
    list.querySelectorAll(".sr-ex").forEach(b =>
      b.addEventListener("click", () => {
        const inp = document.getElementById("sr-q");
        inp.value = b.dataset.q;
        inp.dispatchEvent(new Event("input"));
        inp.focus();
      }));
    return;
  }

  const hits = searchData.filter(e =>
    srStatuses.has(e.status) &&
    srSources.has(e.source) &&
    srKinds.has(kindBucket(e.kind)) &&
    (e.name.toLowerCase().includes(q) ||
     (e.preferredName && e.preferredName.toLowerCase().includes(q))));

  // Collapse the same element across versions of the same model (same name +
  // kind + model) into one row — otherwise a property living in 6 versions
  // floods the list with 6 near-identical rows. Representative = newest version
  // ; the row notes "(N versions)".
  const srGroupsMap = new Map();
  hits.forEach(e => {
    const key = e.model_name + " " + e.kind + " " + e.name.toLowerCase();
    const g = srGroupsMap.get(key);
    if (!g) { srGroupsMap.set(key, { rep: e, n: 1 }); return; }
    g.n++;
    if (cmpVerDesc(e.version, g.rep.version) < 0) g.rep = e;   // keep the newest
  });
  const srGroups = [...srGroupsMap.values()];

  srGroups.sort((A, B) => {
    const a = A.rep, b = B.rep;
    const ap = a.name.toLowerCase().startsWith(q) ? 0 : 1;
    const bp = b.name.toLowerCase().startsWith(q) ? 0 : 1;
    return ap - bp || a.name.localeCompare(b.name) ||
           a.model_name.localeCompare(b.model_name);
  });

  cnt.textContent = srGroups.length > SR_CAP
    ? `showing ${SR_CAP} of ${srGroups.length} matches`
    : `${srGroups.length} match${srGroups.length === 1 ? "" : "es"}`;

  if (!srGroups.length) {
    list.innerHTML = `<div class="empty">No element matches ` +
      `“${escapeHtml(q)}” in the selected type / status scope.</div>`;
    return;
  }

  list.innerHTML = "";
  srGroups.slice(0, SR_CAP).forEach(g => {
    const e = g.rep;
    const row = document.createElement("div");
    row.className = "sr-row";
    const pn = e.preferredName &&
      e.preferredName.toLowerCase() !== e.name.toLowerCase()
        ? `<span class="pn">${escapeHtml(e.preferredName)}</span>` : "";
    row.innerHTML =
      `<div class="top">` +
        `<span class="kbadge"><span class="d" style="background:` +
        `${color(e.kind)}"></span>${e.kind}</span>` +
        `<span class="nm">${escapeHtml(e.name)}</span>` + pn +
        `<span class="loc">${escapeHtml(e.model_name)} · v` +
        `${escapeHtml(e.version)}` +
        (g.n > 1 ? ` <span class="vmore">(${g.n} versions)</span>` : "") + ` ` +
        `<span class="badge" style="background:${sourceColor(e.source)}">` +
        `${sourceLabel(e.source)}</span> ` +
        `<span class="badge" style="background:${statusColor(e.status)}">` +
        `${e.status}</span></span>` +
      `</div>` +
      (e.description
        ? `<div class="desc">${escapeHtml(e.description)}</div>` : "");
    const ent = allModels.find(m => m.id === e.id) ||
                entryForKey(`${e.model_name}@${e.version}`);
    if (ent)
      row.addEventListener("click", () => { openEntry(ent); showSub("sv-graph"); });
    list.appendChild(row);
  });
}

const srInput = document.getElementById("sr-q");
if (srInput) srInput.addEventListener("input", () => {
  // Live typing = replaceState (don't pollute history). Bare URL when empty.
  const q = srInput.value || "";
  const p = q ? new URLSearchParams({ q }) : null;
  setUrl(buildHash(URL_BY_VIEW.searchview, "", p), { replace: true });
  renderSearch();
});
