"""ke-core · 结构化数据**批量建模**（probe / plan / apply / state）。

用户口径（2026-09-29）
--------------------
- 智能体**接受自然语言描述的 Excel 结构**，把它对应到本体的**类 / 关系 / 数据属性**；
- **约定：一次批量建模只包含「一个类（含其数据属性）」或「一条关系」** —— 因此同一份文件
  **多扫几遍**就能把所有类与关系建完（`import_state` 报还差哪些，收敛即完成）；
- 不逐行确认：`plan` 出**一次**完整影响面 + `ticket`，用户同意后 `apply` 整批写。

四个动作
--------
    probe(file)                     只读：sheet / 表头 / 行数 / 抽样 / 重复表头 / 列前缀（复用 ke_sheet）
    plan(kind, target, file, kb_id, …)  只读：校验本体面 + 影响面 + 风险 + ticket（写进账本）
    apply(ticket, ack, …)           写：**只写这一个 target**（500 行/事务；幂等；末了重算 in_links）
    state(batch)                    账本：每个 target 的状态与 remaining（智能体靠它循环到收敛）

关键不变量
----------
1. **一次一个目标**：`kind=class` 的 target 是一个类；`kind=relation` 的 target 是一条关系。多个 → 拒。
2. **两段式**：`plan` 出 `ticket`（= 文件 sha256 + target + 映射 + 行数 的指纹）；`apply` 必须带同一 ticket。
3. **幂等**：页面 `last_edit_source='bodhi-import:<batch>:<target>'`；内容哈希未变 → **零写入**；
   变化 → 快照(`wiki_page_revisions`) + `version+1`；`prune=true` 软删"源里已消失"的本批页。
4. **巡检干净**：每页带 `page_type`（本体类）+「## 原文依据」逐字原文 + `page_metadata.ontology.attributes`
   + `page_metadata.import{file_sha256,sheet,row}`；关系批次把 `- 标签（关系名）→ [[slug|标题]]` 追加到
   **domain 侧页**的「## 本体关系」，再全库重算 `in_links`。
5. **写权限**：`ke_db.assert_can_write(kb_id, tenant)`（与其它写路径同一守门）。
6. **零第三方依赖**：读表走 `ke_sheet`（标准库 zipfile+xml）。

账本：`state/import/<batch>.json`（含每个 target 的 plan 快照、ticket、结果），可人工审阅/回滚。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import ke_db  # noqa: E402
import ke_ontology  # noqa: E402
import ke_pages  # noqa: E402
import ke_sheet  # noqa: E402

STATE_DIR = REPO / "state" / "import"
TAG = "bodhi-import"                     # last_edit_source 前缀
CHUNK = 500                              # 每事务行数
REL_SECTION = "## 本体关系"
EVIDENCE_SECTION = "## 原文依据"
AUTH_SECTION = "## 属性"
DEF_SECTION = "## 定义"

# 本体类 → 正文里「属性表」的列顺序（缺省按 mapping 顺序）
SLUG_SAFE = re.compile(r"[\\/\s]+")


def _sha256_file(path: str | pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _slugify(text: str) -> str:
    """键值 → slug 片段：去首尾空白、把 `/` 与空白压成 `-`，保留中文。"""
    return SLUG_SAFE.sub("-", (text or "").strip()).strip("-")


def _norm_key(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")).strip().lower()


def class_slug_prefix(page_type: str) -> str:
    """`bmm:MainSystem` → `bmm/mainsystem`（实例页三段式的前两段）。"""
    module, _, local = (page_type or "").partition(":")
    return "%s/%s" % (module, local.lower())


def page_slug(page_type: str, key: str) -> str:
    return "%s/%s" % (class_slug_prefix(page_type), _slugify(key))


def _rows_as_dicts(sheet_rows: list[list[str]], header: list[str]) -> list[dict]:
    """二维行 → [{列名: 值, "__row__": 原始行号}]；跳过多余/全空行；重复表头**保留第一列**并记 `__dup__`。"""
    out = []
    for idx, row in enumerate(sheet_rows, start=2):      # 表头占第 1 行
        if not any((c or "").strip() for c in row):
            continue
        item: dict = {"__row__": idx, "__raw__": list(row)}
        for ci, col in enumerate(header):
            value = (row[ci] if ci < len(row) else "") or ""
            if col in item:
                continue                                  # 重复表头：只取第一个（第二个进 __raw__）
            item[col] = value.strip()
        out.append(item)
    return out
