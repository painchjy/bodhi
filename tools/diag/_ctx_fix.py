"""给 `ke_context.py` 所有 `FROM wiki_pages WHERE deleted_at IS NULL` 补上"**所属知识库未软删**"过滤。

背景：软删知识库后，其页仍被"同名扫描/上下文统计/概念渲染"计入 → 前端出现
「名称为空的上下文」与「同名页分布在 N 个领域上下文」虚高（N 里含已删库）。

用法：python3 tools/diag/_ctx_fix.py [--apply]
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
P = REPO / "tools" / "ke-core" / "ke_context.py"
APPLY = "--apply" in sys.argv
OLD = "FROM wiki_pages WHERE deleted_at IS NULL"
NEW = ("FROM wiki_pages WHERE deleted_at IS NULL "
       "AND knowledge_base_id IN (SELECT id FROM knowledge_bases WHERE deleted_at IS NULL)")

t = P.read_text(encoding="utf-8")
n = t.count(OLD)
print("命中 %d 处 `%s`" % (n, OLD))
if OLD + " " in t and NEW in t:
    print("已打过补丁（幂等跳过）")
elif n:
    t2 = t.replace(OLD, NEW)
    print("新增 KB 活库过滤：%d 处" % n)
    for i, ln in enumerate(t2.splitlines(), 1):
        if "knowledge_base_id IN (SELECT id FROM knowledge_bases" in ln:
            print("   %d: %s" % (i, ln.strip()[:110]))
    if APPLY:
        P.write_text(t2, encoding="utf-8")
        print("→ 已写入 %s" % P.relative_to(REPO).as_posix())
    else:
        print("**dry-run**（未写）；确认后加 --apply。")
else:
    print("!! 没命中，可能写法已变，请人工看")
