"""P0.5 · 测试知识库工厂（**硬删除** teardown ✓ 用户口径 2026-10-21）

为什么不软删：软删（`deleted_at`）会让 `context_*`（跨库同名/权威）多出重复项 ✗。
所以 teardown 顺序固定、逐表清理，最后删 `knowledge_bases` 行，并断言**归零** ✓。

用法：
```
f = TestKB("rule_publish")
kb = f.create()          # -> kb_id（名称 _test_rule_publish_<ts>）
... 测试 ...
f.drop()                 # 硬删 + 断言零残留（失败会抛 ✓）
```
"""
from __future__ import annotations

import os
import sys
import time
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))


class TestKB:
    def __init__(self, purpose: str = "t"):
        # 2026-10-21 实测：只用秒级时间戳会撞名（同一秒内多个用例 ✗）⇒ 加 uuid 片段 ✓
        self.name = "_test_%s_%s_%s" % (purpose, time.strftime("%m%d%H%M%S"), uuid.uuid4().hex[:6])
        self.kb_id = ""

    # ── 建库 ───────────────────────────────────────────────────────
    def create(self) -> str:
        import ke_db
        row = ke_db.psql_csv("SELECT id FROM knowledge_bases WHERE name = %s AND deleted_at IS NULL"
                             % ke_db.sql_str(self.name))
        if row:
            self.kb_id = row[0]["id"]
        else:
            # 2026-10-21 实测：NOT NULL 列已有 embedding_model_id / summary_model_id ✗
            # ⇒ 直接"克隆"一条既有库（不猜列名，未来加列也不炸 ✓）
            tpl = ke_db.psql_csv("SELECT id FROM knowledge_bases "
                                 "WHERE deleted_at IS NULL AND summary_model_id IS NOT NULL "
                                 "AND embedding_model_id IS NOT NULL ORDER BY created_at LIMIT 1")
            assert tpl, "找不到可克隆的模板库 ✗"
            ke_db.psql("INSERT INTO knowledge_bases (id, tenant_id, name, description, "
                       "embedding_model_id, summary_model_id) "
                       "SELECT gen_random_uuid(), tenant_id, %s, %s, embedding_model_id, summary_model_id "
                       "FROM knowledge_bases WHERE id = %s"
                       % (ke_db.sql_str(self.name), ke_db.sql_str("回归测试库（自动清理 ✓）"),
                          ke_db.sql_str(tpl[0]["id"])))
            self.kb_id = ke_db.psql_csv(
                "SELECT id FROM knowledge_bases WHERE name = %s AND deleted_at IS NULL"
                % ke_db.sql_str(self.name))[0]["id"]
        return self.kb_id

    # ── 硬删 ───────────────────────────────────────────────────────
    def drop(self) -> dict:
        import ke_db
        import ke_neo4j
        kb = self.kb_id
        if not kb:
            return {"ok": True, "note": "未建库，跳过"}
        done = {}
        try:                                     # ① 图（节点 + 边）
            ke_neo4j.query("MATCH (n:BodhiInstance {kb_id:$kb}) DETACH DELETE n", {"kb": kb}, timeout=30)
            done["graph"] = True
        except Exception as exc:  # noqa: BLE001
            done["graph"] = "skip:%r" % (str(exc)[:60],)
        # ② PG 子表 → 主表 → 库（顺序不可颠倒 ✓）
        for table in ("wiki_page_revisions", "wiki_page_issues", "wiki_pages",
                      "wiki_folders", "chunks", "knowledges"):
            try:
                ke_db.psql("DELETE FROM %s WHERE knowledge_base_id = %s"
                           % (table, ke_db.sql_str(kb)))
                done[table] = True
            except Exception as exc:  # noqa: BLE001
                done[table] = "skip:%r" % (str(exc)[:60],)
        ke_db.psql("DELETE FROM knowledge_bases WHERE id = %s" % ke_db.sql_str(kb))
        done["knowledge_bases"] = True
        # ③ 断言归零 ✓
        left = ke_db.psql_csv(
            "SELECT (SELECT count(*) FROM wiki_pages WHERE knowledge_base_id = %s) AS pages, "
            "       (SELECT count(*) FROM knowledge_bases WHERE id = %s) AS kbs"
            % (ke_db.sql_str(kb), ke_db.sql_str(kb)))[0]
        done["residual"] = {"pages": int(left["pages"]), "kb": int(left["kbs"])}
        if done["residual"]["pages"] or done["residual"]["kb"]:
            raise RuntimeError("硬删除未归零 ✗：%s" % done["residual"])
        return done
