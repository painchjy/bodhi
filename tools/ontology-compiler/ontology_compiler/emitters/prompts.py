"""产物：抽取提示词（`artifacts/prompts/<模块>_extraction.md`）。

提示词是「本体约束」进到 LLM 的主要注入点，因此这里只做一件事：
把 TBox 里**已经声明过的东西**翻译成模型能照着执行的自然语言约束，绝不引入新语义。

同一份内容有两种粒度，共用同一套渲染数据：
- `render_prompt(view)`  -> Markdown 全文，给人看、给 Agent 用（含表格与反例）；
- `compact_prompt(view)` -> 单段紧凑文本，给 WeKnora `extract_config.text` 字段用（few-shot 的 Q 行：术语白名单 + 表述线索，有长度预算）；
- `extraction_constraints(view)` -> 硬约束单段文本，给 WeKnora `extract_config.custom_instructions` 用（进系统提示词，不占 Q 行）。

硬约束（写进提示词、也由 SHACL 在落库前机械校验）：
1. 类只能取术语表里的名字，不允许自造；
2. 关系的 domain/range 必须匹配（A --rel--> B 要求 rel 的 domain 覆盖 A、range 覆盖 B）；
3. 必填基数（minCardinality ≥ 1）在抽取阶段就要尽力满足，缺失时不要编造，标注 `missing`；
4. 枚举型取值必须来自枚举词表，不允许自由发挥；
5. 抽不出来就不要抽（宁缺毋滥），并把无法归类的内容放进 `unmatched`。
"""

from __future__ import annotations

from pathlib import Path

from ontology_compiler.config import ARTIFACTS_DIR
from ontology_compiler.lexicon import load_lexicons
from ontology_compiler.loader import ModuleView, build_module_views, display_iri
from ontology_compiler.model import OntologyBundleView

from ontology_compiler.emitters._common import banner, write_text


class PromptEmitter:
    name = "prompts"

    def emit(self, bundle: OntologyBundleView, out_dir: Path | None = None) -> list[str]:
        root = Path(out_dir) if out_dir is not None else Path(ARTIFACTS_DIR)
        lexicons = load_lexicons(bundle.modules)
        views = build_module_views(bundle, lexicons)
        written: list[str] = []
        for key, view in views.items():
            written.append(write_text(root / "prompts" / ("%s_extraction.md" % key), render_prompt(view)))
        written.append(write_text(root / "prompts" / "README.md", render_prompt_readme(bundle, views)))
        return written


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
def module_source_files(view: ModuleView) -> str:
    return "、".join(view.spec.rel_files())


def class_rows(view: ModuleView) -> list[tuple[str, str, str, str]]:
    rows = []
    for cls in view.classes:
        if cls.is_enum:
            continue
        rows.append(
            (
                cls.prefixed,
                cls.label or cls.local,
                (cls.comment or "").replace("\n", " ").strip(),
                "、".join(display_iri(iri, view.prefix_map) for iri in cls.parents) or "-",
            )
        )
    return rows


def relation_rows(view: ModuleView) -> list[tuple[str, str, str, str, str]]:
    rows = []
    for prop in view.object_properties:
        restrictions = ", ".join("%s=%s" % (r.kind, r.value_display) for r in prop.restrictions)
        rows.append(
            (
                prop.prefixed,
                prop.label or prop.local,
                prop.domain_display(),
                prop.range_display(),
                restrictions or "-",
            )
        )
    return rows


def attribute_rows(view: ModuleView) -> list[tuple[str, str, str, str]]:
    rows = []
    for prop in view.data_properties:
        rows.append((prop.prefixed, prop.label or prop.local, prop.domain_display(), prop.range_display()))
    return rows


def enum_blocks(view: ModuleView) -> list[str]:
    blocks = []
    for cls in view.classes:
        if not cls.is_enum:
            continue
        blocks.append("### %s（%s）" % (cls.label or cls.local, cls.prefixed))
        if cls.comment:
            blocks.append(cls.comment.strip())
        blocks.append("")
        for member in cls.members:
            blocks.append("- `%s`：%s" % (member.prefixed, member.label or member.local))
        blocks.append("")
    return blocks


