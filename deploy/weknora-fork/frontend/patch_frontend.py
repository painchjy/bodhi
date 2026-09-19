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


def replace_maybe(text: str, old: str, new: str, what: str) -> str:
    """容忍锚点缺失的替换——用于「v1 补丁结果 → v2」的迁移步骤。

    这样同一个脚本既能给**干净源码**打补丁（走 v1 再迁移到 v2），
    也能给**已打过 v1 的构建树**升级（v1 锚点存在就直接迁移）。
    """
    if new in text:
        print("  - 已是 v2：%s" % what)
        return text
    if text.count(old) != 1:
        print("  · 跳过（未找到 v1 锚点）：%s" % what)
        return text
    print("  + 迁移到 v2：%s" % what)
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
    patch_wikibrowser_v2(fe)


def patch_wikibrowser_v2(fe: pathlib.Path) -> None:
    """v2 调整（用户 2026-09-19 验收口径）：

    1. 目录只保留 2 级（模型 → 大类），**第三层直接就是知识页**；
       本体类型改由页面行前面的**类型标签**表达，不再多分层折叠。
       （数据结构侧：`category_path` 由三级改两级，见 server.class_category_path + backfill_paths.py）
    2. 侧栏 tab 只留「本体」与「待确认合并」——上游的「知识 / 摘要」在本知识库没有对应
       页面（DB 实测：只有 bmm:* 与 index），隐藏后不再出现「知识 9」这种后端口径的困惑数字。
       要恢复上游 tab：把 CONTENT_TABS 改回 [KNOWLEDGE_TAB, ONTOLOGY_TAB, PENDING_TAB, 'summary']。
    3. 页面行标题前加类型标签（本体类型的中文名，如「操作性业务规则」）。
    """
    path = fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    text = path.read_text(encoding="utf-8")

    # 1) tab 收敛
    text = replace_maybe(
        text,
        "const CONTENT_TABS = [KNOWLEDGE_TAB, ONTOLOGY_TAB, PENDING_TAB, 'summary']",
        "const CONTENT_TABS = [ONTOLOGY_TAB, PENDING_TAB]",
        "CONTENT_TABS 收敛为 本体 + 待确认合并")

    # 2) tab 标签：「本体」（此前直接显示英文键 ontology）
    text = replace_maybe(
        text,
        "  if (map[type]) return map[type]\n"
        "  if (isOntologyType(type)) return ontologyDisplayLabel(type)",
        "  if (map[type]) return map[type]\n"
        "  if (type === ONTOLOGY_TAB) return '本体'\n"
        "  if (isOntologyType(type)) return ontologyDisplayLabel(type)",
        "tab 标签 ontology → 本体")

    # 3) 页面行：类型标签 + 标题 + 版本徽标
    text = replace_maybe(
        text,
        '                      <span class="wiki-page-item-title">{{ item.page.title }}</span>\n'
        '                      <span v-if="(item.page.version || 1) > 1" '
        'class="wiki-page-item-version">v{{ item.page.version }}</span>',
        '                      <span v-if="isOntologyType(item.page.page_type) || isPendingMergeType(item.page.page_type)"\n'
        '                        :class="[\'wiki-page-item-type\', `wiki-page-item-type--${getTypeTheme(item.page.page_type)}`]">\n'
        '                        {{ getTypeLabel(item.page.page_type) }}</span>\n'
        '                      <span class="wiki-page-item-title">{{ item.page.title }}</span>\n'
        '                      <span v-if="(item.page.version || 1) > 1" '
        'class="wiki-page-item-version">v{{ item.page.version }}</span>',
        "页面行类型标签")

    if ".wiki-page-item-type {" not in text:
        text += ("\n<style scoped>\n"
                 "/* bodhi2：第三层是知识页，本体类型用标签区分（不再多分层折叠） */\n"
                 ".wiki-page-item-type {\n"
                 "  flex: 0 0 auto;\n"
                 "  margin-right: 6px;\n"
                 "  padding: 0 4px;\n"
                 "  font-size: 10px;\n"
                 "  line-height: 15px;\n"
                 "  border-radius: 3px;\n"
                 "  background: var(--td-brand-color-light, #e3f2fd);\n"
                 "  color: var(--td-brand-color, #0052d9);\n"
                 "  white-space: nowrap;\n"
                 "}\n"
                 ".wiki-page-item-type--warning { background: #fff3e0; color: #e37318; }\n"
                 ".wiki-page-item-type--success { background: #e8f5e9; color: #2ba471; }\n"
                 ".wiki-page-item-type--danger  { background: #fdecee; color: #d54941; }\n"
                 ".wiki-page-item-type--default { background: #eef0f3; color: #6b7280; }\n"
                 "</style>\n")
        print("  + 应用：类型标签样式")

    path.write_text(text, encoding="utf-8")
    print("  WikiBrowser.vue v2 完成（%d 字节）" % len(text.encode("utf-8")))
    patch_wikibrowser_v3(fe)


