"""Bodhi 语义图：服务自带的独立 HTML 页（零前端改动即可查看）。

用法（服务已常驻 WSL）：
    http://localhost:8765/graph?kb_id=<知识库UUID>&model=bmm
页面能力：
  - 节点 = 本体要素页（按本体类着色，颜色取自 ontology_index）
  - 边 = 页面「## 本体关系」里的关系（带关系 key + 中文标签 + 方向箭头）
  - 过滤：模型 / 本体类（多选）/ 关系类型（多选）/ 名称搜索 / 节点上限
  - 交互：拖拽节点、滚轮缩放、空白拖拽平移、悬停高亮邻接边、点击看页面全文
  - 无任何外部依赖（自绘 SVG + 自写力导向），不依赖 CDN，离线可用
"""

from __future__ import annotations

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bodhi 本体图谱</title>
<style>
  :root { --bg:#0f172a; --panel:#111827; --line:#1f2937; --text:#e5e7eb; --dim:#9ca3af; --accent:#38bdf8; }
  * { box-sizing: border-box; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--text);
    font: 13px/1.5 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
  #app { display:flex; flex-direction:column; height:100%; }
  header { padding:8px 12px; border-bottom:1px solid var(--line); background:var(--panel);
    display:flex; gap:14px; align-items:center; flex-wrap:wrap; }
  header b { color:var(--accent); font-weight:600; }
  .stat { color:var(--dim); }
  .grow { flex:1; }
  main { position:relative; flex:1; overflow:hidden; }
  svg { display:block; width:100%; height:100%; cursor:grab; }
  svg.dragging { cursor:grabbing; }
  .edge { stroke:#475569; stroke-width:1.1; marker-end:url(#arrow); }
  .edge.hot { stroke:var(--accent); stroke-width:2.2; }
  .elabel { fill:#94a3b8; font-size:9px; pointer-events:none; paint-order:stroke;
    stroke:var(--bg); stroke-width:2.5px; }
  .elabel.show { fill:#cbd5e1; }
  .elabel.hot { fill:var(--accent); font-weight:600; }
  .node circle { stroke:#0b1220; stroke-width:1; cursor:pointer; }
  .node.sel circle { stroke:#fff; stroke-width:2.5; }
  .node text { fill:var(--text); font-size:10px; pointer-events:none; paint-order:stroke;
    stroke:var(--bg); stroke-width:2.5px; }
  .node.dim { opacity:.18; }
  .edge.dim { opacity:.08; }
  aside { width:520px; max-width:52vw; border-left:1px solid var(--line); background:var(--panel);
    overflow:auto; padding:12px 14px; }
  aside h2 { margin:2px 0 4px; font-size:15px; }
  aside .meta { color:var(--dim); margin-bottom:8px; }
  aside pre { white-space:pre-wrap; word-break:break-word; background:#0b1220; padding:10px;
    border-radius:6px; border:1px solid var(--line); font-size:12px; }
  .chip { display:inline-block; padding:1px 7px; border-radius:10px; background:#1e293b;
    color:var(--dim); margin:0 6px 6px 0; font-size:11px; }
  .chip.link { cursor:pointer; color:var(--accent); }
  .filters { position:absolute; left:10px; top:10px; width:290px; max-height:calc(100% - 20px);
    overflow:auto; background:rgba(17,24,39,.94); border:1px solid var(--line); border-radius:8px;
    padding:8px 10px; }
  .filters h4 { margin:8px 0 4px; font-size:12px; color:var(--dim); font-weight:600; }
  .filters label { display:flex; align-items:center; gap:6px; padding:1px 0; cursor:pointer; }
  .filters input[type=text], .filters input[type=number], .filters select {
    width:100%; background:#0b1220; color:var(--text); border:1px solid var(--line);
    border-radius:5px; padding:3px 6px; }
  .swatch { width:10px; height:10px; border-radius:2px; flex:0 0 10px; }
  .row { display:flex; gap:6px; align-items:center; margin-top:6px; }
  button { background:#1e293b; color:var(--text); border:1px solid var(--line); border-radius:5px;
    padding:3px 8px; cursor:pointer; }
  button:hover { border-color:var(--accent); color:var(--accent); }
  .hint { position:absolute; right:12px; bottom:10px; color:var(--dim); font-size:11px; }
  .loading { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
    color:var(--dim); background:rgba(15,23,42,.7); }
</style>
</head>
<body>
<div id="app">
  <header>
    <b>Bodhi 本体图谱</b>
    <span class="stat" id="stat">加载中…</span>
    <span class="stat" id="stat2"></span>
    <span class="grow"></span>
    <label>模型 <select id="model" style="background:#0b1220;color:inherit;border:1px solid var(--line);border-radius:5px;padding:2px 6px"></select></label>
    <label>节点上限 <input id="limit" type="number" min="20" max="800" value="150" style="width:70px;background:#0b1220;color:inherit;border:1px solid var(--line);border-radius:5px;padding:2px 6px"></label>
    <label><input type="checkbox" id="showLabels"> 显示关系标签</label>
    <button id="reload">重新加载</button>
    <button id="relayout">重新布局</button>
    <button id="zoomFit">适应窗口</button>
  </header>
  <div style="display:flex; flex:1; min-height:0">
    <main>
      <svg id="svg">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="18" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="#64748b"></path>
          </marker>
        </defs>
        <g id="viewport">
          <g id="edges"></g>
          <g id="elabels"></g>
          <g id="nodes"></g>
        </g>
      </svg>
      <div class="filters">
        <h4>本体类</h4><div id="classes"></div>
        <h4>搜索</h4>
        <input type="text" id="search" placeholder="按名称 / 定义过滤节点">
        <div class="row"><button id="allOn">全选</button><button id="allOff">全不选</button></div>
      </div>
      <div class="hint">拖拽节点移动 · 滚轮缩放 · 空白拖拽平移 · 点击节点看页面</div>
      <div class="loading" id="loading">加载中…</div>
    </main>
    <aside id="panel" style="display:none"></aside>
  </div>
</div>
<script>
const KB = "__KB_ID__", MODEL = "__MODEL__";
</script>
<!-- SCRIPT_PART_2 -->
</body>
</html>
"""

SCRIPT_A = r"""
const svg = document.getElementById('svg');
const viewport = document.getElementById('viewport');
const gEdges = document.getElementById('edges');
const gLabels = document.getElementById('elabels');
const gNodes = document.getElementById('nodes');
const panel = document.getElementById('panel');
const state = { nodes: [], edges: [], classes: new Map(), relTypes: new Map(),
                selClasses: new Set(), q: '', showLabels: false,
                sel: null, hover: null };
const view = { x: -600, y: -450, w: 1200, h: 900 };

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function applyView() { svg.setAttribute('viewBox', view.x + ' ' + view.y + ' ' + view.w + ' ' + view.h); }

async function load() {
  const limit = document.getElementById('limit').value || 150;
  const model = document.getElementById('model').value || '';
  document.getElementById('loading').style.display = 'flex';
  try {
    const r = await fetch('/bodhi/graph?kb_id=' + encodeURIComponent(KB) +
                          '&model=' + encodeURIComponent(model) + '&limit=' + limit);
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    state.nodes = d.nodes; state.edges = d.edges; state.sel = null; state.hover = null;
    state.classes = new Map();
    d.nodes.forEach(function (n) {
      if (!state.classes.has(n.page_type))
        state.classes.set(n.page_type, { color: n.color, label: n.class_label, count: 0 });
      state.classes.get(n.page_type).count++;
    });
    state.relTypes = new Map();
    d.edges.forEach(function (e) { state.relTypes.set(e.type, (state.relTypes.get(e.type) || 0) + 1); });
    state.selClasses = new Set(state.classes.keys());
    renderFilters(); layout(); zoomFit(); render();
    document.getElementById('stat').textContent =
      d.meta.node_count + ' 节点 / ' + d.meta.edge_count + ' 关系 / ' +
      state.classes.size + ' 个本体类 / ' + state.relTypes.size + ' 种关系类型';
  } catch (e) {
    document.getElementById('stat').textContent = '加载失败：' + e.message;
  } finally {
    document.getElementById('loading').style.display = 'none';
  }
}

function renderFilters() {
  var cls = document.getElementById('classes');
  var rows = Array.from(state.classes.entries()).sort(function (a, b) { return b[1].count - a[1].count; });
  cls.innerHTML = rows.map(function (kv) {
    var t = kv[0], v = kv[1];
    return '<label><input type="checkbox" data-cls="' + esc(t) + '"' +
      (state.selClasses.has(t) ? ' checked' : '') + '>' +
      '<span class="swatch" style="background:' + esc(v.color) + '"></span>' + esc(v.label) +
      ' <span style="color:#6b7280">(' + v.count + ')</span></label>';
  }).join('') || '<div style="color:#6b7280">（无）</div>';
}
"""

SCRIPT_B = r"""
function layout() {
  var nodes = state.nodes;
  if (!nodes.length) return;
  var groups = new Map();
  nodes.forEach(function (n) {
    if (!groups.has(n.page_type)) groups.set(n.page_type, []);
    groups.get(n.page_type).push(n);
  });
  var R = 260 + nodes.length * 1.7, gi = 0, gsize = groups.size || 1;
  groups.forEach(function (list) {
    var a0 = (gi / gsize) * Math.PI * 2; gi++;
    list.forEach(function (n, i) {
      var frac = i / Math.max(1, list.length);
      var a = a0 + (frac - 0.5) * 0.9;
      var r = R * (0.55 + 0.45 * frac);
      n.x = Math.cos(a) * r + (Math.random() - 0.5) * 24;
      n.y = Math.sin(a) * r + (Math.random() - 0.5) * 24;
      n.vx = 0; n.vy = 0;
    });
  });
  var idx = new Map(nodes.map(function (n) { return [n.slug, n]; }));
  var links = state.edges.map(function (e) { return { s: idx.get(e.source), t: idx.get(e.target) }; })
                         .filter(function (l) { return l.s && l.t; });
  var REP = 30000, ITER = nodes.length > 200 ? 50 : 110;
  for (var it = 0; it < ITER; it++) {
    for (var i = 0; i < nodes.length; i++) {
      var a = nodes[i];
      for (var j = i + 1; j < nodes.length; j++) {
        var b = nodes[j];
        var dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy || 0.01;
        if (d2 > 260000) continue;
        var f = REP / d2, d = Math.sqrt(d2), fx = f * dx / d, fy = f * dy / d;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      }
    }
    links.forEach(function (l) {
      var dx = l.t.x - l.s.x, dy = l.t.y - l.s.y, d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      var f = (d - 95) * 0.02;
      l.s.vx += f * dx / d; l.s.vy += f * dy / d;
      l.t.vx -= f * dx / d; l.t.vy -= f * dy / d;
    });
    nodes.forEach(function (n) {
      n.vx -= n.x * 0.002; n.vy -= n.y * 0.002;
      n.x += Math.max(-8, Math.min(8, n.vx)) * 0.5;
      n.y += Math.max(-8, Math.min(8, n.vy)) * 0.5;
      n.vx *= 0.7; n.vy *= 0.7;
    });
  }
}

function visible() {
  var q = state.q.trim().toLowerCase();
  var nodes = state.nodes.filter(function (n) {
    return state.selClasses.has(n.page_type) &&
      (!q || n.title.toLowerCase().indexOf(q) >= 0 ||
       (n.summary || '').toLowerCase().indexOf(q) >= 0);
  });
  var keep = new Set(nodes.map(function (n) { return n.slug; }));
  var edges = state.edges.filter(function (e) {
    return keep.has(e.source) && keep.has(e.target);
  });
  return { nodes: nodes, edges: edges };
}

function zoomFit() {
  var v = visible();
  var nodes = v.nodes.length ? v.nodes : state.nodes;
  if (!nodes.length) return;
  var x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  nodes.forEach(function (n) {
    x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y);
    x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y);
  });
  var pad = 90;
  view.x = x0 - pad; view.y = y0 - pad;
  view.w = Math.max(300, (x1 - x0) + pad * 2); view.h = Math.max(220, (y1 - y0) + pad * 2);
  applyView();
}

function render() {
  var v = visible();
  var pos = new Map();
  v.nodes.forEach(function (n) { pos.set(n.slug, n); });
  var deg = new Map();
  v.edges.forEach(function (e) {
    deg.set(e.source, (deg.get(e.source) || 0) + 1);
    deg.set(e.target, (deg.get(e.target) || 0) + 1);
  });
  var relById = new Map();
  var labelsOn = state.showLabels || v.nodes.length <= 60;

  gEdges.innerHTML = v.edges.map(function (e) {
    var s = pos.get(e.source), t = pos.get(e.target);
    if (!s || !t) return '';
    return '<line class="edge" data-src="' + esc(e.source) + '" data-dst="' + esc(e.target) +
      '" data-rel="' + esc(e.type) + '" x1="' + s.x + '" y1="' + s.y +
      '" x2="' + t.x + '" y2="' + t.y + '"></line>';
  }).join('');

  gLabels.innerHTML = labelsOn ? v.edges.map(function (e) {
    var s = pos.get(e.source), t = pos.get(e.target);
    if (!s || !t) return '';
    var mx = (s.x + t.x) / 2, my = (s.y + t.y) / 2;
    return '<text class="elabel show" data-src="' + esc(e.source) + '" data-dst="' + esc(e.target) +
      '" data-rel="' + esc(e.type) + '" x="' + mx + '" y="' + my + '" text-anchor="middle">' +
      esc(e.label || e.type) + '</text>';
  }).join('') : '';

  var showText = v.nodes.length <= 90;
  gNodes.innerHTML = v.nodes.map(function (n) {
    var r = 6 + Math.min(13, (deg.get(n.slug) || 0) * 1.1);
    var txt = showText ? '<text x="' + (n.x + r + 3) + '" y="' + (n.y + 3) + '">' +
      esc(n.title.length > 14 ? n.title.slice(0, 13) + '…' : n.title) + '</text>' : '';
    return '<g class="node" data-slug="' + esc(n.slug) + '">' +
      '<circle cx="' + n.x + '" cy="' + n.y + '" r="' + r + '" fill="' + esc(n.color) + '"></circle>' +
      txt + '</g>';
  }).join('');

  if (state.sel) highlight(state.sel);
  document.getElementById('stat2').textContent =
    '当前显示 ' + v.nodes.length + ' / ' + state.nodes.length + ' 节点，' +
    v.edges.length + ' / ' + state.edges.length + ' 关系';
}
"""

SCRIPT_C1 = r"""
function highlight(slug) {
  state.hover = slug;
  var keep = null;
  if (slug) {
    keep = new Set([slug]);
    state.edges.forEach(function (e) {
      if (e.source === slug || e.target === slug) { keep.add(e.source); keep.add(e.target); }
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll('.node'), function (el) {
    el.classList.toggle('dim', !!slug && !keep.has(el.dataset.slug));
  });
  Array.prototype.forEach.call(document.querySelectorAll('.edge'), function (el) {
    var hot = !!slug && (el.dataset.src === slug || el.dataset.dst === slug);
    el.classList.toggle('hot', hot);
    el.classList.toggle('dim', !!slug && !hot);
  });
  Array.prototype.forEach.call(document.querySelectorAll('.elabel'), function (el) {
    el.classList.toggle('hot', !!slug && (el.dataset.src === slug || el.dataset.dst === slug));
  });
}

function moveNode(n) {
  Array.prototype.forEach.call(gNodes.children, function (g) {
    if (g.dataset.slug !== n.slug) return;
    var c = g.querySelector('circle');
    if (c) { c.setAttribute('cx', n.x); c.setAttribute('cy', n.y); }
    var t = g.querySelector('text');
    if (t && c) { t.setAttribute('x', n.x + (+c.getAttribute('r')) + 3); t.setAttribute('y', n.y + 3); }
  });
  var pos = new Map();
  state.nodes.forEach(function (x) { pos.set(x.slug, x); });
  Array.prototype.forEach.call(gEdges.children, function (l) {
    if (l.dataset.src !== n.slug && l.dataset.dst !== n.slug) return;
    var s = pos.get(l.dataset.src), t = pos.get(l.dataset.dst);
    if (!s || !t) return;
    l.setAttribute('x1', s.x); l.setAttribute('y1', s.y);
    l.setAttribute('x2', t.x); l.setAttribute('y2', t.y);
    // 关键：关系标签画在边的中点，节点移动时必须一起搬，否则标签会留在原地（看起来"消失了"）
    var lab = gLabels.querySelector('text[data-src="' + l.dataset.src +
                                    '"][data-dst="' + l.dataset.dst + '"]');
    if (lab) { lab.setAttribute('x', (s.x + t.x) / 2); lab.setAttribute('y', (s.y + t.y) / 2); }
  });
}

async function showPanel(slug) {
  state.sel = slug; panel.style.display = 'block';
  panel.innerHTML = '<div style="color:#9ca3af">加载中…</div>';
  try {
    var r = await fetch('/bodhi/page?kb_id=' + encodeURIComponent(KB) + '&slug=' + encodeURIComponent(slug));
    var d = await r.json();
    if (d.error) throw new Error(d.error);
    var chips = function (arr) {
      return (arr || []).map(function (s) {
        return '<span class="chip link" data-goto="' + esc(s) + '">' + esc(s) + '</span>';
      }).join('') || '<span style="color:#6b7280">（无）</span>';
    };
    panel.innerHTML =
      '<h2>' + esc(d.title) + '</h2>' +
      '<div class="meta">' + esc(d.page_type) + (d.class_label ? '（' + esc(d.class_label) + '）' : '') +
      ' · v' + d.version + ' · 更新 ' + esc(String(d.updated_at || '').slice(0, 19)) +
      ((d.merge_history && d.merge_history.length) ? ' · 合并 ' + d.merge_history.length + ' 次' : '') + '</div>' +
      '<div style="color:#9ca3af">出链 ' + (d.out_links || []).length + ' ｜ 入链 ' + (d.in_links || []).length + '</div>' +
      '<h4 style="color:#9ca3af;margin:8px 0 2px">出链</h4><div>' + chips(d.out_links) + '</div>' +
      '<h4 style="color:#9ca3af;margin:8px 0 2px">被引用</h4><div>' + chips(d.in_links) + '</div>' +
      '<h4 style="color:#9ca3af;margin:8px 0 2px">页面内容</h4><pre>' + esc(d.content) + '</pre>';
    Array.prototype.forEach.call(panel.querySelectorAll('[data-goto]'), function (el) {
      el.addEventListener('click', function () { gotoSlug(el.dataset.goto); });
    });
    highlight(slug);
  } catch (e) {
    panel.innerHTML = '<div style="color:#f87171">' + esc(e.message) + '</div>';
  }
}

function gotoSlug(slug) {
  var n = null;
  state.nodes.forEach(function (x) { if (x.slug === slug) n = x; });
  if (!n) {
    panel.innerHTML = '<div>该页面不在当前结果集里（可能被类/关系筛选掉，或超出节点上限）</div>';
    return;
  }
  view.x = n.x - view.w / 2; view.y = n.y - view.h / 2; applyView();
  showPanel(slug);
}
"""

SCRIPT_C2 = r"""
var drag = null, pan = null;
svg.addEventListener('mousedown', function (ev) {
  var el = ev.target;
  while (el && el !== svg && !(el.classList && el.classList.contains('node'))) el = el.parentNode;
  if (el && el !== svg && el.dataset && el.dataset.slug) {
    var slug = el.dataset.slug, node = null;
    state.nodes.forEach(function (x) { if (x.slug === slug) node = x; });
    drag = { n: node, moved: false };
  } else {
    pan = { x: ev.clientX, y: ev.clientY, vx: view.x, vy: view.y };
    svg.classList.add('dragging');
  }
  ev.preventDefault();
});
window.addEventListener('mousemove', function (ev) {
  var scale = view.w / Math.max(1, svg.clientWidth);
  if (drag && drag.n) {
    drag.n.x += (ev.movementX || 0) * scale;
    drag.n.y += (ev.movementY || 0) * scale;
    drag.moved = true;
    moveNode(drag.n);
  } else if (pan) {
    view.x = pan.vx - (ev.clientX - pan.x) * scale;
    view.y = pan.vy - (ev.clientY - pan.y) * scale;
    applyView();
  }
});
window.addEventListener('mouseup', function () {
  if (drag) {
    if (!drag.moved) { showPanel(drag.n.slug); }
    else { render(); }        // 拖完整体重绘一次，保证线/标签/节点严格一致
    drag = null;
  }
  pan = null; svg.classList.remove('dragging');
});
svg.addEventListener('wheel', function (ev) {
  ev.preventDefault();
  var rect = svg.getBoundingClientRect();
  var mx = view.x + (ev.clientX - rect.left) / rect.width * view.w;
  var my = view.y + (ev.clientY - rect.top) / rect.height * view.h;
  var k = ev.deltaY > 0 ? 1.12 : 0.89;
  view.x = mx - (mx - view.x) * k; view.y = my - (my - view.y) * k;
  view.w *= k; view.h *= k; applyView();
}, { passive: false });
svg.addEventListener('mouseover', function (ev) {
  var el = ev.target;
  while (el && el !== svg && !(el.classList && el.classList.contains('node'))) el = el.parentNode;
  if (el && el !== svg && el.dataset && el.dataset.slug) highlight(el.dataset.slug);
});
svg.addEventListener('mouseout', function () { highlight(state.sel); });
document.addEventListener('keydown', function (ev) {
  if (ev.key === 'Escape') { panel.style.display = 'none'; state.sel = null; highlight(null); }
});

document.getElementById('classes').addEventListener('change', function (ev) {
  var t = ev.target.dataset.cls;
  if (!t) return;
  if (ev.target.checked) state.selClasses.add(t); else state.selClasses.delete(t);
  render();
});
document.getElementById('search').addEventListener('input', function (ev) {
  state.q = ev.target.value; render();
});
document.getElementById('showLabels').addEventListener('change', function (ev) {
  state.showLabels = ev.target.checked; render();
});
document.getElementById('reload').addEventListener('click', load);
document.getElementById('relayout').addEventListener('click', function () { layout(); zoomFit(); render(); });
document.getElementById('zoomFit').addEventListener('click', zoomFit);
document.getElementById('allOn').addEventListener('click', function () {
  state.selClasses = new Set(state.classes.keys());
  renderFilters(); render();
});
document.getElementById('allOff').addEventListener('click', function () {
  state.selClasses = new Set();
  renderFilters(); render();
});
document.getElementById('model').addEventListener('change', load);

(async function init() {
  await load();
  var prefixes = new Set();
  state.nodes.forEach(function (n) { prefixes.add(n.page_type.split(':')[0]); });
  var sel = document.getElementById('model');
  sel.innerHTML = '<option value="">全部模型</option>' +
    Array.from(prefixes).sort().map(function (p) {
      return '<option value="' + esc(p) + '">' + esc(p) + '</option>';
    }).join('');
  if (MODEL) { sel.value = MODEL; await load(); }
})();
"""


def render_graph_page(kb_id: str, model: str = "") -> str:
    """拼最终 HTML（占位符替换，避免 % 与花括号转义问题）。"""
    script = SCRIPT_A + SCRIPT_B + SCRIPT_C1 + SCRIPT_C2
    return (TEMPLATE
            .replace("__KB_ID__", kb_id or "")
            .replace("__MODEL__", model or "")
            .replace("<!-- SCRIPT_PART_2 -->", "<script>\n" + script + "\n</script>"))