def markdown_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    if not rows:
        return ["（本模块没有此类术语）", ""]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        cells = [(cell if cell else "-").replace("|", "/").replace("\n", " ") for cell in row]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def lexicon_list(lexicon: dict, key: str) -> list:
    """取词表分组，宽容地接受两种手写风格：

    - `anti_patterns: ["一句话反例", ...]`            -> 字符串原样返回；
    - `enum_keywords: {属性: {取值: [线索]}}`          -> 展开成 `[{key: 属性, 取值: [线索]}]`。
    """
    raw = lexicon.get(key) if isinstance(lexicon, dict) else None
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [
            dict(value, key=name) if isinstance(value, dict) else {"key": name, "value": value}
            for name, value in raw.items()
        ]
    if isinstance(raw, list):
        return list(raw)
    return []


def anti_pattern_lines(view: ModuleView) -> list[str]:
    """词表里的反例（最容易抽错的写法）——比正面规则更能压住幻觉。

    词表里既可能是「一句话字符串」（人写起来最快），也可能是 `{wrong, right, why}` 结构化条目，两种都吃下。
    """
    lines: list[str] = []
    for pattern in lexicon_list(view.lexicon, "anti_patterns"):
        if isinstance(pattern, str):
            lines.append("- ❌ %s" % pattern.strip())
            continue
        if not isinstance(pattern, dict):
            continue
        wrong = pattern.get("wrong") or pattern.get("anti") or pattern.get("term") or ""
        right = pattern.get("right") or pattern.get("correct") or pattern.get("prefer") or pattern.get("value") or ""
        why = pattern.get("why") or pattern.get("reason") or ""
        if not wrong and not right:
            continue
        lines.append("- ❌ %s → ✅ %s%s" % (wrong, right, ("（%s）" % why) if why else ""))
    return lines


def render_prompt(view: ModuleView) -> str:
    lines: list[str] = [banner("抽取提示词 · %s" % view.spec.label, source=module_source_files(view)), ""]
    lines.append("# 抽取提示词 · %s（模块 `%s`）" % (view.spec.label, view.key))
    lines.append("")
    lines.append(
        "你从**业务与架构文档**里抽取知识，产出必须严格使用下面列出的本体术语。"
        "本模块共 %d 个类、%d 个关系、%d 个属性；不在清单里的说法一律不要自造名称，"
        "确实无法归类的内容放进 `unmatched`。" % (len(view.classes), len(view.object_properties), len(view.data_properties))
    )
    lines.append("")

    lines.append("## 1. 可用的类（`nodes[].name` 只能取第一列）")
    lines.append("")
    lines.extend(markdown_table(("类", "中文名", "定义（什么算这个类）", "父类"), class_rows(view)))

    lines.append("## 2. 可用的关系（`relations[].name` 只能取第一列）")
    lines.append("")
    lines.extend(
        markdown_table(("关系", "中文名", "起点（domain）", "终点（range）", "基数约束"), relation_rows(view))
    )

    lines.append("## 3. 可用的数据属性（作为节点的属性，不建边）")
    lines.append("")
    lines.extend(markdown_table(("属性", "中文名", "适用类", "取值类型"), attribute_rows(view)))

    keyword_blocks = render_keyword_sections(view)
    if keyword_blocks:
        lines.append("## 4. 词表关键词（命中线索 -> 抽成对应类型/关系）")
        lines.append("")
        lines.extend(keyword_blocks)

    if view.classes:
        blocks = enum_blocks(view)
        if blocks:
            lines.append("## 5. 枚举取值（必须逐字取自下表，不允许自由发挥）")
            lines.append("")
            lines.extend(blocks)

    lines.append("## 6. 跨模块上下文（属于其他模块的术语，可引用但不要重复定义）")
    lines.append("")
    if view.external_by_module:
        for module, iris in view.external_by_module.items():
            lines.append("- `%s`：%s" % (module, "、".join(display_iri(iri, view.prefix_map) for iri in iris)))
    else:
        lines.append("（无，本模块自洽）")
    lines.append("")

    lines.append("## 7. 校验规则（违反则该条抽取作废）")
    lines.append("")
    lines.append("1. `nodes[].name` 只能取第 1 节里的类；`relations[].name` 只能取第 2 节里的关系；")
    lines.append("2. 关系两端类型必须匹配：起点必须属于 domain（或其子类），终点必须属于 range（或其子类）；")
    lines.append("3. 基数约束 ≥1 的关系必须尽量抽全；抽不到就留空并在 `missing` 里登记，**不要编造**；")
    lines.append("4. 枚举型属性的取值必须来自第 5 节；")
    lines.append("5. 每一处结论都要带上 `evidence`（原文片段或章节标题），便于人工复核；")
    lines.append("6. 宁缺毋滥：拿不准的不要抽。")
    lines.append("")
    anti = anti_pattern_lines(view)
    if anti:
        lines.append("### 最容易犯的错（词表反例）")
        lines.append("")
        lines.extend(anti)
        lines.append("")

    lines.append("## 8. 输出契约")
    lines.append("")
    lines.append("```json")
    lines.append("{")
    lines.append('  "nodes":     [{"name": "%s", "label": "中文名", "properties": {}, "evidence": "原文片段"}],' % (view.classes[0].prefixed if view.classes else "bmm:Resource"))
    lines.append('  "relations": [{"name": "%s", "source": "<节点 name>", "target": "<节点 name>", "evidence": "原文片段"}],' % (view.object_properties[0].prefixed if view.object_properties else "bmm:hasObjective"))
    lines.append('  "missing":   [{"name": "<应抽未抽的术语>", "reason": "文档未提及 / 表述模糊"}],')
    lines.append('  "unmatched": [{"text": "无法归类的原文说法", "why": "不在本模块术语表内"}]')
    lines.append("}")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def keyword_rows(lexicon: dict, group: str) -> list[tuple[str, str, str]]:
    """把 `type_keywords` / `relation_keywords` 归一化成 (线索, 目标, 说明) 三元组。"""
    rows: list[tuple[str, str, str]] = []
    for entry in lexicon_list(lexicon, group):
        if not isinstance(entry, dict):
            continue
        keywords = entry.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [keywords]
        target = entry.get("type") or entry.get("predicate") or ""
        if not target:
            continue
        rows.append(("、".join(str(k) for k in keywords), str(target), str(entry.get("note") or "")))
    return rows


