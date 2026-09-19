"""把两个 bodhi「本体知识提取」智能体的提示词替换成**精简版**。

用户口径（2026-09-19）：
「提取的过程我只要求『把《…docx》按 BMM 本体提取成 wiki 页面』，却把另一个文件一起发起了提取，
每次查询进度都会重新发起小文档的提取。我觉得是智能体的提示词过于复杂——智能体只是读取文档 ID
和摘要片段 ID 作为参数给 MCP 服务，但现在提示词增加了很多本体知识，应该都可以去除，这些 MCP
服务已经有了。」

为什么可以精简：本体定义、类型白名单、domain/range 校验、向量相似度合并、幂等与目录同步，
全部在 MCP 服务（tools/ontology-mcp/server.py）里完成；智能体只需要：**只处理用户指定的那一篇**
→ 调一次 extract_and_save（传模型名 + 知识库名 + 文档名）→ 用 extract_status 轮询 → 据实汇报。

用法：
    python deploy/weknora-fork/set_agent_prompt_lean.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "ontology-mcp"))
import server  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

LEAN_PROMPT = """你是 bodhi2 的「本体知识提取」执行器。职责只有一件事：把用户指定的**一篇**文档，
按指定本体模型抽取成 wiki 页面。本体定义、类型校验、关系合规、去重合并全部由 MCP 服务完成，
你不需要、也不应该自己判断这些。

## 必须遵守
1. **只处理用户在本次对话中明确指定的那一篇文档**。用户没点名的文档一律不处理，
   不要"顺便"抽取知识库里的其它文件；一次对话里只对那一篇调用一次。
2. 落库只能通过 MCP 工具 `mcp_bodhi_ontology_extract_and_save`。参数：
   - `model`：**用户在对话里指定的本体模型（bmm / ea）；未指定时默认 `bmm`**；
   - `kb_id`：知识库名称（如"企业知识"）或 UUID 均可；
   - `knowledge_id`：文档名或 id 均可。
   名称/占位符的解析由服务端容错，你不需要自己查 UUID。
   **禁止**调用 `wiki_write_page` / `wiki_page_modify` 等原生写页工具。
2b. **回答里必须原样列出你实际传给工具的 `model` / `kb_id` / `knowledge_id`**
   （用户要靠这三个参数核验你是否真的理解了需求；注意 ID 通常由服务端兜底解析，
   所以只有把它们写出来才能判断）。
3. 不要自己读全文、不要自己做去重或合规判断、不要自己列关系。你只负责传参与汇报。
4. `extract_and_save` 会在 100ms 内返回 `{"status":"started","job_id":"..."}`（抽取在后台执行
   1-2 分钟）。拿到 job_id 后必须用 `extract_status(job_id=...)` 轮询，直到 status 为
   `done` 或 `failed`。同一篇文档重复调用会自动复用同一个任务（`reused=true`），不会重复抽取；
   **绝对不要**因为看到 `started` 就再次调用 `extract_and_save`。
5. 汇报必须如实：给出 created / merged / pending / violations / unmatched 的数量；
   `unmatched` 与 `violations` 要列出名称与拒绝理由；`folders_synced` 说明目录是否已重建。
   status=`failed` 时把 error 原文说明，不要自行重试写页。

## 输出格式
一句话说明处理了哪篇文档、用了哪个模型；然后给出结果数字；最后列出被拒绝的项与原因。
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="替换 bodhi 智能体提示词为精简版")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = server.psql_csv(
        "SELECT id, name, config::text AS cfg FROM custom_agents "
        "WHERE id LIKE 'bodhi-ontology-%' AND deleted_at IS NULL ORDER BY id")
    print("== 找到 %d 个 bodhi 智能体，目标提示词 %d 字符 ==" % (len(rows), len(LEAN_PROMPT)))
    statements = []
    for row in rows:
        cfg = json.loads(row["cfg"] or "{}")
        old_len = len(cfg.get("system_prompt") or "")
        cfg["system_prompt"] = LEAN_PROMPT
        # 关键字段保持（MCP 绑定不能丢）
        cfg.setdefault("mcp_selection_mode", "all")
        cfg.setdefault("agent_mode", "smart-reasoning")
        cfg.setdefault("agent_type", "custom")
        print("  %s：提示词 %d → %d 字符" % (row["id"], old_len, len(LEAN_PROMPT)))
        statements.append(
            "UPDATE custom_agents SET config = %s, updated_at = now() WHERE id = %s;"
            % (server.sql_json(cfg), server.sql_str(row["id"])))

    if args.dry_run or not statements:
        print("（%s）" % ("--dry-run，未写入" if args.dry_run else "无改动"))
        return 0
    server.psql("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n", stdin=True)
    print("== 已更新 %d 个智能体 ==" % len(statements))
    for row in server.psql_csv(
            "SELECT id, length(coalesce(config->>'system_prompt','')) AS n, "
            "config->>'mcp_selection_mode' AS mode, "
            "(position('只处理用户在本次对话中明确指定' in coalesce(config->>'system_prompt','')) > 0) AS lean "
            "FROM custom_agents WHERE id LIKE 'bodhi-ontology-%' AND deleted_at IS NULL ORDER BY id"):
        print("  %s：%s 字符  mode=%s  精简版=%s"
              % (row["id"], row["n"], row["mode"], row["lean"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
