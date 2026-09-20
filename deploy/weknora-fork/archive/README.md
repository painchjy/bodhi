# deploy/weknora-fork/archive —— 已退役的一次性运维脚本

归档时间：2026-09-20。**归档 ≠ 删除**：脚本仍可直接执行（路径引用基本是绝对路径或 `$BODHI`），
回退方式：`git mv archive/<文件名> ../<文件名>`。

| 文件 | 当时干什么 | 为什么退役 | 现在的对应能力 |
|---|---|---|---|
| `cleanup_auto_wiki.sh` | 清掉上游 pipeline 自动生成的 wiki 页（避免与我们的页/目录冲突），再重建目录树 | 一次性迁移，已执行完毕；上游自动页不会再冒出来 | `POST /bodhi/delete`（硬删，`index` 保护）+ `sync_folders.py` |
| `fix_agent_mcp.py` | 修 agent 配置里的 MCP 服务器指向/工具白名单 | 一次性修复；agent 配置现由生成器统一产出 | `gen_agents.py`（读 `artifacts/prompts/*` 生成 agents.sql / prompt yaml） |

仍在用的同类脚本请留在上级目录：`refresh_ontology_kb.sh`（待改薄封装，见下）、`delete_wiki_pages.sh`（运维兜底）、
`set_agent_prompt_lean.py`（待并入 `gen_agents.py`）。
