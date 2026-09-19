"""本体驱动的知识提取 -> 写 Neo4j（v0.1 捷径验证版）。

定位
----
这是「先发布一个可用版本」的最小可用路径：**不依赖 fork 镜像**，直接
    文档 -> 轻量版本体提示词 -> LLM -> 本体约束校验 -> 写 Neo4j
让你能立刻在 http://localhost:7474 看到本体类型的节点与关系（节点按 label 着色、
关系显示类型名并带方向箭头）。

与最终形态的差异（见 docs/weknora-fork.md §9）
--------------------------------------------
- 节点落在独立命名空间（label `BodhiInstance` + `模块__类`），**还不是 wiki 页面**；
  正式版由 fork 把结果写成 wiki 页面 + 本体类型。
- 颜色/图例由 Neo4j Browser 按 label 自动分配；正式版由前端用 ontology_index 的
  `classes[].color` / `legend[]` 渲染。
- 去重：正式版交给 WeKnora 的向量匹配；本脚本只做 `MERGE (key)` 的幂等合并，
  **并允许把同一实体在多份文档里的多次出现汇到同一节点**（多源），
  权威定义标记为 `authoritative=true` 的第一次（`docs/weknora-fork.md` §8.3）。

提示词契约（与 src/services/extraction_service.py 对齐，按新口径裁剪）
-------------------------------------------------------------------
- **不让 LLM 判 create/merge**（用户口径）：输出里没有 `action` / `existing_id`；
- 关系类型只能取自本体（`relations[].name`），并带 domain/range 约束，违规进 `violations`；
- 每条要素/关系必须带 `source_text`（逐字引用）与 `source_span`（在原文中的定位串）；
- 无法归类的内容放进 `unmatched`，不丢弃。

日志
----
与 `src/services/extraction_service.py::_save_log` 同格式：
    logs/ontology_<method>_<yyyymmdd_HHMMSS>.log
含 finish_reason / usage / 全部 messages（role + content）/ LLM 原始返回。

用法
----
    # 只看提示词，不调 LLM（省额度）
    python tools/ontology-extract/extract.py --doc docs/samples/ea-开户流程.md --model ea --dry-run

    # 真跑：调 LLM -> 校验 -> 写 Neo4j
    python tools/ontology-extract/extract.py --doc <你的文档> --model ea

    # 只看结果不写图
    python tools/ontology-extract/extract.py --doc <你的文档> --model bmm --no-write
"""

from __future__ import annotations

import argparse
import base64
import json
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

NEO4J_HTTP = "http://localhost:7474"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "password"
NEO4J_DATABASE = "neo4j"
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


def build_system_prompt(model: dict, light: str) -> str:
    classes = allowed_classes(model)
    relations = allowed_relations(model)
    external = external_class_names(model)
    cls_lines = "\n".join(
        "- %s（%s）：%s%s" % (c["name"], c["label"] or c["name"],
                             c["definition"] or "无定义",
                             "　← 来自依赖模块" if c["name"] in external else "")
        for c in classes
    )
    rel_lines = "\n".join(
        "- %s（%s）：%s → %s"
        % (r["name"], r["label"] or r["name"],
           ",".join(r["domain"]) or "?", ",".join(r["range"]) or "?")
        for r in relations
    )
    body = SYSTEM_TEMPLATE.format(
        expert_role=model["expert_role"],
        model_label=model["label"],
        classes=cls_lines,
        relations=rel_lines,
        example_class=classes[0]["name"] if classes else "bmm:Goal",
        example_relation=relations[0]["name"] if relations else "bmm:realizes",
    )
    return "## 本体模型轻量版\n\n%s\n\n%s" % (light, body)


# ---------------------------------------------------------------------------
# LLM 调用 + 日志（格式与 src/services/extraction_service.py::_save_log 对齐）
# ---------------------------------------------------------------------------
def save_log(method: str, messages: list[dict], raw_response: str,
             finish_reason: str = "", usage=None) -> pathlib.Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOG_DIR / ("ontology_%s_%s.log" % (method, ts))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("=== 提取调试日志 %s ===\n\n" % ts)
        handle.write("finish_reason: %s\n" % finish_reason)
        handle.write("usage: %s\n\n" % usage)
        for i, msg in enumerate(messages):
            handle.write("--- Message[%d] role=%s ---\n" % (i, msg["role"]))
            handle.write(msg["content"])
            handle.write("\n\n")
        handle.write("--- LLM 原始返回 ---\n")
        handle.write(raw_response or "(空)")
        handle.write("\n")
    print("[Bodhi]   📝 LLM调试日志已保存: %s" % path.relative_to(REPO).as_posix())
    return path


