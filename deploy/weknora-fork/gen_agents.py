"""给 WeKnora 建「本体知识提取」智能体（BMM / EA）—— 从已有智能体克隆 config 再覆盖。

为什么这样做（见 docs/weknora-fork.md §10.13）
--------------------------------------------
- agent-type preset（`agent_type_presets.yaml`）只是**编辑器里的模板**：前端
  `stores/editorResources.ts -> AgentEditorModal.vue` 才用它，**不会出现在智能体列表**；
- 运行时用的是 `custom_agents.config.system_prompt`（**提示词全文**），
  见 `session_agent_qa.go:360`：`if config.SystemPrompt != "" { UseCustomSystemPrompt = true }`；
- 所以直接把提示词文本 + 工具集写进 `custom_agents` 就能得到一个**开箱可用的智能体**。

用法
----
    python deploy/weknora-fork/gen_agents.py            # 只生成 SQL（config/agents.sql）
    python deploy/weknora-fork/gen_agents.py --apply    # 生成并执行（写入 WeKnora 库）
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import yaml

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
CONFIG = HERE / "config"
OUT_SQL = CONFIG / "agents.sql"

TEMPLATE_IDS = {"bmm": "ontology_extract_agent_bmm", "ea": "ontology_extract_agent_ea"}
NAMES = {"bmm": "本体知识提取 · BMM 业务动机模型", "ea": "本体知识提取 · EA 企业架构"}
DESCRIPTIONS = {
    "bmm": "按 BMM 业务动机模型从知识库片段抽取要素与关系，并为每个要素写入本体类型（bmm:*）的 wiki 页面。",
    "ea": "按 EA 企业架构本体从知识库片段抽取要素与关系，并为每个要素写入本体类型（ea:*）的 wiki 页面。",
}
ALLOWED_TOOLS = [
    "grep_chunks", "list_knowledge_chunks", "get_document_info",
    "wiki_search", "wiki_read_page", "wiki_write_page", "todo_write", "thinking",
]
DB_CONTAINER, DB_USER, DB_NAME, DB_PASSWORD = "WeKnora-postgres", "postgres", "WeKnora", "postgres123!@#"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def load_templates() -> dict[str, str]:
    doc = yaml.safe_load((CONFIG / "agent_system_prompt.yaml").read_text(encoding="utf-8"))
    by_id = {t["id"]: t.get("content", "") for t in doc.get("templates", [])}
    out = {}
    for key, tid in TEMPLATE_IDS.items():
        if tid not in by_id:
            raise SystemExit("模板 %s 不存在（先跑 gen_agent_config.py）" % tid)
        out[key] = by_id[tid]
    return out


def sql_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def build_sql(templates: dict[str, str]) -> str:
    parts = ["-- 由 deploy/weknora-fork/gen_agents.py 生成：克隆已有智能体的 config，覆盖本体提取相关字段"]
    for key, content in templates.items():
        agent_id = "bodhi-ontology-%s" % key
        overrides = {
            "agent_mode": "smart-reasoning",
            "agent_type": "custom",                 # Go 端只认这五个常量值，用 custom 最安全
            "system_prompt_id": TEMPLATE_IDS[key],
            "system_prompt": content,               # 运行时用的是这个文本
            "temperature": 0.1,
            "max_iterations": 40,
            "allowed_tools": ALLOWED_TOOLS,
            "retain_retrieval_history": True,
            "faq_priority_enabled": False,
            "web_search_enabled": False,
            "kb_selection_mode": "selected",
        }
        pairs = []
        for field, value in overrides.items():
            if isinstance(value, bool):
                literal = "true" if value else "false"
            elif isinstance(value, (int, float)):
                literal = str(value)
            elif isinstance(value, list):
                literal = "jsonb_build_array(%s)" % ", ".join(sql_literal(v) for v in value)
            else:
                literal = sql_literal(value)
            pairs.append("%s, %s" % (sql_literal(field), literal))
        overrides_sql = "jsonb_build_object(%s)" % ", ".join(pairs)
        parts.append("""
-- %s
DELETE FROM custom_agents WHERE id = %s;
INSERT INTO custom_agents (id, name, description, avatar, is_builtin, tenant_id, created_by,
                           config, created_at, updated_at, runnable_by_viewer)
SELECT %s, %s, %s, '', false, t.tenant_id, COALESCE(t.created_by, ''),
       (t.config || %s), now(), now(), true
FROM (SELECT * FROM custom_agents WHERE is_builtin = true ORDER BY created_at LIMIT 1) t;
""" % (NAMES[key], sql_literal(agent_id), sql_literal(agent_id), sql_literal(NAMES[key]),
       sql_literal(DESCRIPTIONS[key]), overrides_sql))
    return "".join(parts)


def run_sql(sql: str) -> None:
    cmd = ["wsl", "-d", "Ubuntu", "-u", "root", "docker", "exec", "-i",
           "-e", "PGPASSWORD=" + DB_PASSWORD, DB_CONTAINER,
           "psql", "-U", DB_USER, "-d", DB_NAME, "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"]
    done = subprocess.run(cmd, input=sql, text=True, encoding="utf-8", capture_output=True, check=False)
    print("psql rc=%s %s" % (done.returncode, (done.stderr or "").strip()[:500]))
    if done.returncode != 0:
        raise SystemExit("写入失败")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成/写入「本体知识提取」智能体")
    parser.add_argument("--apply", action="store_true", help="同时写入数据库")
    args = parser.parse_args()

    templates = load_templates()
    sql = build_sql(templates)
    CONFIG.mkdir(parents=True, exist_ok=True)
    OUT_SQL.write_text(sql, encoding="utf-8")
    print("写出 %s（%d 字节）" % (OUT_SQL.relative_to(REPO).as_posix(), len(sql.encode("utf-8"))))
    for key, content in templates.items():
        print("  智能体 bodhi-ontology-%-3s 提示词 %5d 字符  tools=%d" % (key, len(content), len(ALLOWED_TOOLS)))
    if args.apply:
        run_sql(sql)
        print("已写入 WeKnora 数据库")
    return 0


if __name__ == "__main__":
    sys.exit(main())