def render_keyword_sections(view: ModuleView) -> list[str]:
    blocks: list[str] = []
    type_rows = keyword_rows(view.lexicon, "type_keywords")
    if type_rows:
        blocks.append("### 文档表述 → 抽成哪个类")
        blocks.append("")
        blocks.extend(markdown_table(("关键词 / 线索", "抽成这个类", "说明"), type_rows))

    relation_rows_ = keyword_rows(view.lexicon, "relation_keywords")
    if relation_rows_:
        blocks.append("### 文档表述 → 抽成哪条关系")
        blocks.append("")
        blocks.extend(markdown_table(("关键词 / 线索", "抽成这个关系", "说明"), relation_rows_))

    enum_rows: list[tuple[str, str, str]] = []
    for entry in lexicon_list(view.lexicon, "enum_keywords"):
        if not isinstance(entry, dict):
            continue
        prop = str(entry.get("key") or entry.get("property") or "")
        for value, hints in entry.items():
            if value in ("key", "property", "note") or not isinstance(hints, list):
                continue
            enum_rows.append((prop, str(value), "、".join(str(hint) for hint in hints)))
    if enum_rows:
        blocks.append("### 取值线索（属性 -> 取值 -> 文档里的说法）")
        blocks.append("")
        blocks.extend(markdown_table(("属性", "取值", "文档里的说法"), enum_rows))
    return blocks


