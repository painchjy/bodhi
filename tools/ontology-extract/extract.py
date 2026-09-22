"""本体索引 / 模型 / 合规校验**库**（原「本体知识提取 CLI」的常驻部分）。

2026-09-22：LLM 调用链与 CLI 抽取入口**已回收**（`call_llm` / `_call_llm_stdlib` /
`build_system_prompt` / `build_user_prompt` / `save_log` / `write_graph` / `main` →
`archive/llm_cli_retired_2026-09-22.py.txt`）：抽取改由智能体按 `domain_modeling` 技能
分批完成，MCP 侧不再持有 LLM 凭据、不再需要 `openai` 依赖。

本模块现役职责（供 `ontology-mcp/server.py` 使用）
-------------------------------------------
- `load_index()` / `pick_model()` / `load_light()`：读 `artifacts/weknora/` 里的
  模型索引与轻量版本体提示词；
- `validate()`：类型白名单 + domain→range 合规校验（不合规进 `violations`）；
- `parse_json()`：LLM/智能体给的 JSON 容错解析（现主要供落库路径复用）；
- `allowed_classes()` / `allowed_relations()` / `external_class_names()`：类型面查询。

历史定位（已退役，仅作脉络）
--------------------------
原为「不依赖 fork 镜像」的最小可用路径：文档 → 轻量版本体提示词 → LLM → 本体约束校验 →
写 Neo4j；提示词契约（不让 LLM 判 create/merge、关系类型取自本体、每条要素带
`source_text`/`source_span`、无法归类进 `unmatched`）仍然适用于智能体侧的抽取。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

REPO = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_INDEX = REPO / "artifacts" / "weknora" / "ontology_index.json"
DEFAULT_PROMPTS = REPO / "artifacts" / "prompts"
LOG_DIR = REPO / "logs"

# Windows 控制台默认 GBK，含 emoji / 生僻字的输出会直接抛 UnicodeEncodeError。
# 这里把标准输出切到 UTF-8（无法编码的字符退化为 ?，不再中断流程）。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

NEO4J_HTTP = os.environ.get("BODHI_NEO4J_HTTP", "http://localhost:7474")
NEO4J_USER = os.environ.get("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")   # 口令不内置（Neo4j 为可选件）
NEO4J_DATABASE = os.environ.get("BODHI_NEO4J_DB", "neo4j")
INSTANCE_LABEL = "BodhiInstance"


# ---------------------------------------------------------------------------
# 配置与本体目录
# ---------------------------------------------------------------------------
def load_env(path: pathlib.Path) -> dict:
    """读 .env（只认 KEY=VALUE，忽略注释）；不覆盖已存在的环境变量。"""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def load_index(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def pick_model(index: dict, key: str) -> dict:
    for model in index["models"]:
        if model["key"] == key:
            return model
    raise SystemExit("本体目录里没有模块 %r（可选：%s）"
                     % (key, ", ".join(m["key"] for m in index["models"])))


def load_light(model: dict, prompts_dir: pathlib.Path) -> str:
    """轻量版正文：优先取产物（artifacts/prompts/<key>_light.md），退回真源 md。"""
    if model.get("light_available"):
        artifact = REPO / model["light_prompt"]
        if artifact.is_file():
            return artifact.read_text(encoding="utf-8")
        source = REPO / model["light_source"]
        if source.is_file():
            return source.read_text(encoding="utf-8")
    raise SystemExit("模块 %s 没有轻量版（见 docs/weknora-fork.md §8.5）" % model["key"])


def allowed_classes(model: dict) -> list[dict]:
    """可用的抽取类型 = 本模块可实例化的类 + **跨模块引用的外部类**。

    外部类必须一起放行：本模块的跨层关系（如 ea:activityAchievesDesiredResult）
    range 就指向 bmm:DesiredResult，若不放行，LLM 识别出的正确术语会被误判为违规。
    见 docs/weknora-fork.md §8.2 与 ontology/EA轻量版.md §七。
    """
    own = [c for c in model["classes"] if not c.get("is_enum")]
    referenced = (model.get("referenced") or {}).get("classes") or []
    extra = [c for c in referenced if not c.get("is_enum") and c.get("name") not in {o["name"] for o in own}]
    return own + extra


def external_class_names(model: dict) -> set[str]:
    """外部（依赖模块）类名集合，用于提示词里标注「来自依赖模块」。"""
    referenced = (model.get("referenced") or {}).get("classes") or []
    return {c["name"] for c in referenced}


def allowed_relations(model: dict) -> list[dict]:
    return list(model["relations"]) + list(model.get("cross_module_bridges") or [])


# ---------------------------------------------------------------------------
# 提示词（system = 专家角色 + 轻量版本体 + 契约；user = 文档 + 存量知识）
# ---------------------------------------------------------------------------
SYSTEM_TEMPLATE = """你是一个{expert_role}。

