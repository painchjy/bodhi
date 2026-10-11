"""端到端校验：源库（技术方案评审领域模型）vs 迁移测试库（KB 迁移工具自测）。

测试库按**名字**从库里查（不依赖日志），然后调 kb_transfer.cmd_verify。
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "ke-core"))
import kb_transfer  # noqa: E402

SRC = "7efad3eb-762a-4981-9164-cd3b3a226bd4"          # 技术方案评审领域模型
NAME = "迁移测试-技术方案评审领域"

rows = kb_transfer.q_json("SELECT id, name FROM knowledge_bases WHERE name = %s AND deleted_at IS NULL"
                          % kb_transfer.sql_lit(NAME))
if not rows:
    print("未找到测试库：%s（导入可能还没跑完）" % NAME)
    raise SystemExit(2)
dst = rows[0]["id"]
print("测试库 = %s（%s）" % (rows[0]["name"], dst))
rep = kb_transfer.cmd_verify(SRC, dst)
print("\n详细：", {k: v.get("ok") for k, v in rep["detail"].items()})
raise SystemExit(0 if rep["ok"] else 1)
