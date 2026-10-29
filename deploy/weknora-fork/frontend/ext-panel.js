/* bodhi2 外部接口面板（P4）
 *
 * 为什么是"DOM 级脚本"而不是改 Vue
 * ---------------------------------
 * 报告正文是 Markdown，渲染出的是 <pre><code class="language-bodhi-ext">；只要在渲染后把这类块
 * **替换成可编辑表格**即可 —— 不需要重建前端镜像，也不需要碰 Vue 组件树 ✓。
 *
 * 数据来源（全部经 nginx `/bodhi/` 反代到 bodhi-mcp，浏览器不直连端口）：
 *   GET  /bodhi/ext/list?object=…&review_no=…[&columns=a,b][&title=…] → rows + schema + projection
 *        （`review_no` 可省：面板传页面标题 `title`，后端从**报告上下文**解析编号 ✓）
 *   GET  /bodhi/ext/schema?object=…             → 列标签/主键标签（**不需要编号**）
 *   POST /bodhi/ext/update                      → 改一行（actor/session_no/if_rev）
 *   POST /bodhi/ext/describe                    → 重新订阅契约（写投射 / 写契约卡锚点）
 *
 * 两类块（按**内容**识别，渲染器不保证给 language-xxx class）：
 *   ```bodhi-ext           → 台账面板（编号 + rev；可编辑表格；改完自动回写正文）
 *   ```bodhi-ext-contract  → 契约卡（`kind: contract`；只读列/枚举 + 刷新契约）
 *
 * 纪律：**列/枚举只认回执**（面板不写死列）；写库带 `if_rev`（冲突就重取，不覆盖）；
 *      **编号不写死**：块里没有就用 `/bodhi/ext/schema` 的 `key_label` 去页面表格里认，或交后端按标题解析；
 *      报告只用哪几列由**模板**给（引用块里的 `columns:` 行）⇒ 面板按它渲染、按它回写 ✓。
 */
