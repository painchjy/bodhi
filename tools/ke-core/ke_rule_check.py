#!/usr/bin/env python3
"""R1 判定样板（确定性）：规则5「开源软件适用规则」= 文档抽取值 vs 清单集合比对
   清单来源：DataObject「开源软件清单」（挡板/接口；当前读其 skos:note 样例）"""
import json
import os
import sys

os.chdir("/mnt/c/Users/PHJY/source/bodhi2")
sys.path.insert(0, "tools/ke-core")
import ke_db  # noqa: E402
import ke_import  # noqa: E402
import ke_review  # noqa: E402

L = open("logs/_r1_sample.txt", "w", encoding="utf-8")


def w(*a):
    print(*a, file=L)
    L.flush()


KB = "技术方案评审领域模型"
kb = ke_db.resolve_kb_id(KB)[0]

w("== ① 取清单（DataObject「开源软件清单」的 skos:note 样例 = 挡板输出）==")
slug_obj = ke_import.page_slug("bpmn:DataObject", "开源软件清单")
row = ke_db.psql_csv(
    "SELECT COALESCE(page_metadata->'ontology'->'attributes'->>'skos:definition','') AS dfn, "
    "       COALESCE(page_metadata->'ontology'->'attributes'->>'skos:note','') AS note "
    " FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL AND slug = %s"
    % (ke_db.sql_str(kb), ke_db.sql_str(slug_obj)))
note = (row[0].get("note") if row else "") or ""
w("  来源页 = %s" % slug_obj)
cert: dict = {}
for line in note.splitlines():
    line = line.strip().lstrip("·").strip()
    if "——" in line:
        left, status = line.split("——", 1)
        parts = left.strip().split()
        if parts:
            name = parts[0]
            ver = parts[1] if len(parts) > 1 else ""
            cert[(name.lower(), ver)] = status.strip()
w("  清单条数 = %d ；样例 = %s" % (len(cert), list(cert.items())[:3]))

w("\n== ② 取规则 5 的口径（rules_of_task，真读）==")
rep = ke_review.rules_of_task(KB, "逐项评审")
r5 = [x for x in rep["rules"] if "开源软件" in x["title"]]
if not r5:
    w("  ✗ 未找到规则 5")
    raise SystemExit(1)
r5 = r5[0]
w("  rule_slug=%s  enforcement=%s  needs_extract=%s" % (r5["slug"], r5["enforcement"], r5["needs_extract"]))
w("  knowledgeNeed=%s" % r5["knowledgeNeed"])
w("  rules_hash=%s" % rep["hash"])

w("\n== ③ 文档抽取值（样例 1 技术栈表格，逐字）==")
extracted = [("MySQL", "8.0.32", "第 4 章 技术架构 · 技术栈表 · 第 1 行"),
             ("Redis", "7.2.4", "第 4 章 技术架构 · 技术栈表 · 第 2 行"),
             ("Nginx", "1.24.0", "第 4 章 技术架构 · 技术栈表 · 第 3 行"),
             ("PostgreSQL", "15.6", "第 4 章 技术架构 · 技术栈表 · 第 4 行")]
for n, v, loc in extracted:
    w("  %-12s %-8s %s" % (n, v, loc))

w("\n== ④ 判定（集合比对；逐字证据）==")
findings = []
for name, ver, loc in extracted:
    status = cert.get((name.lower(), ver))
    if status is None:
        findings.append({"quote": "%s %s" % (name, ver), "locator": loc,
                         "why": "不在清单内（或版本不符）"})
    elif "未认证" in status:
        findings.append({"quote": "%s %s（清单标记：%s）" % (name, ver, status), "locator": loc,
                         "why": "清单内但状态=未认证"})
conclusion = "违规" if findings else "合规"
w("  conclusion = %s" % conclusion)
for f in findings:
    w("  · 违规证据：%s  ← %s（%s）" % (f["quote"], f["why"], f["locator"]))
w("  契约字段：rule_slug=%s / severity=%s / source=%s" % (r5["slug"],
                                                          "高" if r5["enforcement"] == "严格执行" else "中",
                                                          r5["source"]))

w("\n== ⑤ 断言 ==")
ok1 = conclusion == "违规"
ok2 = any("PostgreSQL 15.6" in f["quote"] for f in findings)
ok3 = all((n.lower(), v) in cert for n, v, _ in extracted[:3])
ok4 = bool(r5["source"] and r5["slug"] and r5["needs_extract"])
w("  ① 样例 1 判违规（因 PostgreSQL 15.6 不在清单）= %s" % ok1)
w("  ② 违规证据指向 PostgreSQL 15.6 = %s" % ok2)
w("  ③ 前 3 个版本命中清单（不误报）= %s" % ok3)
w("  ④ 结论契约字段齐（rule_slug/source/needs_extract）= %s" % ok4)
w("  合计：%s" % ("全绿 ✓" if all((ok1, ok2, ok3, ok4)) else "有失败 ✗"))
L.close()
print("R1_SAMPLE_DONE")