def _call_llm_stdlib(cfg: dict, messages: list[dict]) -> tuple[str, str, str]:
    """标准库版本的 OpenAI 兼容调用（WSL 服务里没装 openai 包时的兜底）。

    只用到 chat/completions，足够本项目（提示词 + JSON 输出）。返回
    (文本, finish_reason, usage 文本)。
    """
    body: dict = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg["temperature"],
        "max_tokens": cfg["max_tokens"],
    }
    if cfg.get("json_mode"):
        body["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", "Bearer " + cfg["api_key"])
    with urllib.request.urlopen(request, timeout=600) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise SystemExit("LLM 返回错误：%s" % json.dumps(payload["error"], ensure_ascii=False)[:400])
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    # 有些网关把思维链放在 reasoning_content，正文仍取 content
    raw = (message.get("content") or message.get("reasoning_content") or "")
    finish = choice.get("finish_reason") or ""
    return raw, finish, str(payload.get("usage") or {})


def call_llm(cfg: dict, system: str, user: str, method: str = "extraction") -> tuple[str, dict]:
    """调用 LLM（OpenAI 兼容），返回 (原始文本, 元信息)。

    优先用 `openai` 包；没装则退回标准库实现 —— 因为提取服务要跑在 WSL 里，
    不该被一个 pip 依赖卡住（这个兜底也顺带让整个工具链零依赖）。
    """
    if not cfg.get("api_key"):
        raise SystemExit("缺少 LLM_API_KEY（写在仓库根的 .env 里）")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    started = time.time()
    backend = "openai"
    try:
        from openai import OpenAI

        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=600)
        kwargs = {
            "model": cfg["model"], "messages": messages,
            "temperature": cfg["temperature"], "max_tokens": cfg["max_tokens"],
        }
        if cfg.get("json_mode"):
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise SystemExit("LLM 调用失败：%s" % exc)
        raw = (response.choices[0].message.content or "")
        finish_reason = response.choices[0].finish_reason or ""
        usage = getattr(response, "usage", None)
    except ImportError:
        backend = "stdlib"
        print("[Bodhi]   ℹ 未安装 openai 包，使用标准库 HTTP 调用")
        try:
            raw, finish_reason, usage = _call_llm_stdlib(cfg, messages)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SystemExit("LLM 调用失败（stdlib）：%s" % exc)
    save_log(method, messages, raw, finish_reason, usage)
    print("[Bodhi]   ⏱ LLM 用时 %.1fs，返回 %d 字符，finish_reason=%s（backend=%s）"
          % (time.time() - started, len(raw), finish_reason, backend))
    return raw, {"finish_reason": finish_reason, "usage": str(usage), "backend": backend}


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


def read_raw_from_log(path: pathlib.Path) -> str:
    """从已保存的调试日志里取回 LLM 原始返回（配合 --from-log 复用上一次调用，省额度）。"""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    marker = "--- LLM 原始返回 ---"
    index = text.find(marker)
    if index == -1:
        raise SystemExit("日志里找不到「%s」段：%s" % (marker, path))
    return text[index + len(marker):].strip()


