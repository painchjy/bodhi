"""给 WeKnora 前端打「本体感知」补丁（幂等；在构建副本上跑）。

用法（在 WSL 里，<fe> = 前端构建副本，例如 /root/fe-build）：
    python3 deploy/weknora-fork/frontend/patch_frontend.py --fe /root/fe-build

改动点（见 docs/weknora-fork.md §10.15）：
1. 新增 src/utils/ontologyTypes.ts（由 gen_frontend_types.py 生成）
2. WikiBrowser.vue：
   - 引入本体类型映射
   - CONTENT_TABS 增加「本体」「待确认合并」两个 tab（空时自动隐藏）
   - tabPageTypes / statTotal 支持这两个 tab
   - getTypeLabel / getTypeTheme / getPageIcon 认识本体类型
   - graphFilterTypes 初始集合包含全部本体类型（否则 wiki 图会按类型把它们过滤掉）
   - groupedPages 末尾类型按本体顺序排序、待确认合并排最后
   - 树节点显示版本徽标（v2/v3，合并过的页）
3. 新增 src/views/knowledge/wiki/BodhiGraphTab.vue（Bodhi 语义图 tab，内嵌 /bodhi/view）
4. KnowledgeBase.vue：增加「本体图谱」tab
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent


def replace_once(text: str, old: str, new: str, what: str) -> str:
    if new in text and old not in text:
        print("  - 已应用：%s" % what)
        return text
    if text.count(old) != 1:
        raise SystemExit("！！补丁锚点不唯一或缺失（%s）：命中 %d 次" % (what, text.count(old)))
    print("  + 应用：%s" % what)
    return text.replace(old, new, 1)


def patch_wikibrowser(fe: pathlib.Path) -> None:
    path = fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "const KNOWLEDGE_TAB = 'knowledge'",
        "import { ONTOLOGY_TYPE_KEYS, isOntologyType, isPendingMergeType, "
        "ontologyDisplayLabel, ontologyOrder, ontologyTheme, PENDING_MERGE_TYPE } "
        "from '@/utils/ontologyTypes'\n\n"
        "const KNOWLEDGE_TAB = 'knowledge'",
        "import ontologyTypes")

    text = replace_once(
        text,
        "const CONTENT_TABS = [KNOWLEDGE_TAB, 'summary']",
        "// 本体要素统一进「本体」tab（页内按 category_path 三级折叠：模型 → 大类 → 类）；\n"
        "// 待确认合并单列一个 tab，人工裁决后消失。两者为空时自动隐藏。\n"
        "const ONTOLOGY_TAB = 'ontology'\n"
        "const PENDING_TAB = PENDING_MERGE_TYPE\n"
        "const CONTENT_TABS = [KNOWLEDGE_TAB, ONTOLOGY_TAB, PENDING_TAB, 'summary']\n\n"
        "// 当前知识库实际存在的本体类型（来自 stats；未加载完时退回全部已知类型）\n"
        "function ontologyTypesInStats(): string[] {\n"
        "  const byType = stats.value?.pages_by_type || {}\n"
        "  const present = Object.keys(byType).filter(t => isOntologyType(t))\n"
        "  return present.length ? present : ONTOLOGY_TYPE_KEYS\n"
        "}",
        "CONTENT_TABS + ontologyTypesInStats")

    text = replace_once(
        text,
        "return tab === KNOWLEDGE_TAB ? KNOWLEDGE_TYPES.join(',') : tab",
        "if (tab === KNOWLEDGE_TAB) return KNOWLEDGE_TYPES.join(',')\n"
        "  if (tab === ONTOLOGY_TAB) return ontologyTypesInStats().join(',')\n"
        "  return tab",
        "tabPageTypes")

    text = replace_once(
        text,
        "if (tab === KNOWLEDGE_TAB) return KNOWLEDGE_TYPES.reduce((sum, t) => sum + (byType[t] || 0), 0)",
        "if (tab === KNOWLEDGE_TAB) return KNOWLEDGE_TYPES.reduce((sum, t) => sum + (byType[t] || 0), 0)\n"
        "    if (tab === ONTOLOGY_TAB) return ontologyTypesInStats().reduce((sum, t) => sum + (byType[t] || 0), 0)",
        "statTotal")

    text = replace_once(
        text,
        "  for (const tab of Object.keys(pagesByType.value)) {\n"
        "    if (seen.has(tab)) continue\n"
        "    if (tab === 'index') continue\n"
        "    push(tab)\n"
        "  }",
        "  const rest = Object.keys(pagesByType.value)\n"
        "    .filter(tab => !seen.has(tab) && tab !== 'index')\n"
        "    .sort((a, b) => {\n"
        "      const pa = isPendingMergeType(a), pb = isPendingMergeType(b)\n"
        "      if (pa !== pb) return pa ? 1 : -1\n"
        "      return ontologyOrder(a) - ontologyOrder(b) || a.localeCompare(b)\n"
        "    })\n"
        "  for (const tab of rest) push(tab)",
        "groupedPages 排序")

    path.write_text(text, encoding="utf-8")
    print("  WikiBrowser.vue 阶段一完成（%d 字节）" % len(text.encode("utf-8")))


def patch_wikibrowser_part2(fe: pathlib.Path) -> None:
    path = fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "  }\n  return map[type] || 'default'\n}",
        "  }\n"
        "  if (isOntologyType(type)) return ontologyTheme(type)\n"
        "  if (isPendingMergeType(type)) return 'warning'\n"
        "  return map[type] || 'default'\n}",
        "getTypeTheme")
    text = replace_once(
        text,
        "  }\n  return map[type] || type\n}",
        "  }\n"
        "  if (map[type]) return map[type]\n"
        "  if (isOntologyType(type)) return ontologyDisplayLabel(type)\n"
        "  if (isPendingMergeType(type)) return '待确认合并'\n"
        "  return type\n}",
        "getTypeLabel")
    text = replace_once(
        text,
        "  }\n  return map[page.page_type] || 'file'\n}",
        "  }\n"
        "  if (isOntologyType(page.page_type)) return 'hierarchy'\n"
        "  if (isPendingMergeType(page.page_type)) return 'error-circle'\n"
        "  return map[page.page_type] || 'file'\n}",
        "getPageIcon")

    text = replace_once(
        text,
        "const graphFilterTypes = ref<Set<string>>(new Set(['summary', 'entity', 'concept', 'synthesis', 'comparison', 'index']))",
        "const graphFilterTypes = ref<Set<string>>(new Set(['summary', 'entity', 'concept', 'synthesis', "
        "'comparison', 'index', ...ONTOLOGY_TYPE_KEYS, PENDING_MERGE_TYPE]))",
        "graphFilterTypes")

    text = replace_once(
        text,
        '                      <span class="wiki-page-item-title">{{ item.page.title }}</span>',
        '                      <span class="wiki-page-item-title">{{ item.page.title }}</span>\n'
        '                      <span v-if="(item.page.version || 1) > 1" '
        'class="wiki-page-item-version">v{{ item.page.version }}</span>',
        "版本徽标")

    if ".wiki-page-item-version {" not in text:
        text += ("\n<style scoped>\n"
                 "/* bodhi2：合并过的页面显示版本徽标（配合 WikiRevisionDrawer 可回退） */\n"
                 ".wiki-page-item-version {\n"
                 "  margin-left: 6px;\n"
                 "  padding: 0 4px;\n"
                 "  font-size: 10px;\n"
                 "  line-height: 15px;\n"
                 "  border-radius: 4px;\n"
                 "  border: 1px solid var(--td-warning-color-3, #e37318);\n"
                 "  color: var(--td-warning-color, #e37318);\n"
                 "  flex: 0 0 auto;\n"
                 "}\n"
                 "</style>\n")
        print("  + 应用：版本徽标样式")

    path.write_text(text, encoding="utf-8")
    print("  WikiBrowser.vue 阶段二完成（%d 字节）" % len(text.encode("utf-8")))


def patch_knowledgebase(fe: pathlib.Path) -> None:
    path = fe / "src" / "views" / "knowledge" / "KnowledgeBase.vue"
    text = path.read_text(encoding="utf-8")
    if "BodhiGraphTab" in text:
        print("  - 已应用：KnowledgeBase.vue 本体图谱 tab")
        return

    # 1) 新增 tab 键
    text = replace_once(
        text,
        "const validTabs = ['documents', 'wiki', 'graph'] as const",
        "const validTabs = ['documents', 'wiki', 'graph', 'bodhi-graph'] as const",
        "validTabs")

    # 2) 面包屑里加「本体图谱」入口（挂在 graph 那个 tooltip 之后）
    text = replace_once(
        text,
        "                </t-tooltip>\n"
        "              </template>\n"
        "              <span v-else class=\"breadcrumb-current\">",
        "                </t-tooltip>\n"
        "                <span class=\"breadcrumb-tab-sep\">/</span>\n"
        "                <t-tooltip content=\"本体要素 + 带语义的关系（bodhi-mcp）\" placement=\"bottom\">\n"
        "                  <span :class=\"['breadcrumb-tab', { active: activeKbTab === 'bodhi-graph' }]\"\n"
        "                    @click=\"activeKbTab = 'bodhi-graph'\">\n"
        "                    本体图谱\n"
        "                  </span>\n"
        "                </t-tooltip>\n"
        "              </template>\n"
        "              <span v-else class=\"breadcrumb-current\">",
        "面包屑 tab")

    # 3) 主区域容器放行新 tab；并让 WikiBrowser 在新 tab 下不挂载（避免多余请求）
    text = replace_once(
        text,
        "<div v-if=\"isWiki && (activeKbTab === 'wiki' || activeKbTab === 'graph')\" class=\"wiki-main-area\">",
        "<div v-if=\"isWiki && (activeKbTab === 'wiki' || activeKbTab === 'graph' "
        "|| activeKbTab === 'bodhi-graph')\" class=\"wiki-main-area\">",
        "主区域 v-if")
    text = replace_once(
        text,
        "<WikiBrowser v-if=\"kbId\" :knowledge-base-id=\"kbId\" :view=\"activeKbTab === 'graph' ? 'graph' : 'browser'\"",
        "<WikiBrowser v-if=\"kbId && activeKbTab !== 'bodhi-graph'\" :knowledge-base-id=\"kbId\" "
        ":view=\"activeKbTab === 'graph' ? 'graph' : 'browser'\"",
        "WikiBrowser v-if 收窄")

    # 4) 在 WikiBrowser 元素结束后插入本体图谱组件（锚在它的收尾行，避免截断多行元素）
    text = replace_once(
        text,
        "          @view-graph=\"onViewWikiInGraph\" />",
        "          @view-graph=\"onViewWikiInGraph\" />\n"
        "        <!-- bodhi2：Bodhi 语义图谱（本体要素 + 带语义的关系；经 /bodhi/ 代理到 bodhi-mcp） -->\n"
        "        <BodhiGraphTab v-if=\"kbId && activeKbTab === 'bodhi-graph'\" "
        ":knowledge-base-id=\"kbId\" />",
        "插入 BodhiGraphTab")

    text = replace_once(
        text,
        "import WikiBrowser from './wiki/WikiBrowser.vue'",
        "import WikiBrowser from './wiki/WikiBrowser.vue'\n"
        "import BodhiGraphTab from './wiki/BodhiGraphTab.vue'",
        "KnowledgeBase.vue import")
    path.write_text(text, encoding="utf-8")
    print("  KnowledgeBase.vue 已插入 BodhiGraphTab（tab 键：bodhi-graph）")
    print("  KnowledgeBase.vue 完成（%d 字节）" % len(text.encode("utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser(description="给 WeKnora 前端打本体感知补丁")
    parser.add_argument("--fe", required=True, help="前端源码目录（构建副本）")
    args = parser.parse_args()
    fe = pathlib.Path(args.fe)
    if not (fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue").is_file():
        raise SystemExit("不是有效的前端目录：%s" % fe)

    print("== 1) 拷贝新增文件 ==")
    (fe / "src" / "utils").mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / "ontologyTypes.ts", fe / "src" / "utils" / "ontologyTypes.ts")
    print("  + src/utils/ontologyTypes.ts")
    shutil.copy2(HERE / "BodhiGraphTab.vue",
                 fe / "src" / "views" / "knowledge" / "wiki" / "BodhiGraphTab.vue")
    print("  + src/views/knowledge/wiki/BodhiGraphTab.vue")

    print("== 2) 补丁 WikiBrowser.vue（两阶段，便于定位失败） ==")
    patch_wikibrowser(fe)
    patch_wikibrowser_part2(fe)
    print("== 3) 补丁 KnowledgeBase.vue ==")
    patch_knowledgebase(fe)
    print("== 完成 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