def patch_wikibrowser_v3(fe: pathlib.Path) -> None:
    """v3 微调（用户 2026-09-19 第三轮验收口径）：

    1. 类型标签**改图标 + 悬停提示**：中文标签太长会把页面名挤出可视区、版本徽标也被顶掉；
       改成小图标，tooltip 显示「中文名（本体类型）」。
    2. **版本徽标挪到最前**（v2 放在标题后，长标题下看不到）。
    3. 页面名允许换行（`min-width:0` + `overflow-wrap:anywhere`），列表容器可横向滚动。
    4. **列表视图也加类型图标与版本徽标**（原先只有树视图有）。
    """
    path = fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    text = path.read_text(encoding="utf-8")

    # 1+2+3) 树视图的页面行：徽标 → 类型图标(带 tooltip) → 标题
    text = replace_maybe(
        text,
        '                      <span v-if="isOntologyType(item.page.page_type) || isPendingMergeType(item.page.page_type)"\n'
        '                        :class="[\'wiki-page-item-type\', `wiki-page-item-type--${getTypeTheme(item.page.page_type)}`]">\n'
        '                        {{ getTypeLabel(item.page.page_type) }}</span>\n'
        '                      <span class="wiki-page-item-title">{{ item.page.title }}</span>\n'
        '                      <span v-if="(item.page.version || 1) > 1" '
        'class="wiki-page-item-version">v{{ item.page.version }}</span>',
        '                      <span v-if="(item.page.version || 1) > 1" '
        'class="wiki-page-item-version">v{{ item.page.version }}</span>\n'
        '                      <t-tooltip\n'
        '                        v-if="isOntologyType(item.page.page_type) || isPendingMergeType(item.page.page_type)"\n'
        '                        :content="`${getTypeLabel(item.page.page_type)}（${item.page.page_type}）`"\n'
        '                        placement="top">\n'
        '                        <t-icon :name="getPageIcon(item.page)" class="wiki-page-item-type-icon" />\n'
        '                      </t-tooltip>\n'
        '                      <span class="wiki-page-item-title">{{ item.page.title }}</span>',
        "树视图：徽标前置 + 类型图标(悬停看类名)")

    # 4) 列表视图：类型图标 + 版本徽标
    text = replace_maybe(
        text,
        '                    <div class="wiki-page-item-title">{{ item.title }}</div>',
        '                    <div class="wiki-page-item-title">\n'
        '                      <span v-if="(item.version || 1) > 1" '
        'class="wiki-page-item-version">v{{ item.version }}</span>\n'
        '                      <t-tooltip\n'
        '                        v-if="isOntologyType(item.page_type) || isPendingMergeType(item.page_type)"\n'
        '                        :content="`${getTypeLabel(item.page_type)}（${item.page_type}）`" placement="top">\n'
        '                        <t-icon :name="getPageIcon(item)" class="wiki-page-item-type-icon" />\n'
        '                      </t-tooltip>\n'
        '                      <span class="wiki-page-item-title-text">{{ item.title }}</span>\n'
        '                    </div>',
        "列表视图：徽标 + 类型图标")

    if ".wiki-page-item-type-icon {" not in text:
        text += ("\n<style scoped>\n"
                 "/* bodhi2 v3：类型用图标，悬停看类名；徽标前置；名称可换行 */\n"
                 ".wiki-page-item-type-icon {\n"
                 "  flex: 0 0 auto;\n"
                 "  margin-right: 4px;\n"
                 "  font-size: 13px;\n"
                 "  color: var(--td-brand-color, #0052d9);\n"
                 "}\n"
                 ".wiki-page-item-title {\n"
                 "  min-width: 0;\n"
                 "  flex: 1 1 auto;\n"
                 "  white-space: normal;\n"
                 "  overflow-wrap: anywhere;\n"
                 "  line-height: 1.35;\n"
                 "}\n"
                 ".wiki-page-item-title > .wiki-page-item-title-text { word-break: break-word; }\n"
                 ".wiki-tree-list,\n"
                 ".wiki-group-scroller { overflow-x: auto; }\n"
                 "</style>\n")
        print("  + 应用：v3 样式（图标/换行/横向滚动）")

    path.write_text(text, encoding="utf-8")
    print("  WikiBrowser.vue v3 完成（%d 字节）" % len(text.encode("utf-8")))


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

    # 幂等保护（2026-09-19 踩坑）：部分补丁的 new 里包含 old 锚点，判据
    # `new in text and old not in text` 会失效，重复运行就会**重复插入**
    # import / 版本徽标，vite 随即报 babel `parseImportSpecifier` 语法错误。
    # 因此这里显式拦一道：已打过补丁的源码必须先从干净副本还原。
    already = fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    if "@/utils/ontologyTypes" in already.read_text(encoding="utf-8"):
        raise SystemExit(
            "！！WikiBrowser.vue 已含本体补丁（需要干净源码）\n"
            "   还原办法：rm -rf %s/src && cp -a <干净副本>/src %s/src" % (fe, fe))

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
