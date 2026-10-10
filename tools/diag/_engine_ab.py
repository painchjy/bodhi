"""引擎 A/B（**零写入**）：同一份真实载荷，legacy vs kernel 两条"写库"路径对比 + 内核实收校验。

为什么需要：`save_elements` 的写库段在 dry_run 下不会执行 → 普通 dry_run 复测**验证不到**接线。
本脚本把两条路径的写入**全部拦截**（不改任何数据），再单独用真内核 dry_run 校验：

· `ke_db.psql`：**只拦截写**（`stdin=True`，即 BEGIN..COMMIT / 单条 UPDATE）→ 记录并返回空串；
  `csv=True` 的读放行（`psql_csv` 依赖它）。
· `ke_pages.write_knowledge_batch`：kernel 路径的写 → 只**记录 specs**。
· 图侧 `ensure_instances` / `add_edges_batch` / `strip_wiki_pages`：no-op（本脚本不验证图）。
· 最后：把记录到的 specs 逐个喂**真内核** `write_knowledge(dry_run=True)`，并与**库里已写正文**
  （上一轮 legacy apply 落的）逐字比对。

用法：/opt/bodhi-venv/bin/python3 _engine_ab.py [args.json]
"""
import inspect
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "ke-core"))
sys.path.insert(0, str(REPO / "tools" / "ontology-mcp"))
import ke_db  # noqa: E402
import ke_graph  # noqa: E402
import ke_pages  # noqa: E402
import server  # noqa: E402

ARG = sys.argv[1] if len(sys.argv) > 1 else "logs/args/20261010-083013-242370-save_knowledge.json"
raw = json.loads((REPO / ARG).read_text(encoding="utf-8"))
raw["mode"] = "apply"                      # 只有 apply 才走到写库段（写入已被拦截）
sig = set(inspect.signature(server.save_knowledge).parameters)
args = {k: v for k, v in raw.items() if k in sig}
KB = str(args.get("kb_id") or "")

# ---------------- 拦截 ----------------
specs_seen: list = []
psql_writes: list = []
_real_psql = ke_db.psql


def fake_psql(sql, stdin=False, csv=False):
    if stdin:                              # 写事务/写语句 → 拦
        psql_writes.append(sql.strip()[:70])
        return ""
    return _real_psql(sql, stdin=stdin, csv=csv)


ke_db.psql = fake_psql
server.psql = fake_psql


def fake_batch(kb_id, specs, **kw):
    specs_seen.extend(specs)
    return {"applied_count": len(specs), "failed": 0, "pages": [], "resume_hint": ""}


ke_pages.write_knowledge_batch = fake_batch
ke_graph.ensure_instances = lambda *a, **k: 0
ke_graph.add_edges_batch = lambda *a, **k: {"written": 0, "errors": [], "types": 0, "total": 0}
ke_graph.strip_wiki_pages = lambda *a, **k: {"ok": True, "total": 0, "stripped": 0, "skipped": 0}


def run(engine: str) -> dict:
    os.environ["BODHI_SAVE_ENGINE"] = engine
    specs_seen.clear()
    psql_writes.clear()
    server._reset_request_cache()
    return server.save_knowledge(**args)


legacy = run("legacy")
kernel = run("kernel")


def brief(r: dict) -> dict:
    return {k: (len(r[k]) if isinstance(r.get(k), list) else r.get(k))
            for k in ("engine", "applied", "created", "merged", "pending",
                      "violations", "unmatched", "kernel") if k in r}


print("legacy:", json.dumps(brief(legacy), ensure_ascii=False))
print("kernel:", json.dumps(brief(kernel), ensure_ascii=False))
lv = [x.get("slug") for x in (legacy.get("page_versions") or [])]
kv = [x.get("slug") for x in (kernel.get("page_versions") or [])]
print("page_versions 逐条一致 =", lv == kv, "（%d 条）" % len(lv))
print("legacy 写库动作 =", len(psql_writes), "条 psql（已拦截）；kernel 记录 specs =", len(specs_seen))

# ---------------- 内核实收 + 与库里正文逐字比对 ----------------
import ke_ontology  # noqa: E402
acc, mismatch, checked, stripped_only = 0, [], 0, 0
for spec in specs_seen:
    try:
        res = ke_pages.write_knowledge(KB, spec, dry_run=True, strict_source=False,
                                       sync_folders=False)
        ok = (res.get("slug") == spec["slug"] and res.get("mode") == spec["mode"])
        acc += 1 if ok else 0
        if not ok:
            mismatch.append((spec["slug"], "内核回执不符", res.get("slug"), res.get("mode")))
    except Exception as exc:  # noqa: BLE001
        mismatch.append((spec["slug"], "内核拒收: %s" % str(exc)[:120]))
        continue
    # 与库里已写正文逐字比对（该库是上一轮 legacy apply 落的）
    rows = ke_db.psql_csv("SELECT COALESCE(content,'') AS c FROM wiki_pages WHERE knowledge_base_id=%s "
                          "AND slug=%s AND deleted_at IS NULL" % (ke_db.sql_str(KB),
                                                                 ke_db.sql_str(spec["slug"])))
    if not rows:
        continue
    checked += 1
    kern = "\n".join(ke_pages._contract_head(spec)) + "\n" + (spec.get("wiki_content") or "").lstrip()
    cur = rows[0]["c"]
    # 与库里比对要**走 apply 的真实流程**：内核写完正文后，`save_knowledge` 的 apply 段还会
    # `strip_wiki_pages` 去掉「本体关系 / 被引用」小节 —— 库里存的是 strip 之后的版本。
    if ke_graph.strip_relation_sections(kern).rstrip() != cur.rstrip():
        mismatch.append((spec["slug"], "正文不一致（strip 后仍不同）",
                         len(cur), len(ke_graph.strip_relation_sections(kern))))
        if len(mismatch) <= 2:                      # 打印真实 diff，定位那几行差异
            import difflib  # noqa: PLC0415
            print("\n--- diff：%s ---" % spec["slug"])
            for ln in list(difflib.unified_diff(
                    cur.rstrip().splitlines(),
                    ke_graph.strip_relation_sections(kern).rstrip().splitlines(),
                    "库里(legacy)", "内核(kernel)", lineterm="", n=1))[:14]:
                print("   ", ln[:160])
    elif kern.rstrip() != cur.rstrip():
        stripped_only += 1     # 差异只在"关系小节"（apply 段会 strip）→ 等价

print("\n内核接受 = %d/%d；与库里正文比对 = %d 页（strip 后逐字相同 = %d，仅关系小节差 = %d，不一致 = %d）"
      % (acc, len(specs_seen), checked, checked - stripped_only - len(mismatch),
         stripped_only, len(mismatch)))
for m in mismatch[:5]:
    print("  ·", m)
print("RESULT:", "PASS" if (acc == len(specs_seen) and not mismatch and lv == kv) else "FAIL")
