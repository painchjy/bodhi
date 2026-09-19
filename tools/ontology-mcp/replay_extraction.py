"""用已保存的抽取结果离线复放写入路径（不调 LLM、不过 MCP 传输）。

为什么要它
----------
2026-09-19 实测：智能体调用 `extract_and_save` 时
  1) app 侧 MCP 客户端**硬编码 60 秒超时**（无配置项），而单次抽取的 LLM 就要 ~57s
     → 同步调用必然超时；
  2) 写入侧还会撞 `wiki_pages_pkey`（确定性 UUIDv5 撞上被软删除的旧页）。
为了先把**数据管道**验证清楚（合规校验 / 两级 category_path / 关系行 [[slug|正文]] /
图谱 edges），这里拿已经落盘的抽取结果直接复放写入路径：把 `run_extraction` 换成
「读文件」，其余（合并/新增/待确认/写库）完全走真实代码。

用法：
    python tools/ontology-mcp/replay_extraction.py [--log <文件>] [--kb 企业知识库]
        [--knowledge d1] [--model bmm] [--dry-run]
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import server  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def newest_log() -> pathlib.Path:
    files = sorted(glob.glob(str(REPO / "logs" / "ontology_mcp_extraction_*.log")),
                   key=lambda p: pathlib.Path(p).stat().st_mtime)
    if not files:
        raise SystemExit("找不到 logs/ontology_mcp_extraction_*.log")
    return pathlib.Path(files[-1])


def load_payload(path: pathlib.Path) -> dict:
    """从调试日志里取出 LLM 返回的 JSON（兼容整文件 JSON / ```json 代码块 / 末尾对象）。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S):
        try:
            return json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            continue
    for start in [m.start() for m in re.finditer(r"\{", text)][::-1]:
        chunk = text[start:]
        dec = json.JSONDecoder()
        try:
            obj, _ = dec.raw_decode(chunk)
            if isinstance(obj, dict):
                return obj
        except Exception:  # noqa: BLE001
            continue
    raise SystemExit("无法从 %s 解析出 JSON 抽取结果" % path.name)


def main() -> int:
    ap = argparse.ArgumentParser(description="离线复放抽取结果的写入路径")
    ap.add_argument("--log")
    ap.add_argument("--kb", default="企业知识库", help="知识库名称或 UUID（验证在线解析）")
    ap.add_argument("--knowledge", default="d1", help="文档 id/标题片段（验证占位符解析）")
    ap.add_argument("--model", default="bmm")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = pathlib.Path(args.log) if args.log else newest_log()
    payload = load_payload(path)
    keys = list(payload.keys())[:8]
    print("== 复放来源 ==")
    print("  文件: %s（%d 字节）" % (path.name, path.stat().st_size))
    print("  顶层键: %s" % keys)

    # 正解：引擎自带「从调试日志复放原始 LLM 输出」的能力（run_extraction 的 from_log 分支），
    # extract_and_save 已把 from_log 透传下去 → 仍会重跑本体合规校验与全部写入逻辑，
    # 只是不发起 LLM 调用（既不烧 token，也不受 app 侧 60s 超时影响）。
    print("\n== 走真实写入路径（extract_and_save + from_log） ==")
    result = server.extract_and_save(args.model, args.kb, args.knowledge,
                                     dry_run=args.dry_run, from_log=str(path))
    brief = {k: result.get(k) for k in
             ("model", "kb_id", "knowledge_id", "resolved_note", "doc_title",
              "chunks", "chars", "elements", "relationships", "dry_run")}
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    for key in ("created", "merged", "pending", "violations", "unmatched"):
        items = result.get(key) or []
        print("  %-11s %d" % (key, len(items)))
        for item in items[:3]:
            print("      %s" % json.dumps(item, ensure_ascii=False)[:160])
    return 0


if __name__ == "__main__":
    sys.exit(main())
