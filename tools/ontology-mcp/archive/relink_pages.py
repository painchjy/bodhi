"""把存量页面正文里的关系链接统一成 [[slug|正文]] 格式。

为什么（用户 2026-09-19 实测）
------------------------------
「本体关系」小节此前写成 `- 指导（`bmm:guides`）→ [开发流程](wiki:bmm/businessprocess/开发流程)`。
上游前端 markdown 渲染器把 `wiki:` 当**未知协议** → 退化成纯文本，页面里关系目标看不到链接；
而 `[[wiki]]` 是上游明确支持的站内链接语法（`frontend/src/utils/citationMarkdown.ts`：
「Convert <web/> / <kb/> / [[wiki]] tags into inline citation HTML」）。写入侧已同步改为
`server.rel_line()` 生成新格式，本脚本负责把**已落库**的页面一次性迁移过来。

要点
----
- 只改「关系行」的链接形态，不动其它内容；幂等（已是新格式的页不会被重复处理）；
- **不做版本+1**：这是纯格式迁移，不是内容合并，不应污染版本徽标；
- 迁移后 `server.parse_rel_line()` 新老格式都能解析，「本体图谱」不受影响。

用法：
    python tools/ontology-mcp/relink_pages.py --kb-id <UUID> [--dry-run]
    python tools/ontology-mcp/relink_pages.py --all
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import server  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

# [正文](wiki:slug) → [[slug|正文]]
OLD_LINK = re.compile(r"\[([^\]\n]+?)\]\(wiki:([^)\n]+?)\)")


def convert(text: str) -> tuple[str, int]:
    hits = {"n": 0}

    def _sub(m: re.Match[str]) -> str:
        hits["n"] += 1
        return "[[%s|%s]]" % (m.group(2).strip(), m.group(1))

    return OLD_LINK.sub(_sub, text or ""), hits["n"]


def migrate_kb(kb_id: str, dry_run: bool) -> tuple[int, int]:
    rows = server.psql_csv(
        "SELECT slug, content FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "AND content LIKE '%%](wiki:%%'" % server.sql_str(kb_id))
    statements, pages, links = [], 0, 0
    for row in rows:
        new_content, n = convert(row["content"])
        if n == 0 or new_content == row["content"]:
            continue
        pages += 1
        links += n
        statements.append(
            "UPDATE wiki_pages SET content = %s, updated_at = now() "
            "WHERE knowledge_base_id = %s AND slug = %s;"
            % (server.sql_str(new_content), server.sql_str(kb_id), server.sql_str(row["slug"])))
    print("  知识库 %s：命中 %d 页 / %d 处链接%s" % (kb_id, pages, links, "（dry-run）" if dry_run else ""))
    if dry_run or not statements:
        return pages, links
    server.psql("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n", stdin=True)
    return pages, links


def main() -> int:
    parser = argparse.ArgumentParser(description="把 [正文](wiki:slug) 统一成 [[slug|正文]]")
    parser.add_argument("--kb-id")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.all:
        kbs = [r["knowledge_base_id"] for r in server.psql_csv(
            "SELECT DISTINCT knowledge_base_id FROM wiki_pages "
            "WHERE deleted_at IS NULL AND content LIKE '%%](wiki:%%'")]
    elif args.kb_id:
        kbs = [args.kb_id]
    else:
        parser.error("需要 --kb-id 或 --all")

    print("== 关系链接格式迁移（%d 个知识库%s） ==" % (len(kbs), "，dry-run" if args.dry_run else ""))
    tp = tl = 0
    for kb in kbs:
        p, l = migrate_kb(kb, args.dry_run)
        tp += p
        tl += l
    if not args.dry_run:
        print("== 完成：%d 页 / %d 处链接已迁移 ==" % (tp, tl))
        left = server.psql_csv(
            "SELECT count(*) AS n FROM wiki_pages WHERE deleted_at IS NULL AND content LIKE '%%](wiki:%%'")
        print("  仍含旧格式的页数：%s（应为 0）" % left[0]["n"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