def compact_prompt(view: ModuleView, external_limit: int = 20) -> str:
    """紧凑提示词：WeKnora `extract_config.text` 用（长度敏感，只保留判定必需的信息）。"""
    parts: list[str] = [
        "【任务】从当前文档抽取「%s（模块 %s）」知识：只能使用下列本体术语，禁止自造名称；"
        "确实无法归类的内容放进 unmatched，不要丢弃。" % (view.spec.label, view.key)
    ]
    classes = [cls.prefixed for cls in view.classes if not cls.is_enum]
    if classes:
        parts.append("【类】" + "、".join(classes))
    enums = [
        "%s=%s" % (cls.prefixed, "/".join(member.prefixed for member in cls.members))
        for cls in view.classes
        if cls.is_enum and cls.members
    ]
    if enums:
        parts.append("【枚举取值】" + "；".join(enums))
    relations = [
        "%s(%s→%s)" % (prop.prefixed, prop.domain_display(), prop.range_display()) for prop in view.object_properties
    ]
    if relations:
        parts.append("【关系】" + "；".join(relations))
    attributes = ["%s@%s" % (prop.prefixed, prop.domain_display()) for prop in view.data_properties]
    if attributes:
        parts.append("【属性】" + "；".join(attributes))

    hints = keyword_rows(view.lexicon, "type_keywords") + keyword_rows(view.lexicon, "relation_keywords")
    if hints:
        parts.append("【表述线索】" + "；".join("%s→%s" % (row[0], row[1]) for row in hints))

    external: list[str] = []
    for iris in view.external_by_module.values():
        external.extend(display_iri(iri, view.prefix_map) for iri in iris)
    if external:
        parts.append("【可引用的外部术语】" + "、".join(external[:external_limit]))

    parts.append(
        "【硬约束】关系两端类型必须匹配 domain/range；枚举取值必须来自【枚举取值】；"
        "拿不准的不要抽；每条结论附 evidence（原文片段）。"
    )
    return "\n".join(parts)


def extraction_constraints(view: ModuleView) -> str:
    """领域硬约束（WeKnora `extract_config.custom_instructions`：追加进系统提示词）。

    与 `compact_prompt` 的分工：术语清单放 `text`（few-shot 的 Q 行），约束放系统段——
    Q 行会被模型当“示例输入”模仿，系统段才是权威指令。
    上游注释也明说这里只补领域约束、**输出协议仍由系统侧持有**，所以这里不描述 JSON 形状。
    """
    enums = [
        "%s=%s" % (cls.prefixed, "/".join(member.prefixed for member in cls.members))
        for cls in view.classes
        if cls.is_enum and cls.members
    ]
    lines: list[str] = [
        "【本体硬约束｜模块 %s（key=%s）】" % (view.spec.label, view.key),
        "· 节点与关系类型只能取自本模块已声明的术语，禁止自造名称或用近义词替换。",
        "· 关系两端必须匹配该关系类型的 domain/range：domain 侧与 range 侧不得互换，也不得替成其它类。",
    ]
    if enums:
        lines.append("· 枚举型取值必须来自枚举词表：%s。" % "；".join(enums))
    lines.append("· 每条结论附 evidence（原文片段）；无法归类的原文片段放进 unmatched，不要丢弃。")
    lines.append("· 拿不准的不要抽（宁缺毋滥）；缺信息时不要编造。")
    return "\n".join(lines)


def render_prompt_readme(bundle: OntologyBundleView, views: dict[str, ModuleView]) -> str:
    lines: list[str] = [banner("抽取提示词说明", source="ontology/*.ttl + ontology/lexicon/*.keywords.yaml"), ""]
    lines.append("# artifacts/prompts —— 抽取提示词（自动生成）")
    lines.append("")
    lines.append("每个模块一份提示词，内容 = 该模块的 TBox（类/关系/属性/枚举）+ 词表关键词 + 硬约束 + 输出契约。")
    lines.append("WeKnora 的 `extract_config.<模块>.json` 里：`text` 是同一内容的紧凑版（few-shot 的 Q 行），")
    lines.append("硬约束另走 `custom_instructions`（进系统提示词），两者与本文同源。")
    lines.append("")
    lines.append("| 模块 | 文件 | 类 | 关系 | 属性 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for key, view in views.items():
        lines.append(
            "| %s | `%s_extraction.md` | %d | %d | %d |"
            % (view.spec.label, key, len(view.classes), len(view.object_properties), len(view.data_properties))
        )
    lines.append("")
    lines.append("用法：把提示词注入到「抽取」调用（WeKnora 抽取或本地 LLM 批处理）的 system 段；")
    lines.append("落库前必须过 `artifacts/shacl/generated.shapes.ttl` 的机械校验，提示词只是第一道闸。")
    lines.append("")
    lines.append("重新生成：`python tools/ontology-compiler/compile.py compile`（本体真源：%s）" % "、".join(sorted(bundle.source_files)))
    return "\n".join(lines)