def validate(result: dict, model: dict, doc_name: str) -> dict:
    """本体约束校验：类型白名单 + 关系 domain/range。违规不写图，进 violations。"""
    class_by_name = {c["name"]: c for c in allowed_classes(model)}
    rel_by_name = {r["name"]: r for r in allowed_relations(model)}
    nodes, edges, violations = [], [], []

    for element in result.get("elements") or []:
        type_name = (element.get("type") or "").strip()
        name = (element.get("name") or "").strip()
        if type_name not in class_by_name:
            violations.append({"kind": "unknown_class", "value": type_name, "name": name})
            continue
        if not name:
            violations.append({"kind": "missing_name", "value": type_name, "name": ""})
            continue
        nodes.append({
            "name": name,
            "type": type_name,
            "type_label": class_by_name[type_name].get("label") or type_name,
            "module": model["key"],
            "definition": (element.get("definition") or "").strip(),
            "description": (element.get("description") or "").strip(),
            "source_text": (element.get("source_text") or "").strip(),
            "source_span": (element.get("source_span") or "").strip(),
            "doc": doc_name,
        })

    known = {n["name"] for n in nodes} | {e["name"] for e in (result.get("existing_used") or [])}
    for relation in result.get("relationships") or []:
        type_name = (relation.get("type") or "").strip()
        src = (relation.get("source_element") or "").strip()
        dst = (relation.get("target_element") or "").strip()
        spec = rel_by_name.get(type_name)
        if spec is None:
            violations.append({"kind": "unknown_relation", "value": type_name,
                               "name": "%s->%s" % (src, dst)})
            continue
        if not src or not dst:
            violations.append({"kind": "missing_endpoint", "value": type_name, "name": ""})
            continue
        edges.append({
            "type": type_name,
            "label": spec.get("label") or type_name,
            "source": src,
            "target": dst,
            "source_text": (relation.get("source_text") or "").strip(),
            "source_span": (relation.get("source_span") or "").strip(),
            "doc": doc_name,
        })

    return {
        "nodes": nodes,
        "edges": edges,
        "violations": violations,
        "unmatched": result.get("unmatched") or [],
        "notes": result.get("notes") or "",
        "unknown_targets": sorted({e["source"] for e in edges if e["source"] not in known}
                                  | {e["target"] for e in edges if e["target"] not in known}),
    }

def build_user_prompt(doc_name: str, text: str, existing: list[dict]) -> str:
    if existing:
        lines = ["## 已有要素（同名或同义时，关系请直接引用它们的 name；不要重复新建）"]
        for item in existing[:200]:
            lines.append("- [%s] %s | %s" % (item["type"], item["name"], (item.get("definition") or "")[:200]))
        existing_block = "\n".join(lines)
    else:
        existing_block = "## 已有要素\n（无，所有识别均为新建）"
    return existing_block + """

## 文档：%s
<content>
%s
</content>
""" % (doc_name, text)


# ---------------------------------------------------------------------------
# Neo4j 写入（HTTP API，零驱动依赖）+ 存量读取
# ---------------------------------------------------------------------------
def neo4j_commit(statements: list[dict]) -> dict:
    """调用 Neo4j 的 HTTP 事务端点执行一批 Cypher。"""
    payload = json.dumps({"statements": statements}).encode("utf-8")
    request = urllib.request.Request(
        "%s/db/%s/tx/commit" % (NEO4J_HTTP, NEO4J_DATABASE), data=payload, method="POST")
    token = base64.b64encode(("%s:%s" % (NEO4J_USER, NEO4J_PASSWORD)).encode("utf-8")).decode("ascii")
    request.add_header("Authorization", "Basic " + token)
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        raise SystemExit("Neo4j 返回 %s：%s" % (exc.code, detail))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("连不上 Neo4j（%s）：%s（先用 docker ps 确认 WeKnora-neo4j 在跑）"
                         % (NEO4J_HTTP, exc))
    if body.get("errors"):
        raise SystemExit("Cypher 执行失败：%s" % json.dumps(body["errors"], ensure_ascii=False)[:600])
    return body


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


