"""由本体产物生成 WeKnora「挂载用」配置：agent_system_prompt.yaml + agent_type_presets.yaml

为什么这样做（A1 的零重建路径，见 docs/weknora-fork.md §10.11）
------------------------------------------------------------------
- app 容器启动时从 `/app/config/` **读文件**加载 prompt 模板与 agent-type preset（非 go:embed）；
- `config.yaml` 已经被 compose 挂载，我们再加两个挂载即可让 preset/提示词生效，
  **不需要改 Go、不需要重建 app 镜像**；
- `loadPromptTemplates` 只认**固定文件名**，所以新模板必须追加进 `agent_system_prompt.yaml`
  的 `templates:` 列表（新增 yaml 文件不会被读取）。

生成规则
--------
`config/<文件>` = `baseline/<文件>` + 末尾标记块（本脚本生成，可重复生成/幂等）。

用法
----
    python deploy/weknora-fork/gen_agent_config.py            # 生成到 deploy/weknora-fork/config/
    python deploy/weknora-fork/gen_agent_config.py --check    # 只检查是否与 baseline+产物一致
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASELINE = HERE / "baseline"
OUT = HERE / "config"
INDEX_PATH = REPO / "artifacts" / "weknora" / "ontology_index.json"

BEGIN = "  # ==== bodhi2 ontology extraction (generated) BEGIN ===="
END = "  # ==== bodhi2 ontology extraction (generated) END ===="
TOOL_TAG = "bodhi2-gen"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 加载本体产物
# ---------------------------------------------------------------------------
def load_models() -> list[dict]:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    models = [m for m in index["models"] if m.get("light_available")]
    if not models:
        raise SystemExit("本体目录里没有可用的轻量版模块")
    return models


def load_light(model: dict) -> str:
    artifact = REPO / model["light_prompt"]
    if artifact.is_file():
        return artifact.read_text(encoding="utf-8").strip()
    source = REPO / model["light_source"]
    if source.is_file():
        return source.read_text(encoding="utf-8").strip()
    raise SystemExit("模块 %s 没有轻量版正文" % model["key"])


def class_lines(model: dict) -> list[str]:
    out = []
    for item in model["classes"]:
        external = "（依赖模块）" if item.get("external") else ""
        label = item.get("label") or item["name"]
        definition = (item.get("definition") or "").strip()
        line = ("- `%s`（%s）%s %s" % (item["name"], label, external, definition)).rstrip()
        out.append(line)
    return out


def relation_lines(model: dict) -> list[str]:
    out = []
    for rel in list(model["relations"]) + list(model.get("cross_module_bridges") or []):
        domain = ",".join(rel.get("domain") or []) or "?"
        rng = ",".join(rel.get("range") or []) or "?"
        label = rel.get("label") or rel["name"]
        out.append("- `%s`（%s）：%s → %s" % (rel["name"], label, domain, rng))
    return out


# ---------------------------------------------------------------------------
# 系统提示词正文（= 对话式本体提取的"作业指导书"）
# ---------------------------------------------------------------------------
PROMPT_TEMPLATE = """### 角色
你是{expert_role}。

### 任务
用户在对话里会指定**文档或关键词**。你要按「{model_label}」本体，从知识库的**真实片段**中识别
要素（elements）与关系（relationships），并为**每一个要素创建一个 wiki 页面**。

### 本体模型（{model_label} 轻量版）
{light}

### 可用类型（共 {n_classes} 个，page_type 只能取这些）
{classes}

### 可用关系（共 {n_relations} 条，注意方向 domain → range）
{relations}

### 工作流程（必须按顺序执行，不得跳步）
1. **先取片段**：
   - 点名了文档 → 用 `get_document_info` 确认文档，再用 `list_knowledge_chunks` 读取片段全文；
   - 只给了关键词/主题 → 用 `grep_chunks` 检索，再读片段全文。
   - 只依据**真实读到的片段**抽取；禁止凭记忆或常识补充内容。
2. **抽取要素与关系**：每条都要带 `source_text`——**逐字**引用片段原文（不得改写、不得拼接），
   并记下它来自哪个知识（knowledge_id）与哪个片段（chunk_id）。
3. **写页面**：每个要素调用一次 `wiki_write_page`：
   - `slug`：`{model_key}/<类的中文名>/<要素名称>`，例如 `{model_key}/目标/逐步提升落标覆盖率`
     （只允许小写字母、数字、`-`、`/` 和中文；空格换成 `-`；不能以 `/` 开头或结尾）；
   - `title`：要素名称（用文档里的说法）；
   - `page_type`：**必须逐字取自「可用类型」**，形如 `bmm:Goal`（这是本体类型，
     **不要用 entity / concept**）；
   - `summary`：一句话定义；
   - `content`：Markdown，固定包含：`# 名称` → 定义 → `## 判定依据` → `## 原文依据`
     （引用 `source_text`）→ `## 本体关系`（用 `[对方名称](wiki:<对方 slug>)` 写链接）；
   - `source_refs`：`[<knowledge_id>]`；`chunk_refs`：`[<chunk_id>]`。
   - 页面已存在时**更新**它（先用 `wiki_read_page` 查同 slug），不要重复创建。
4. **关系要双向落页**：A 通过某关系指向 B 时，A 页的「## 本体关系」写 `[B](wiki:B的slug)`，
   B 页要补一条指回 A —— wiki 图谱的方向与连线来自页面链接，缺一边就断链。
5. **汇报**：最后用一段话给出：要素数、关系数（按类型分组）、无法归类的项，以及写入/更新的页面清单。