以上为 {model_label} 本体模型（轻量版）的定义与取值。

## 任务
从用户提供的文档片段中，识别本体的**要素（elements）**与**关系（relationships）**。

## 硬约束
1. 类型只能取自下方「可用类型」，**禁止自造**：
{classes}
2. 关系类型只能取自下方「可用关系」，且必须满足其方向（domain → range）：
{relations}
3. 每条要素与关系都要给出 `source_text`：**逐字**引用文档中的原文片段（不得改写、不得拼接）；
   并给出 `source_span`：该片段在文档中首次出现的**前 12 个字**（用于定位）。
4. 文档里出现但无法归入上述类型的重要内容，放进 `unmatched`（name + reason + source_text），不要丢弃。
5. 只输出 JSON，不要输出解释、不要 Markdown 代码块。

## 输出格式
{{
  "elements": [
    {{
      "type": "{example_class}",
      "name": "要素名称（用文档里的说法）",
      "definition": "一句话定义（尽量用文档原话概括）",
      "description": "判定为该类型的理由",
      "source_text": "逐字引用的原文片段",
      "source_span": "该片段前 12 个字",
      "attributes": {{}}
    }}
  ],
  "relationships": [
    {{
      "type": "{example_relation}",
      "source_element": "起点要素的 name（必须在 elements 里，或引用「已有要素」的 name）",
      "target_element": "终点要素的 name",
      "source_text": "逐字引用的原文片段",
      "source_span": "该片段前 12 个字"
    }}
  ],
  "unmatched": [{{"name": "…", "reason": "…", "source_text": "…"}}],
  "notes": "整体说明（可空）"
}}
"""


# ---------------------------------------------------------------------------
# 解析与本体校验
# ---------------------------------------------------------------------------
def parse_json(raw: str) -> dict:
    """稳妥解析：去代码围栏 -> 取第一个 { 到最后一个 }。"""
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise SystemExit("LLM 返回里找不到 JSON：%s" % text[:200])
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise SystemExit("JSON 解析失败：%s\n原文片段：%s" % (exc, text[start:start + 300]))


def node_key(node: dict) -> str:
    return "%s|%s" % (node["type"], node["name"])


def rel_label(type_name: str) -> str:
    """关系类型名 -> Cypher 关系类型。

    Neo4j 关系类型不能带前缀冒号，也不能含小写驼峰可读性差，所以转成 UPPER_SNAKE：
        ea:activityHasTask -> ACTIVITY_HAS_TASK
    （中文 label 仍然存在关系的 `label` 属性里，供前端/图例显示。）
    """
    local = type_name.split(":", 1)[-1]
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", local)
    return re.sub(r"[^A-Za-z0-9_]", "_", snake).upper()


if __name__ == "__main__":
    print("[bodhi] 抽取 CLI 已于 2026-09-22 退役：请改用 domain_modeling 技能的"
          "分批流程（MCP 工具 doc_outline / save_knowledge / extract_state）。",
          file=sys.stderr)
    sys.exit(2)