def write_graph(model: dict, nodes: list[dict], edges: list[dict], kb: str) -> dict:
    """写实例层：节点 label = BodhiInstance + 模块__类；边 = 关系本地名（大写）。"""
    node_statements, edge_statements = [], []
    for node in nodes:
        # 节点 label 用「类型前缀__类名」（前缀取自节点自身的 type，这样跨模块引用的
        # 外部类（如 bmm:DesiredResult）也能得到正确的 label）
        label = "%s__%s" % (node["type"].split(":", 1)[0], node["type"].split(":", 1)[-1])
        node_statements.append({
            "statement": (
                "MERGE (n:%s:%s {key: $key}) "
                "ON CREATE SET n.created_at = timestamp(), n.authoritative = true "
                "SET n.name = $name, n.type = $type, n.type_label = $type_label, "
                "    n.module = $module, n.kb = $kb, n.definition = $definition, "
                "    n.source_doc = $doc, n.source_text = $source_text, "
                "    n.updated_at = timestamp() "
                "RETURN n.key"
            ) % (INSTANCE_LABEL, label),
            "parameters": {
                "key": node_key(node), "name": node["name"], "type": node["type"],
                "type_label": node["type_label"], "module": node["module"], "kb": kb,
                "definition": node["definition"], "doc": node["doc"],
                "source_text": node["source_text"],
            },
        })
    for edge in edges:
        edge_statements.append({
            "statement": (
                "MATCH (a:%s {key: $sk}), (b:%s {key: $tk}) "
                "MERGE (a)-[r:%s {key: $rkey}]->(b) "
                "SET r.label = $label, r.type = $type, r.source_doc = $doc, "
                "    r.source_text = $source_text, r.updated_at = timestamp() "
                "RETURN type(r)"
            ) % (INSTANCE_LABEL, INSTANCE_LABEL, rel_label(edge["type"])),
            "parameters": {
                "sk": "%s|%s" % (_type_of(edges, nodes, edge["source"]), edge["source"]),
                "tk": "%s|%s" % (_type_of(edges, nodes, edge["target"]), edge["target"]),
                "rkey": "%s|%s|%s" % (edge["type"], edge["source"], edge["target"]),
                "label": edge["label"], "type": edge["type"], "doc": edge["doc"],
                "source_text": edge["source_text"],
            },
        })
    result = neo4j_commit(node_statements + edge_statements)
    written_nodes = sum(1 for s in result.get("results", [])[:len(node_statements)] if s.get("data"))
    return {"nodes_written": written_nodes, "edges_attempted": len(edge_statements)}


def _type_of(edges: list[dict], nodes: list[dict], name: str) -> str:
    """关系端点要素的类型：先查本次新建节点，再查图里已有节点（存量关系）。"""
    for node in nodes:
        if node["name"] == name:
            return node["type"]
    return lookup_existing_type(name)


def lookup_existing_type(name: str) -> str:
    body = neo4j_commit([{
        "statement": "MATCH (n:%s {name: $name}) RETURN n.type AS type LIMIT 1" % INSTANCE_LABEL,
        "parameters": {"name": name},
    }])
    rows = body["results"][0]["data"] if body.get("results") else []
    if rows:
        return rows[0]["row"][0]
    raise SystemExit("关系端点 %r 既不在本次结果里，也不在图里（可能是 LLM 用了未定义的 name）" % name)