### 硬约束
- **类型与关系只能取自上面的枚举，禁止自造**；不满足 domain → range 的关系不要写出来；
- 文档里出现但归不进本体的内容**不要硬塞**：列进「未归类」并给出理由；
- `source_text` 必须逐字引用；找不到逐字证据的要素就不要产出；
- 不要输出 JSON，也不要贴大段原文——用工具把结果**落成 wiki 页面**，然后汇报。
"""


def build_prompt_content(model: dict, light: str) -> str:
    classes = class_lines(model)
    relations = relation_lines(model)
    return PROMPT_TEMPLATE.format(
        expert_role=model["expert_role"],
        model_label=model["label"],
        model_key=model["key"],
        light=light,
        classes="\n".join(classes),
        relations="\n".join(relations),
        n_classes=len(classes),
        n_relations=len(relations),
    ).strip()


# ---------------------------------------------------------------------------
# YAML 片段生成（模板 + preset）
# ---------------------------------------------------------------------------
def yaml_literal(text: str, indent: int) -> str:
    pad = " " * indent
    return "\n".join(pad + line if line.strip() else "" for line in text.split("\n"))


def template_entry(model: dict, content: str) -> str:
    key, label = model["key"], model["label"]
    return "\n".join([
        '  - id: "ontology_extract_agent_%s"' % key,
        '    name: "Ontology Extraction (%s)"' % label,
        '    description: "Extract ontology-typed elements from KB chunks and persist them as wiki pages."',
        "    i18n:",
        "      default:",
        '        name: "Ontology Extraction (%s)"' % label,
        '        description: "Ontology-driven extraction: read KB chunks, then write one wiki page per element."',
        "      zh-CN:",
        '        name: "本体知识提取（%s）"' % label,
        '        description: "按本体模型从知识库片段抽取要素与关系，并写成本体类型的 wiki 页面。"',
        '    mode: "rag"',
        "    content: |",
        yaml_literal(content, 6),
    ])


PRESET_TOOLS = [
    "grep_chunks",
    "list_knowledge_chunks",
    "get_document_info",
    "wiki_search",
    "wiki_read_page",
    "wiki_write_page",
    "todo_write",
    "thinking",
]


def preset_entry(model: dict) -> str:
    key, label = model["key"], model["label"]
    lines = [
        '  - id: "ontology-extract-%s"' % key,
        "    i18n:",
        "      default:",
        '        label: "Ontology Extraction (%s)"' % label,
        '        description: "Conversational ontology extraction: reads KB chunks, writes one ontology-typed wiki page per element."',
        "      zh-CN:",
        '        label: "本体知识提取（%s）"' % label,
        '        description: "对话式本体提取：读取知识库片段，按本体类型为每个要素写一张 wiki 页面（含逐字原文依据与本体关系）。"',
        "      zh-TW:",
        '        label: "本體知識提取（%s）"' % label,
        '        description: "對話式本體提取：讀取知識庫片段，依本體類型為每個要素寫一張 wiki 頁面。"',
        "    config:",
        '      system_prompt_id: "ontology_extract_agent_%s"' % key,
        "      temperature: 0.1",
        "      max_iterations: 40",
        "      allowed_tools:",
    ]
    lines += ['        - "%s"' % tool for tool in PRESET_TOOLS]
    lines += [
        "      retain_retrieval_history: true",
        "      faq_priority_enabled: false",
        '      kb_selection_mode: "selected"',
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 合并 + 校验
# ---------------------------------------------------------------------------
def strip_generated(text: str) -> str:
    """去掉上一次生成的标记块（幂等）。"""
    if BEGIN not in text:
        return text
    head = text.split(BEGIN)[0]
    tail = text.split(END)[-1] if END in text else ""
    return head.rstrip("\n") + "\n" + tail.lstrip("\n")


def merge(baseline: str, block: str) -> str:
    body = strip_generated(baseline).rstrip("\n")
    return "%s\n\n%s\n%s\n%s\n" % (body, BEGIN, block, END)


def build_outputs() -> dict[str, str]:
    models = load_models()
    templates, presets = [], []
    for model in models:
        content = build_prompt_content(model, load_light(model))
        templates.append(template_entry(model, content))
        presets.append(preset_entry(model))
        print("[gen] 模型 %-3s %-14s 提示词 %5d 字符｜类 %d｜关系 %d"
              % (model["key"], model["label"], len(content),
                 len(class_lines(model)),
                 len(relation_lines(model))))
    out = {}
    for name, entries in (("agent_system_prompt.yaml", templates),
                          ("agent_type_presets.yaml", presets)):
        baseline = (BASELINE / name).read_text(encoding="utf-8")
        out[name] = merge(baseline, "\n".join(entries))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 WeKnora 挂载用的 preset + 提示词模板")
    parser.add_argument("--check", action="store_true", help="只检查产物是否最新")
    args = parser.parse_args()

    if not (BASELINE / "agent_system_prompt.yaml").is_file():
        raise SystemExit("缺少 baseline/agent_system_prompt.yaml（从容器 docker cp 出来放这里）")
    outputs = build_outputs()
    OUT.mkdir(parents=True, exist_ok=True)
    stale = []
    for name, text in outputs.items():
        target = OUT / name
        if args.check:
            current = target.read_text(encoding="utf-8") if target.is_file() else ""
            if current != text:
                stale.append(name)
            continue
        target.write_text(text, encoding="utf-8")
        print("[gen] 写出 %s（%d 字节）" % (target.relative_to(REPO).as_posix(),
                                           len(text.encode("utf-8"))))
    if args.check:
        if stale:
            print("[gen] 需要重新生成：%s" % ", ".join(stale))
            return 1
        print("[gen] 产物已是最新 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())


