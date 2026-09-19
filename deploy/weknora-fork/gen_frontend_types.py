"""生成前端用的本体类型映射 TS（单一来源：artifacts/weknora/ontology_index.json）。

产物：deploy/weknora-fork/frontend/ontologyTypes.ts
用途：
  - wiki 列表/树的类型标签与分组（模型 → 大类 → 类）
  - 图谱节点配色与类型过滤
  - 待确认合并页的识别

用法：python deploy/weknora-fork/gen_frontend_types.py
"""

from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
INDEX = REPO / "artifacts" / "weknora" / "ontology_index.json"
OUT = HERE / "frontend" / "ontologyTypes.ts"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def top_group(cls: dict, by_name: dict[str, dict]) -> str:
    """取该类的顶层父类标签作为「大类」（无父类则用自己）。"""
    seen = set()
    cur = cls
    while cur and (cur.get("parents") or []):
        name = cur["parents"][0]
        if name in seen or name not in by_name:
            break
        seen.add(name)
        cur = by_name[name]
    return cur.get("label") or cur["name"]


def main() -> int:
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    by_name: dict[str, dict] = {}
    for model in index["models"]:
        for cls in model["classes"]:
            by_name[cls["name"]] = cls

    entries: list[str] = []
    modules: list[dict] = []
    for mi, model in enumerate(index["models"]):
        modules.append({"key": model["key"], "label": model["label"], "order": mi})
        for ci, cls in enumerate(model["classes"]):
            entry = {
                "label": cls.get("label") or cls["name"],
                "color": cls.get("color") or "#94a3b8",
                "module": model["key"],
                "moduleLabel": model["label"],
                "group": top_group(cls, by_name),
                "order": mi * 1000 + ci,
            }
            entries.append('  "%s": %s,' % (cls["name"], json.dumps(entry, ensure_ascii=False)))
    modules_s = json.dumps(modules, ensure_ascii=False, indent=2)

    body = '''// 由 deploy/weknora-fork/gen_frontend_types.py 生成 —— 不要手改。
// 数据来源：artifacts/weknora/ontology_index.json（本体 TTL 的编译产物）。

export interface OntologyTypeMeta {
  label: string;
  color: string;
  module: string;
  moduleLabel: string;
  /** 大类（顶层父类的中文名），用于列表/树的二级分组 */
  group: string;
  order: number;
}

export const ONTOLOGY_TYPES: Record<string, OntologyTypeMeta> = {
%s
};

export const ONTOLOGY_MODULES: { key: string; label: string; order: number }[] = %s;

/** 所有本体类型 key（图过滤/请求参数用） */
export const ONTOLOGY_TYPE_KEYS: string[] = Object.keys(ONTOLOGY_TYPES);

/** 待确认合并页的类型（由 MCP 保存工具生成，人工裁决后消失） */
export const PENDING_MERGE_TYPE = 'ontology:PendingMerge';

export function isOntologyType(pageType: string | undefined | null): boolean {
  return !!pageType && !!ONTOLOGY_TYPES[pageType];
}

export function isPendingMergeType(pageType: string | undefined | null): boolean {
  return pageType === PENDING_MERGE_TYPE;
}

export function ontologyLabel(pageType: string): string {
  const meta = ONTOLOGY_TYPES[pageType];
  return meta ? meta.label : pageType;
}

/** 列表/树里显示成「目标（bmm:Goal）」这种形式，类名即标签、同时保留本体 key */
export function ontologyDisplayLabel(pageType: string): string {
  const meta = ONTOLOGY_TYPES[pageType];
  return meta ? `${meta.label}（${pageType}）` : pageType;
}

export function ontologyColor(pageType: string): string {
  const meta = ONTOLOGY_TYPES[pageType];
  return meta ? meta.color : '#94a3b8';
}

/** TDesign 主题：按模块给一个稳定可区分的标签色 */
export function ontologyTheme(pageType: string): string {
  const meta = ONTOLOGY_TYPES[pageType];
  if (!meta) return 'default';
  const themes = ['primary', 'success', 'warning', 'danger'];
  const idx = Math.abs(meta.module.split('').reduce((a, c) => a + c.charCodeAt(0), 0)) %% themes.length;
  return themes[idx];
}

export function ontologyGroup(pageType: string): string {
  const meta = ONTOLOGY_TYPES[pageType];
  return meta ? meta.group : '';
}

export function ontologyModule(pageType: string): string {
  const meta = ONTOLOGY_TYPES[pageType];
  return meta ? meta.module : '';
}

export function ontologyOrder(pageType: string): number {
  const meta = ONTOLOGY_TYPES[pageType];
  return meta ? meta.order : 999999;
}
''' % ("\n".join(entries), modules_s.replace("\n", "\n"))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(body, encoding="utf-8")
    print("[gen-fe] 写出 %s（%d 个本体类型 / %d 个模块 / %d 字节）"
          % (OUT.relative_to(REPO).as_posix(), len(entries), len(modules),
             len(body.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