def fetch_existing(model: dict, limit: int = 200) -> list[dict]:
    """取该模块的存量要素（供提示词注入，见 docs/weknora-fork.md §8.2）。"""
    body = neo4j_commit([{
        "statement": (
            "MATCH (n:%s) WHERE n.module = $module "
            "RETURN n.type AS type, n.name AS name, n.definition AS definition LIMIT $limit"
        ) % INSTANCE_LABEL,
        "parameters": {"module": model["key"], "limit": limit},
    }])
    rows = body["results"][0]["data"] if body.get("results") else []
    return [{"type": r["row"][0], "name": r["row"][1], "definition": r["row"][2]} for r in rows]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="本体驱动的知识提取（校验后写 Neo4j）")
    parser.add_argument("--doc", required=True, help="待抽取的文档（.md/.txt）")
    parser.add_argument("--model", default="ea", help="本体模型 key（当前可选：bmm / ea）")
    parser.add_argument("--kb", default="bodhi-poc", help="写入图里的知识库标记（便于区分批次）")
    parser.add_argument("--dry-run", action="store_true", help="只导出提示词，不调 LLM（省额度）")
    parser.add_argument("--no-write", action="store_true", help="调 LLM 但不写 Neo4j")
    parser.add_argument("--max-chars", type=int, default=24000, help="文档截断长度（默认 24000）")
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="本体目录产物路径")
    parser.add_argument("--env-file", default=str(REPO / ".env"), help="LLM 配置所在 .env")
    parser.add_argument("--from-log", default="", help="复用某份调试日志里的 LLM 返回，跳过本次调用（省额度）")
    args = parser.parse_args()

    doc_path = pathlib.Path(args.doc)
    if not doc_path.is_file():
        raise SystemExit("文档不存在：%s" % doc_path)
    text = doc_path.read_text(encoding="utf-8")
    if len(text) > args.max_chars:
        print("[Bodhi]   ⚠ 文档 %d 字符，先截断到 %d（正式版按 chunk 分片）"
              % (len(text), args.max_chars))
        text = text[: args.max_chars]

    index = load_index(pathlib.Path(args.index))
    model = pick_model(index, args.model)
    light = load_light(model, DEFAULT_PROMPTS)
    system = build_system_prompt(model, light)

    env = load_env(pathlib.Path(args.env_file))
    existing: list[dict] = []
    if not args.dry_run:
        try:
            existing = fetch_existing(model)
            print("[Bodhi]   存量要素 %d 条（已注入提示词）" % len(existing))
        except SystemExit as exc:
            print("[Bodhi]   ⚠ 取存量失败（%s），按无存量继续" % exc)
    user = build_user_prompt(doc_path.name, text, existing)

    print("[Bodhi]   模型=%s（%s）｜专家角色=%s" % (model["key"], model["label"], model["expert_role"]))
    print("[Bodhi]   可用类型 %d 个 / 可用关系 %d 条"
          % (len(allowed_classes(model)), len(allowed_relations(model))))
    print("[Bodhi]   system %d 字符（其中轻量版 %d）/ user %d 字符"
          % (len(system), len(light), len(user)))

    if args.dry_run:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        out = LOG_DIR / "last_prompt_preview.md"
        out.write_text("# system\n\n%s\n\n# user\n\n%s\n" % (system, user), encoding="utf-8")
        print("[Bodhi]   ✅ dry-run：提示词已写到 %s（未调用 LLM）"
              % out.relative_to(REPO).as_posix())
        return 0

    cfg = {
        "api_key": env.get("LLM_API_KEY", ""),
        "base_url": env.get("LLM_BASE_URL", "https://api.deepseek.com"),
        "model": env.get("LLM_MODEL", "deepseek-flash"),
        "temperature": float(env.get("LLM_TEMPERATURE", "0.1")),
        "max_tokens": int(env.get("LLM_MAX_TOKENS", "32768")),
        "json_mode": env.get("LLM_JSON_MODE", "1") == "1",
    }
    if args.from_log:
        raw = read_raw_from_log(pathlib.Path(args.from_log))
        print("[Bodhi]   ♻ 复用日志里的 LLM 返回（%d 字符），本次未调用 LLM" % len(raw))
    else:
        raw, _meta = call_llm(cfg, system, user)
    result = parse_json(raw)
    checked = validate(result, model, doc_path.name)

    print("[Bodhi]   解析：要素 %d / 关系 %d / 违规 %d / unmatched %d"
          % (len(checked["nodes"]), len(checked["edges"]),
             len(checked["violations"]), len(checked["unmatched"])))
    for violation in checked["violations"][:10]:
        print("           ⚠ 违规 %s: %s（%s）"
              % (violation["kind"], violation["value"], violation["name"]))
    if checked["unknown_targets"]:
        print("           ⚠ 关系端点未定义：%s" % ", ".join(checked["unknown_targets"][:10]))
    if checked["unmatched"]:
        print("           · unmatched（未归类）前 5 条：")
        for item in checked["unmatched"][:5]:
            print("             - %s（%s）" % (item.get("name"), item.get("reason")))

    if args.no_write:
        print("[Bodhi]   --no-write：跳过写库")
        return 0

    stats = write_graph(model, checked["nodes"], checked["edges"], args.kb)
    print("[Bodhi]   ✅ 已写 Neo4j：节点 %d 个 / 关系 %d 条（kb=%s）"
          % (stats["nodes_written"], stats["edges_attempted"], args.kb))
    print("[Bodhi]   在 http://localhost:7474 里粘贴下面任一条查看：")
    print("             MATCH (n:BodhiInstance) WHERE n.module = '%s' RETURN n LIMIT 100" % model["key"])
    print("             MATCH (a:BodhiInstance)-[r]->(b:BodhiInstance) RETURN a, r, b LIMIT 200")
    print("             MATCH (n:BodhiInstance) RETURN n.type AS 类型, count(*) AS 数量 ORDER BY 数量 DESC")
    return 0


if __name__ == "__main__":
    sys.exit(main())