(function () {
  'use strict';
  if (window.__bodhiExtPanel) return;
  window.__bodhiExtPanel = true;

  var CSS = [
    '.bx-panel{border:1px solid #dcdfe6;border-radius:8px;padding:10px 12px;margin:8px 0;background:#fff}',
    '.bx-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:13px;margin-bottom:8px}',
    '.bx-tag{background:#eef3ff;color:#2456c4;border-radius:4px;padding:1px 6px;font-family:monospace}',
    '.bx-btn{border:1px solid #2456c4;background:#2456c4;color:#fff;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:13px}',
    '.bx-btn.ghost{background:#fff;color:#2456c4}',
    '.bx-wrap{overflow:auto;max-height:60vh}',
    '.bx-table{border-collapse:collapse;width:100%;font-size:12.5px}',
    '.bx-table th,.bx-table td{border:1px solid #e5e7eb;padding:4px 6px;vertical-align:top}',
    '.bx-table th{background:#f7f8fa;position:sticky;top:0;white-space:nowrap}',
    '.bx-table td.bx-ro{background:#fbfbfc;color:#555}',
    '.bx-table textarea{width:100%;min-height:28px;border:1px solid #dcdfe6;border-radius:3px;font:inherit;padding:2px 4px;box-sizing:border-box}',
    '.bx-table select{border:1px solid #dcdfe6;border-radius:3px;padding:2px;font:inherit}',
    '.bx-msg{margin-top:8px;font-size:12.5px;white-space:pre-wrap}',
    '.bx-msg.err{color:#c62828}.bx-msg.ok{color:#2e7d32}.bx-msg.warn{color:#ad6800}'
  ].join('');

  function style() {
    if (document.getElementById('bx-style')) return;
    var s = document.createElement('style');
    s.id = 'bx-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function tag(cls, text) {
    var e = document.createElement('span');
    e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function btn(text, cls, onclick) {
    var b = document.createElement('button');
    b.className = 'bx-btn' + (cls ? ' ' + cls : '');
    b.style.cssText = 'display:inline-block;visibility:visible;margin:0 4px 0 0';
    b.textContent = text;
    b.addEventListener('click', onclick);
    return b;
  }

  function api(path, body) {
    var opt = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' },
                       body: JSON.stringify(body) } : {};
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: 'HTTP ' + r.status }; });
    });
  }

  // 当前知识库 id（面板调接口要带 `kb_id`；URL 里两种写法都认）
  function currentKb() {
    var m = /knowledge-bases\/([0-9a-fA-F-]{36})/.exec(window.location.pathname || '');
    if (m) return m[1];
    m = /[?&]kb_id=([0-9a-fA-F-]{36})/.exec(window.location.search || '');
    return m ? m[1] : '';
  }

  function parseBlock(text) {
    var out = {};
    (text || '').split('\n').forEach(function (ln) {
      var m = /^\s*([A-Za-z_]+)\s*:\s*(.+?)\s*$/.exec(ln);
      if (m) out[m[1]] = m[2];
    });
    return out;
  }

  function cellInput(col, value) {
    if (col.enum && col.enum.length) {
      var sel = document.createElement('select');
      var blank = document.createElement('option');
      blank.value = ''; blank.textContent = '—';
      sel.appendChild(blank);
      col.enum.forEach(function (v) {
        var o = document.createElement('option');
        o.value = v; o.textContent = v;
        if (v === value) o.selected = true;
        sel.appendChild(o);
      });
      sel.dataset.field = col.field;
      return sel;
    }
    var ta = document.createElement('textarea');
    ta.rows = 2;
    ta.value = value == null ? '' : String(value);
    ta.dataset.field = col.field;
    return ta;
  }

  var VER = 'v4.5';

  function renderPanel(mount, meta) {
    style();
    var panel = document.createElement('div');
    panel.className = 'bx-panel';
    // 内联兜底：万一宿主的全局 CSS 把 div/button 压扁（实测有主题这么干），面板也不至于"看不见"
    panel.style.cssText = 'display:block;min-height:60px;border:1px solid #dcdfe6;' +
                          'border-radius:8px;padding:10px 12px;margin:8px 0;background:#fff';
    var head = document.createElement('div');
    head.className = 'bx-head';
    var msg = document.createElement('div');
    msg.className = 'bx-msg';
    var state = { rev: null, rows: [], cols: [], labels: {}, schemaHash: '',
                  reviewNo: meta.review_no || '', reviewSrc: meta.review_no ? 'ref_block' : '' };

    // 从**报告上下文**取编号：契约说主键列叫什么（key_label），就在页面表格里找那一行 ✓
    // 注意：编号绝不写死、也不依赖引用块（块可能缺失或漏写）✗
    function domFindValue(label) {
      if (!label) return '';
      var cells = document.querySelectorAll('td,th');
      for (var i = 0; i < cells.length; i++) {
        if ((cells[i].textContent || '').trim() !== label) continue;
        var row = cells[i].closest('tr');
        if (!row) continue;
        var tds = row.querySelectorAll('td,th');
        for (var j = 0; j < tds.length; j++) {
          if (tds[j] === cells[i]) continue;
          var v = (tds[j].textContent || '').trim();
          if (v) return v;
        }
      }
      return '';
    }

    function ensureReviewNo() {
      if (state.reviewNo) return Promise.resolve(state.reviewNo);
      return api('/bodhi/ext/schema?object=' + encodeURIComponent(meta.object))
        .then(function (s) {
          var label = (s && s.key_label) || '';
          var v = domFindValue(label);
          if (v) { state.reviewNo = v; state.reviewSrc = 'report_context'; }
          return state.reviewNo;
        })
        .catch(function () { return state.reviewNo; });
    }

    function note(kind, text) {
      msg.className = 'bx-msg ' + (kind || '');
      msg.textContent = text || '';
    }

    function paint() {
      head.textContent = '';
      head.appendChild(tag('bx-tag', state.reviewNo || '（无编号）'));
      if (state.reviewSrc && state.reviewSrc !== 'ref_block') {
        head.appendChild(tag('bx-msg', '编号取自报告上下文'));
      }
      head.appendChild(tag('bx-tag', '台账 rev ' + (state.rev == null ? '?' : state.rev)));
      head.appendChild(tag('bx-tag', '契约 ' + (state.schemaHash || '未订阅')));
      if (meta.rev != null && state.rev != null && String(meta.rev) !== String(state.rev)) {
        head.appendChild(tag('bx-msg warn', '⚠ 正文快照 rev ' + meta.rev + ' 落后台账'));
      }
      if (state.schemaHash && meta.schema_hash && state.schemaHash !== meta.schema_hash) {
        head.appendChild(tag('bx-msg warn', '⚠ 契约 hash 与正文不一致'));
      }
      head.appendChild(tag('bx-tag', VER));
      head.appendChild(tag('bx-tag', (state.rows || []).length + ' 行 / ' +
                                   (state.cols || []).length + ' 列'));
      head.appendChild(btn('刷新', 'ghost', load));
      head.appendChild(btn('同步正文', 'ghost', syncNow));
      panel.textContent = '';
      panel.appendChild(head);
    }

    function syncNow() {
      note('', '正在把台账同步到正文…');
      ensureReviewNo().then(function (rno) {
        if (!rno) { note('err', '没找到编号（引用块缺 review_no，正文也没有主键行）'); return; }
        api('/bodhi/ext/sync-report', { object: meta.object, review_no: rno,
                                        columns: meta.columns || '' })
          .then(function (s) {
            if (s.ok) {
              note('ok', '正文已同步（version ' + s.version + '，台账 rev ' + s.rev +
                         '），页面即将刷新…');
              setTimeout(function () { window.location.reload(); }, 900);
            } else {
              note('err', '同步失败：' + (s.error || '') + (s.hint ? '\n' + s.hint : ''));
            }
          });
      });
    }

    function table() {
      var wrap = document.createElement('div');
      wrap.className = 'bx-wrap';
      var t = document.createElement('table');
      t.className = 'bx-table';
      var tr = document.createElement('tr');
      state.cols.forEach(function (c) {
        var th = document.createElement('th');
        th.textContent = state.labels[c.field] || c.label || c.field;
        if (c.key) th.textContent += ' 🔑';
        tr.appendChild(th);
      });
      var thOps = document.createElement('th');
      thOps.textContent = '操作';
      tr.appendChild(thOps);
      var thead = document.createElement('thead');
      thead.appendChild(tr);
      t.appendChild(thead);

      var tb = document.createElement('tbody');
      state.rows.forEach(function (row) {
        var r = document.createElement('tr');
        state.cols.forEach(function (c) {
          var td = document.createElement('td');
          var ro = c.key || c.generated || c.field === 'rule_fp';
          if (ro) {
            td.className = 'bx-ro';
            td.textContent = row[c.field] == null ? '' : String(row[c.field]);
          } else {
            td.appendChild(cellInput(c, row[c.field]));
          }
          r.appendChild(td);
        });
        var ops = document.createElement('td');
        ops.appendChild(btn('保存', 'ghost', function () {
          var fields = {};
          Array.prototype.forEach.call(r.querySelectorAll('[data-field]'), function (el) {
            var f = el.dataset.field;
            if (String(el.value || '') !== String(row[f] == null ? '' : row[f])) {
              fields[f] = el.value;
            }
          });
          if (!Object.keys(fields).length) { note('warn', '没有改动'); return; }
          save(row.issue_no, fields);
        }));
        r.appendChild(ops);
        tb.appendChild(r);
      });
      t.appendChild(tb);
      wrap.appendChild(t);
      return wrap;
    }

    function save(issueNo, fields) {
      note('', '保存中…');
      api('/bodhi/ext/update', {
        object: meta.object, review_no: state.reviewNo, issue_no: issueNo,
        fields: fields, if_rev: state.rev, actor: '评审面板', session_no: '',
        dry_run: false
      }).then(function (res) {
        if (!res.ok) { note('err', (res.error || '失败') + (res.hint ? '\n' + res.hint : '')); return; }
        note('ok', '已写入台账（rev ' + res.rev + '），正在把正文表格同步成台账最新…');
        // 2026-10-25：写成功后**自动回写正文快照**（否则正文与台账不一致 ✗）
        api('/bodhi/ext/sync-report', { object: meta.object, review_no: state.reviewNo,
                                        columns: meta.columns || '' })
          .then(function (s) {
            if (s.ok) {
              // 关键：正文已在库里更新，但 SPA 不会自己重取 ⇒ **自动刷新页面**，否则看到的还是旧正文
              note('ok', '已写入台账（rev ' + res.rev + '）并同步正文（version ' + s.version +
                         '）—— 页面即将刷新以显示最新正文…');
              setTimeout(function () { window.location.reload(); }, 900);
              return;
            }
            note('warn', '台账已写入（rev ' + res.rev + '），但正文同步失败：' +
                         (s.error || '') + (s.hint ? '\n' + s.hint : '') +
                         '\n（可点面板里的「同步正文」重试）');
            load();
          });
      });
    }

    function load() {
      var q = '/bodhi/ext/list?object=' + encodeURIComponent(meta.object) +
              '&review_no=' + encodeURIComponent(state.reviewNo || '') +
              '&title=' + encodeURIComponent(pageTitle()) +
              (meta.columns ? '&columns=' + encodeURIComponent(meta.columns) : '');
      api(q).then(onList);
    }

    function pageTitle() {
      var h = document.querySelector('h1');
      var t = h ? (h.textContent || '').trim() : '';
      return t || (document.title || '').trim();
    }

    function onList(res) {
          if (!res.ok) {
            paint();
            if (res.error === 'need_review_no') {
              note('err', '没找到台账编号：引用块缺 `review_no`、页面正文里也没有台账编号 ⇒ ' +
                          '让工具重写一次正文（会自动补锚点），或在 ① 表补编号');
            } else {
              note('err', '读台账失败：' + (res.error || '') + (res.hint ? '\n' + res.hint : ''));
            }
            panel.appendChild(msg);
            return;
          }
          if (res.review_no && !state.reviewNo) {      // 编号由后端从报告上下文解析 ✓
            state.reviewNo = res.review_no;
            state.reviewSrc = res.review_no_source || 'report_context';
          }
          state.rev = res.rev;
          state.rows = res.rows || [];
          var sc = res.schema || {};
          state.labels = sc.labels || {};
          var cols = ((res.projection || {}).contract || {}).columns;
          if (!cols || !cols.length) {
            cols = (sc.order || []).map(function (f) {
              return { field: f, label: state.labels[f] };
            });
          }
          // 模板给了字段子集 ⇒ 只留这些列，且**按模板顺序**（工具字段可能多于报告所需 ✓）
          if (meta.columns) {
            var want = String(meta.columns).split(',').map(function (s) { return s.trim(); })
              .filter(Boolean);
            var byField = {};
            cols.forEach(function (c) { byField[c.field] = c; });
            var picked = want.map(function (f) {
              return byField[f] || { field: f, label: state.labels[f] };
            });
            if (picked.length) cols = picked;
          }
          state.cols = cols;
          state.schemaHash = (res.projection || {}).schemaHash || '';
          console.log('[bodhi-ext] 回执 rows=' + state.rows.length + ' cols=' + state.cols.length +
                      ' hash=' + state.schemaHash);
          paint();
          panel.appendChild(table());
          panel.appendChild(msg);
          note('ok', state.rows.length + ' 条 ｜ 列 ' + state.cols.length +
               ' ｜ 改「非只读」的格子后点保存（写台账 + 审计）');
    }

    mount.textContent = '';
    mount.appendChild(panel);
    if (!meta.object) {
      note('err', '引用块缺 object（业务对象 slug），无法挂载面板');
      panel.appendChild(msg);
      return;
    }
    load();
  }

  // ── 契约卡（DataObject 实例页）：只读展示 x-ext，可重新订阅 ─────────────
  // 为什么单列一块：报告面板要「台账编号 + rev」，契约卡只要「对象 + 契约 hash」；
  // 靠内容里的 `kind: contract` 区分（渲染器不一定给 language-xxx class ✓）。
  function isContractBlock(text) {
    var t = text || '';
    return t.indexOf('kind: contract') >= 0 && t.indexOf('object:') >= 0;
  }

  function renderContractCard(mount, meta) {
    style();
    var panel = document.createElement('div');
    panel.className = 'bx-panel';
    panel.style.cssText = 'display:block;min-height:48px;border:1px solid #dcdfe6;' +
                          'border-radius:8px;padding:10px 12px;margin:8px 0;background:#fff';
    var head = document.createElement('div');
    head.className = 'bx-head';
    var body = document.createElement('div');
    var msg = document.createElement('div');
    msg.className = 'bx-msg';
    panel.appendChild(head);
    panel.appendChild(body);
    panel.appendChild(msg);
    mount.textContent = '';
    mount.appendChild(panel);

    function note(kind, text) {
      msg.className = 'bx-msg ' + (kind || '');
      msg.textContent = text || '';
    }

    function draw(res) {
      head.textContent = '';
      head.appendChild(tag('bx-tag', meta.object));
      var b = res.binding || {};
      head.appendChild(tag('bx-tag', (b.tool || meta.tool || '') +
                                   (b.modes ? ' · ' + String(b.modes).replace(/,/g, '/') : '')));
      if (b.keys) head.appendChild(tag('bx-tag', '主键 ' + String(b.keys).replace(/,/g, '+')));
      head.appendChild(tag('bx-tag', '契约 ' + ((res.contract || {}).schemaHash || '未订阅')));
      if ((res.drift || {}).changed) {
        head.appendChild(tag('bx-msg warn', '⚠ 契约已变（上次 ' +
                             ((res.drift || {}).previous_hash || '—') + '）'));
      }
      head.appendChild(btn('刷新契约', 'ghost', function () { resubscribe(); }));

      var cols = ((res.contract || {}).columns) || [];
      if (!cols.length) {
        body.textContent = '（契约没有列声明：该工具未下发 x-ext）';
        return;
      }
      var wrap = document.createElement('div');
      wrap.className = 'bx-wrap';
      var table = document.createElement('table');
      table.className = 'bx-table';
      var tr = document.createElement('tr');
      ['字段', '标签', '枚举/取值'].forEach(function (h) {
        var th = document.createElement('th');
        th.textContent = h;
        tr.appendChild(th);
      });
      table.appendChild(tr);
      cols.forEach(function (c) {
        var row = document.createElement('tr');
        [c.field || '', c.label || '', (c.enum || []).join(' / ') || '—'].forEach(function (v) {
          var td = document.createElement('td');
          td.textContent = v;
          row.appendChild(td);
        });
        table.appendChild(row);
      });
      wrap.appendChild(table);
      body.textContent = '';
      body.appendChild(wrap);
      note('', '契约来源 ' + ((res.service || {}).url || '') + ' ｜ 取回于 ' +
               (res.fetched_at || '') + ' ｜ 只读：列/枚举只认 `tools/list`（服务端单一真源）');
    }

    // ── 绑定 / 映射（2026-10-29 用户口径）────────────────────────────────────
    //   绑定 = **图关系**（`bpmn:dataObjectBindingTool`），有边即绑定；
    //   映射 = 业务列名/列序（以 DataObject 为准）→ 工具字段，写在边上 `fieldMap`。
    var bindBox = document.createElement('div');
    bindBox.className = 'bx-bind';
    panel.appendChild(bindBox);

    function renderBind(res) {
      bindBox.textContent = '';
      var kb = res.kb_id || currentKb();
      var head2 = document.createElement('div');
      head2.className = 'bx-msg';
      head2.textContent = (res.bound
        ? '已绑定工具：' + (res.bindings || []).map(function (b) { return b.tool; }).join(', ')
        : '未绑定外部工具 —— 本对象按「软定义表格」处理（智能体按 definition/note 自行处理，不调外部服务）');
      bindBox.appendChild(head2);

      (res.bindings || []).forEach(function (b) {
        var row = document.createElement('div');
        row.style.cssText = 'margin:4px 0';
        row.appendChild(tag('bx-tag', b.tool));
        row.appendChild(tag('bx-tag', '映射 ' +
          (b.status === 'confirmed' ? '已定稿' : (b.status ? b.status : '未映射'))));
        row.appendChild(btn('解绑', 'ghost', function () {
          api('/bodhi/ext/unbind', { object: meta.object, kb_id: kb, tool: b.tool })
            .then(function (r) {
              note(r.ok ? 'ok' : 'err', r.ok ? ('已解绑 ' + b.tool) : ('解绑失败：' + (r.error || '')));
              loadBind();
            });
        }));
        row.appendChild(btn('写映射…', 'ghost', function () { openMapping(b, res); }));
        bindBox.appendChild(row);
      });

      var rest = res.unbound_tools || [];
      if (rest.length) {
        var sel = document.createElement('select');
        rest.forEach(function (t) {
          var o = document.createElement('option');
          o.value = t.tool;
          o.textContent = t.tool + (t.schema_hash ? '' : '（未同步契约）');
          sel.appendChild(o);
        });
        bindBox.appendChild(sel);
        bindBox.appendChild(btn('绑定', '', function () {
          api('/bodhi/ext/bind', { object: meta.object, kb_id: kb, tool: sel.value })
            .then(function (r) {
              note(r.ok ? 'ok' : 'err',
                   r.ok ? ('已绑定 ' + sel.value + '（映射待定稿）') : ('绑定失败：' + (r.error || '')));
              loadBind();
            });
        }));
      } else if (!(res.bindings || []).length) {
        bindBox.appendChild(btn('同步工具契约', 'ghost', function () {
          api('/bodhi/ext/tool/sync', { kb_id: kb }).then(function (r) {
            note(r.ok ? 'ok' : 'err',
                 r.ok ? ('已同步 ' + ((r.synced || []).length) + ' 个工具页') : ('同步失败：' + (r.error || '')));
            loadBind();
          });
        }));
      }
    }

    function openMapping(b, res) {
      var cols = ((res.contract || {}).columns) || [];
      var cur = {};
      try { cur = JSON.parse(b.field_map || '{}') || {}; } catch (e) { cur = {}; }
      var box = document.createElement('div');
      box.style.cssText = 'margin:6px 0;padding:6px;border:1px dashed #dcdfe6';
      var inputs = cols.map(function (c, i) {
        var row = document.createElement('div');
        row.style.cssText = 'margin:2px 0';
        row.appendChild(tag('bx-tag', c.field));
        var inp = document.createElement('input');
        inp.value = (((cur.items || [])[i] || {}).label) || c.label || '';
        inp.placeholder = '业务列名（领域口径）';
        inp.style.cssText = 'width:180px';
        inp.dataset.field = c.field;
        row.appendChild(inp);
        box.appendChild(row);
        return inp;
      });
      box.appendChild(btn('保存映射（定稿）', '', function () {
        var arr = inputs.map(function (inp) {
          return { field: inp.dataset.field, label: inp.value.trim() || inp.dataset.field };
        });
        api('/bodhi/ext/mapping', { object: meta.object, kb_id: (res.kb_id || currentKb()),
                                    tool: b.tool, fields: arr })
          .then(function (r) {
            note(r.ok ? 'ok' : 'err',
                 r.ok ? ('映射已定稿（' + (r.columns || 0) + ' 列）') : ('保存失败：' + (r.error || '')));
            if (r.ok) { try { box.remove(); } catch (e) { /* 忽略 */ } loadBind(); }
          });
      }));
      bindBox.appendChild(box);
    }

    function loadBind() {
      var kb = currentKb();
      api('/bodhi/ext/binding?object=' + encodeURIComponent(meta.object) +
          (kb ? ('&kb_id=' + encodeURIComponent(kb)) : ''))
        .then(function (r) { if (r && r.ok) renderBind(r); });
    }
    if (meta.kind !== 'tool') loadBind();

    function resubscribe() {
      var kb = currentKb();
      var isTool = (meta.kind === 'tool') || /^agent\/tool\//.test(meta.object || '');
      note('', isTool ? '刷新契约（工具页）…' : '重新订阅契约…');
      var req = isTool
        ? api('/bodhi/ext/tool/refresh',
              { kb_id: kb, tool: meta.tool || String(meta.object || '').split('/').pop() })
        : api('/bodhi/ext/describe',
              { object: meta.object, write_projection: true, write_anchor: true });
      req.then(function (r) {
        if (r.ok) {
          draw(r);
          note('ok', isTool
            ? ('契约已刷新：' + ((r.synced || []).map(function (x) { return x.tool + ' ' +
                (x.schemaHash || ''); }).join('、') || '无变化'))
            : ('契约已更新（' + ((r.contract || {}).schemaHash || '') + '）'));
          setTimeout(function () { window.location.reload(); }, 900);
        } else {
          note('err', '刷新失败：' + (r.error || '') + (r.hint ? '\n' + r.hint : ''));
        }
      });
    }

    // 打开即读（读不写）
    if (meta.kind === 'tool' || /^agent\/tool\//.test(meta.object || '')) {
      var toolName = meta.tool || String(meta.object || '').split('/').pop();
      api('/bodhi/ext/tool?kb_id=' + encodeURIComponent(currentKb()) +
          '&tool=' + encodeURIComponent(toolName))
        .then(function (r) {
          if (!r.ok) { note('err', '取工具契约失败：' + (r.error || '')); return; }
          draw({ contract: { columns: [], schemaHash: (r.tool_row || {}).schema_hash },
                 binding: { tool: r.tool },
                 service: { url: (r.tool_row || {}).modes || '' } });
          note('', '本库' + (r.modeled ? '已建模' : '未建模') + ' ｜ 绑定对象 ' +
                   ((r.bound_objects || []).length) + ' 个');
        });
    } else {
      api('/bodhi/ext/describe', { object: meta.object, write_projection: false })
        .then(function (r) {
          if (r.ok) draw(r);
          else note('err', '取契约失败：' + (r.error || '') + (r.hint ? '\n' + r.hint : ''));
        });
    }
  }

  // 2026-10-25 修：**不能只靠 class** —— 多数 Markdown 渲染器对未知语言只给 `class="hljs"`，
  // 不会有 `language-bodhi-ext`。所以按**块内容**识别：同时含 object:/review_no:/schema_hash: ✓
  function isRefBlock(text) {
    var t = text || '';
    // 只认 `object:`（= 这是"绑定到某个外部接口"的锚点）；`review_no`/`schema_hash` **可缺**
    // —— 编号能从报告上下文取、hash 只有订阅过才有（缺了也要出面板 ✓）
    return t.indexOf('object:') >= 0 && t.indexOf('kind: contract') < 0;
  }

  function candidates() {
    var out = [];
    var pick = function (el, t) {
      if (isRefBlock(t)) out.push({ el: el, kind: 'ledger' });
      else if (isContractBlock(t)) out.push({ el: el, kind: 'contract' });
    };
    Array.prototype.forEach.call(document.querySelectorAll('pre'), function (pre) {
      if (pre.dataset.bxMounted) return;
      pick(pre, pre.textContent);
    });
    if (!out.length) {                     // 极端情况：没有 <pre>，只有 <code>
      Array.prototype.forEach.call(document.querySelectorAll('code'), function (code) {
        var box = code.parentNode;
        if (!box || box.dataset.bxMounted) return;
        pick(box, code.textContent);
      });
    }
    return out;
  }

  function scan() {
    var found = candidates();
    found.forEach(function (hit) {
      var pre = hit.el;
      var meta = parseBlock(pre.textContent);
      var key = (hit.kind === 'contract' ? 'c|' : 'l|') + (meta.object || '') +
                (hit.kind === 'contract' ? '' : '|' + (meta.review_no || ''));
      var parent = pre.parentNode;
      // 去重：同一容器里已经挂过同一份引用块 ⇒ 丢掉这次新渲染出来的 pre（Vue 重渲染会重建节点）
      if (parent && parent.querySelector && parent.querySelector('[data-bx-for="' + key + '"]')) {
        try { parent.removeChild(pre); } catch (e) { /* 忽略 */ }
        return;
      }
      pre.dataset.bxMounted = '1';
      var host = document.createElement('div');
      host.dataset.bxFor = key;
      try {
        parent.replaceChild(host, pre);
      } catch (e) {
        host = pre;
      }
      try {
        if (hit.kind === 'contract') {
          renderContractCard(host, meta);
          console.log('[bodhi-ext] 契约卡已挂载：', meta.object);
        } else {
          renderPanel(host, meta);
          console.log('[bodhi-ext] 面板已挂载：', meta.review_no);
        }
      } catch (err) {
        host.textContent = '面板加载失败：' + err;
        console.error('[bodhi-ext] 挂载失败：', err);
      }
    });
    return found.length;
  }

  function diagnose() {
    var blocks = document.querySelectorAll('pre, code');
    var hits = 0, sample = [];
    Array.prototype.forEach.call(blocks, function (el) {
      var t = el.textContent || '';
      if (isRefBlock(t) || isContractBlock(t)) hits += 1;
      if (sample.length < 6 && t.trim()) {
        sample.push({ tag: el.tagName, cls: el.className || '(无)', head: t.trim().slice(0, 40) });
      }
    });
    var info = { loaded: true, refBlocks: hits, preCodeBlocks: blocks.length, sample: sample };
    console.log('[bodhi-ext] diagnose', info);
    return info;
  }

  function boot() {
    style();
    var n = scan();
    console.log('[bodhi-ext] ' + VER + ' loaded ｜ 已挂载 ' + n + ' 个引用块（可 console 里 bodhiExtPanel.diagnose()）');
    new MutationObserver(function () { scan(); })
      .observe(document.body, { childList: true, subtree: true });
    window.bodhiExtPanel = { rescan: scan, diagnose: diagnose };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
