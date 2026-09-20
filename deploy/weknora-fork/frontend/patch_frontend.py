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
5. 新增 src/views/knowledge/wiki/BodhiOntologyUpload.vue（「上传本体文件」按钮，2026-09-20）：
   本体模型知识库页的面包屑右侧，只在该 KB 渲染；同名模块整体替换、级联删下游、出报告
   （见 docs/handoff-ontology-upload.md §6.1）
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent

# 本体模型知识库 id：与 tools/ke-core/ke_admin.ONTOLOGY_KB 同源（env ONTOLOGY_KB_ID，默认同一个 uuid）。
# 前端拿不到运行期配置，所以这个常量是**构建期**注入的 —— 换本体库时用同一个 ONTOLOGY_KB_ID 重新构建。
BODHI_ONTOLOGY_KB = os.environ.get("ONTOLOGY_KB_ID", "08810cbd-af86-48d1-bd25-3b2c338e3d68")


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
        "// 注意：**不包含** PENDING_MERGE_TYPE —— 待确认合并是人工裁决的工作项，不是知识，\n"
        "// 默认不进图谱（v8 同时改了 graphFilterTypesToArray 的「全选⇒不过滤」捷径，\n"
        "// 否则后端不过滤会把待确认页也带回来）\n"
        "const graphFilterTypes = ref<Set<string>>(new Set(['summary', 'entity', 'concept', 'synthesis', "
        "'comparison', 'index', ...ONTOLOGY_TYPE_KEYS]))",
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
       （数据结构侧：`category_path` 由三级改两级，见 ke_ontology.class_category_path；
       一次性迁移脚本 backfill_paths.py 已归档到 tools/ontology-mcp/archive/）
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


def patch_wikibrowser_v4(fe: pathlib.Path) -> None:
    """v4（用户 2026-09-19 §5.1）：类型**图标渲染为空** → 改成**彩色圆点**。

    根因：v3 用的 `<t-icon :name="getPageIcon(page)">` 对本体类型统一返回 `'hierarchy'`，
    而该图标名在打包后的 tdesign 图标集里不渲染（悬停 tooltip 正常，所以只是图标空白）。
    改法：换成 `<span class="wiki-page-item-type-dot" :style="{ background: ontologyColor(...) }">`，
    颜色来自 gen_frontend_types.py 生成的 `ontologyColor()`（同一张类型表，不在 Vue 里硬编码）。
    """
    path = fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    text = path.read_text(encoding="utf-8")

    # 1) 引入 ontologyColor
    text = replace_once(
        text,
        "import { ONTOLOGY_TYPE_KEYS, isOntologyType, isPendingMergeType, ontologyDisplayLabel, "
        "ontologyOrder, ontologyTheme, PENDING_MERGE_TYPE } from '@/utils/ontologyTypes'",
        "import { ONTOLOGY_TYPE_KEYS, isOntologyType, isPendingMergeType, ontologyColor, "
        "ontologyDisplayLabel, ontologyOrder, ontologyTheme, pageDotColor, PENDING_MERGE_TYPE } "
        "from '@/utils/ontologyTypes'",
        "import ontologyColor")

    # 2) 树视图行内图标 → 圆点（tooltip 内容不变）
    text = replace_once(
        text,
        '                        <t-icon :name="getPageIcon(item.page)" class="wiki-page-item-type-icon" />',
        '                        <span class="wiki-page-item-type-dot"\n'
        '                          :style="{ background: pageDotColor(item.page) }"></span>',
        "树视图类型圆点")

    # 3) 列表视图行内图标 → 圆点
    text = replace_once(
        text,
        '                        <t-icon :name="getPageIcon(item)" class="wiki-page-item-type-icon" />',
        '                        <span class="wiki-page-item-type-dot"\n'
        '                          :style="{ background: pageDotColor(item) }"></span>',
        "列表视图类型圆点")

    # 4) 阅读区标题下的类型徽标也用圆点（同一处“图标空白”问题）
    text = replace_once(
        text,
        '                        <t-icon :name="getPageIcon(selectedPage)" />\n'
        "                        {{ getTypeLabel(selectedPage.page_type) }}",
        '                        <span class="wiki-page-item-type-dot"\n'
        '                          :style="{ background: pageDotColor(selectedPage) }"></span>\n'
        "                        {{ getTypeLabel(selectedPage.page_type) }}",
        "阅读区类型徽标圆点")

    if ".wiki-page-item-type-dot {" not in text:
        text += ("\n<style scoped>\n"
                 "/* bodhi2 v4（§5.1）：本体类型用彩色圆点，简单且一定渲染得出来 */\n"
                 ".wiki-page-item-type-dot {\n"
                 "  display: inline-block;\n"
                 "  width: 8px;\n"
                 "  height: 8px;\n"
                 "  border-radius: 50%;\n"
                 "  margin-right: 6px;\n"
                 "  flex: 0 0 auto;\n"
                 "  vertical-align: middle;\n"
                 "}\n"
                 "</style>\n")
        print("  + 应用：类型圆点样式")

    path.write_text(text, encoding="utf-8")
    print("  WikiBrowser.vue v4 完成（%d 字节）" % len(text.encode("utf-8")))
    patch_wikibrowser_v5(fe)


def patch_wikibrowser_v5_script(fe: pathlib.Path) -> None:
    """v5 的 script/CSS 部分（与 patch_wikibrowser_v5 分开，便于定位失败）。"""
    path = fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    text = path.read_text(encoding="utf-8")

    anchor = ("// --- Manual page editing / version management ---------------------------\n"
              "\n"
              "const editingPage = ref(false)")
    block = (
        "// --- bodhi2 v5（§5.3）：多选批量删除 --------------------------------------\n"
        "// 选中集合存 slug（树/列表两种视图的 key 语义一致）。删除走后端 /bodhi/delete：\n"
        "// 软删除（可回溯）、永不动 index、删完自动重建目录树；失败时保留选择并回显原文。\n"
        "const selectedSlugs = ref<Set<string>>(new Set())\n"
        "const bulkDeleteVisible = ref(false)\n"
        "const bulkDeleting = ref(false)\n"
        "const bulkDeleteBody = computed(() => (`将**硬删除**已选 ${selectedSlugs.value.size} 页：`\n"
        "  + 'PG 记录与版本快照一起删掉、图库同步清理（便于重新提取）；slug=index 与上游页不受影响。确定继续？'))\n"
        "\n"
        "function toggleSlugSelected(slug: string, checked: boolean | number) {\n"
        "  const next = new Set(selectedSlugs.value)\n"
        "  if (checked) next.add(slug)\n"
        "  else next.delete(slug)\n"
        "  selectedSlugs.value = next\n"
        "}\n"
        "\n"
        "function clearSlugSelection() {\n"
        "  selectedSlugs.value = new Set()\n"
        "}\n"
        "\n"
        "async function doBulkDelete() {\n"
        "  const slugs = Array.from(selectedSlugs.value)\n"
        "  if (!slugs.length) { bulkDeleteVisible.value = false; return }\n"
        "  bulkDeleting.value = true\n"
        "  try {\n"
        "    let deleted = 0\n"
        "    // 分片：每批 ≤200 slug（后端与 psql 都轻松）\n"
        "    for (let i = 0; i < slugs.length; i += 200) {\n"
        "      const res = await fetch('/bodhi/delete', {\n"
        "        method: 'POST', headers: { 'Content-Type': 'application/json' },\n"
        "        body: JSON.stringify({ kb_id: props.knowledgeBaseId, slugs: slugs.slice(i, i + 200) }),\n"
        "      })\n"
        "      const data = await res.json().catch(() => ({}))\n"
        "      if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status))\n"
        "      deleted += (data && data.deleted) || 0\n"
        "    }\n"
        "    MessagePlugin.success('已删除 ' + deleted + ' 项（硬删除，可重新提取）')\n"
        "    const removed = new Set(slugs)\n"
        "    if (selectedPage.value && removed.has(selectedPage.value.slug)) selectedPage.value = null\n"
        "    clearSlugSelection()\n"
        "    bulkDeleteVisible.value = false\n"
        "    await loadPages()\n"
        "    await loadStats()\n"
        "  } catch (e) {\n"
        "    MessagePlugin.error(((e as any) && (e as any).message) || '删除失败')\n"
        "  } finally {\n"
        "    bulkDeleting.value = false\n"
        "  }\n"
        "}\n"
        "\n"
        + anchor)
    text = replace_once(text, anchor, block, "多选删除 script")

    if ".wiki-bulk-bar {" not in text:
        text += ("\n<style scoped>\n"
                 "/* bodhi2 v5（§5.3）：多选工具条与行内复选框 */\n"
                 ".wiki-bulk-bar {\n"
                 "  display: flex;\n"
                 "  align-items: center;\n"
                 "  gap: 6px;\n"
                 "  padding: 6px 8px;\n"
                 "  margin: 0 8px 6px;\n"
                 "  border: 1px solid var(--td-warning-color-3, #e37318);\n"
                 "  border-radius: 4px;\n"
                 "  background: var(--td-warning-color-1, #fff3e0);\n"
                 "  font-size: 12px;\n"
                 "}\n"
                 ".wiki-bulk-count { font-weight: 600; color: var(--td-warning-color, #e37318); }\n"
                 ".wiki-bulk-hint { color: var(--td-text-color-secondary, #666); }\n"
                 ".wiki-bulk-spacer { flex: 1 1 auto; }\n"
                 ".wiki-page-checkbox {\n"
                 "  flex: 0 0 auto;\n"
                 "  display: inline-flex;\n"
                 "  align-items: center;\n"
                 "  margin-right: 6px;\n"
                 "}\n"
                 "</style>\n")
        print("  + 应用：多选删除样式")

    path.write_text(text, encoding="utf-8")
    print("  WikiBrowser.vue v5 script 完成（%d 字节）" % len(text.encode("utf-8")))
    patch_wikibrowser_v6(fe)


def patch_wikibrowser_v6(fe: pathlib.Path) -> None:
    """v6（用户 2026-09-19 需求 1）：wiki 编辑页可改**本体类型**（下拉，含模块前缀 + 中文 label）。

    数据来自 bodhi-mcp `GET /bodhi/ontology/classes`（查 Neo4j 本体投影，47 个类）。
    保存链路：先按上游方式存正文，再调我们自己的 `POST /bodhi/page/type`
    —— 上游 `PUT /wiki/pages/<slug>` 只接受 6 个内建 page_type，传 `bmm:Goal` 会 400。
    类型改动会一起重算 `category_path` 并重建目录树（后端做，带回版本快照）。
    """
    path = fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    text = path.read_text(encoding="utf-8")

    # 1) 编辑态：标题输入框**之后**加「本体类型」下拉
    #    ⚠️ 不能插在 `<h2 v-if="!editingPage">` 与 `<t-input v-else v-model="editForm.title">` 之间：
    #    那样会打断 v-if/v-else 配对，等于把标题输入框放到**阅读态**显示（实测：空框，
    #    且跳转后残留上一页标题）。
    anchor = ('                    <t-input v-else v-model="editForm.title" class="wiki-edit-field wiki-edit-field--title"\n'
              '                      :placeholder="$t(\'knowledgeEditor.wikiBrowser.editTitlePlaceholder\')" />')
    text = replace_once(
        text,
        anchor,
        anchor + "\n"
        '                    <div v-if="editingPage" class="wiki-edit-type-row">\n'
        '                      <span class="wiki-edit-type-label">本体类型</span>\n'
        '                      <span class="wiki-page-item-type-dot"\n'
        '                        :style="{ background: ontologyColor(editForm.page_type) }"></span>\n'
        '                      <t-select v-model="editForm.page_type" :options="ontologyClassOptions" filterable clearable\n'
        '                        :loading="ontologyClassesLoading" class="wiki-edit-type-select"\n'
        '                        placeholder="选择本体类（bmm:Goal / ea:Customer …）" />\n'
        '                      <span v-if="editForm.page_type && editForm.page_type !== editBaseType"\n'
        '                        class="wiki-edit-type-hint">\n'
        '                        保存后类型、目录与徽标一起更新（旧版本可回退）\n'
        '                      </span>\n'
        '                    </div>',
        "编辑态类型下拉")

    # 2) editForm 增加 page_type + 类清单加载
    text = replace_once(
        text,
        "const editForm = ref({ title: '', summary: '', content: '' })",
        "const editForm = ref({ title: '', summary: '', content: '', page_type: '' })\n"
        "// bodhi2 v6（需求 1）：本体类型下拉。类清单来自 /bodhi/ontology/classes\n"
        "// （bodhi-mcp 查 Neo4j 本体投影：模块前缀 + 中文 label）。\n"
        "const editBaseType = ref('')\n"
        "const ontologyClassOptions = ref<{ label: string; value: string; group: string }[]>([])\n"
        "const ontologyClassesLoading = ref(false)\n"
        "const ontologyClassesLoaded = ref(false)\n"
        "\n"
        "// 本体类型/关系类型一律走 bodhi-mcp 的 /bodhi/*（同源反代、读 Neo4j 本体投影；\n"
        "// 本体数据的维护（编译 / 清理模型 / 灌库 / 重投影 wiki）也走同一个服务的\n"
        "// /bodhi/ontology/*，浏览器不直连任何 Python 进程）。\n"
        "async function loadOntologyClasses() {\n"
        "  if (ontologyClassesLoaded.value || ontologyClassesLoading.value) return\n"
        "  ontologyClassesLoading.value = true\n"
        "  try {\n"
        "    const res = await fetch('/bodhi/ontology/classes')\n"
        "    const data = await res.json().catch(() => ({}))\n"
        "    if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status))\n"
        "    ontologyClassOptions.value = (data.classes || []).map((c: any) => ({\n"
        "      label: c.label + '（' + c.prefixed + '）',\n"
        "      value: c.prefixed,\n"
        "      group: c.module_label || c.module,\n"
        "    }))\n"
        "    ontologyClassesLoaded.value = ontologyClassOptions.value.length > 0\n"
        "  } catch (e) {\n"
        "    console.error('本体类清单加载失败:', e)\n"
        "  } finally {\n"
        "    ontologyClassesLoading.value = false\n"
        "  }\n"
        "}",
        "editForm.page_type + 类清单")

    # 3) 进入编辑时带上类型并预取类清单
    text = replace_once(
        text,
        "  editForm.value = {\n"
        "    title: selectedPage.value.title,\n"
        "    summary: selectedPage.value.summary || '',\n"
        "    content: selectedPage.value.content || '',\n"
        "  }",
        "  editForm.value = {\n"
        "    title: selectedPage.value.title,\n"
        "    summary: selectedPage.value.summary || '',\n"
        "    content: selectedPage.value.content || '',\n"
        "    page_type: selectedPage.value.page_type || '',\n"
        "  }\n"
        "  editBaseType.value = selectedPage.value.page_type || ''\n"
        "  void loadOntologyClasses()",
        "startEditPage 带上 page_type")

    path.write_text(text, encoding="utf-8")
    print("  WikiBrowser.vue v6 模板/状态完成（%d 字节）" % len(text.encode("utf-8")))
    patch_wikibrowser_v6_save(fe)


def patch_wikibrowser_v6_save(fe: pathlib.Path) -> None:
    """v6 的保存链路：正文保存成功后，若类型变了再调 /bodhi/page/type。"""
    path = fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "    const updated = ((res as any).data || res) as WikiPage\n"
        "    selectedPage.value = updated",
        "    let updated = ((res as any).data || res) as WikiPage\n"
        "    // bodhi2 v6：类型改动走上游不支持的通道（PUT /wiki/pages 只认内建类型）。\n"
        "    if (editForm.value.page_type && editForm.value.page_type !== editBaseType.value) {\n"
        "      try {\n"
        "        const res2 = await fetch('/bodhi/page/type', {\n"
        "          method: 'POST', headers: { 'Content-Type': 'application/json' },\n"
        "          body: JSON.stringify({ kb_id: props.knowledgeBaseId, slug,\n"
        "                                 page_type: editForm.value.page_type }),\n"
        "        })\n"
        "        const data2 = await res2.json().catch(() => ({}))\n"
        "        if (!res2.ok) throw new Error((data2 && data2.error) || ('HTTP ' + res2.status))\n"
        "        editBaseType.value = editForm.value.page_type\n"
        "        MessagePlugin.success('本体类型已改为 ' + (data2.type_label || editForm.value.page_type))\n"
        "        try {\n"
        "          const fresh = await getWikiPage(props.knowledgeBaseId, slug)\n"
        "          updated = (((fresh as any).data) || fresh) as WikiPage\n"
        "        } catch (e) { /* 忽略：类型已改成功，本地版本稍旧 */ }\n"
        "        await loadPages()\n"
        "      } catch (e) {\n"
        "        MessagePlugin.error(((e as any) && (e as any).message) || '本体类型修改失败')\n"
        "      }\n"
        "    }\n"
        "    selectedPage.value = updated",
        "savePageEdit 类型通道")

    if ".wiki-edit-type-row {" not in text:
        text += ("\n<style scoped>\n"
                 "/* bodhi2 v6（需求 1）：编辑态的本体类型选择行 */\n"
                 ".wiki-edit-type-row {\n"
                 "  display: flex;\n"
                 "  align-items: center;\n"
                 "  gap: 6px;\n"
                 "  margin: 8px 0;\n"
                 "  flex-wrap: wrap;\n"
                 "}\n"
                 ".wiki-edit-type-label {\n"
                 "  font-size: 12px;\n"
                 "  color: var(--td-text-color-secondary, #666);\n"
                 "  flex: 0 0 auto;\n"
                 "}\n"
                 ".wiki-edit-type-select { flex: 0 0 320px; max-width: 100%; }\n"
                 ".wiki-edit-type-hint {\n"
                 "  font-size: 12px;\n"
                 "  color: var(--td-warning-color, #e37318);\n"
                 "}\n"
                 "</style>\n")
        print("  + 应用：编辑态类型行样式")

    path.write_text(text, encoding="utf-8")
    print("  WikiBrowser.vue v6 保存链路完成（%d 字节）" % len(text.encode("utf-8")))
    patch_wikibrowser_v7(fe)


def patch_wikibrowser_v7(fe: pathlib.Path) -> None:
    """v7（用户 2026-09-19 需求 2）：阅读区嵌入**本体关系维护面板**。

    面板本身是独立组件 `BodhiRelationsPanel.vue`（补丁只插入一行标签 + 一个回调），
    出边可增/改/删，入边只读（反向边须在对方页面改）。编辑正文时隐藏面板，
    避免「本地未保存正文」与「服务端写关系」互相覆盖。
    """
    path = fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "import WikiRevisionDrawer from './WikiRevisionDrawer.vue'",
        "import WikiRevisionDrawer from './WikiRevisionDrawer.vue'\n"
        "import BodhiRelationsPanel from './BodhiRelationsPanel.vue'",
        "import BodhiRelationsPanel")

    text = replace_once(
        text,
        "              <!-- Page footer: backlinks + sources -->",
        "              <!-- bodhi2 v6（需求 2）：本体关系维护（出边可改、入边只读） -->\n"
        "              <BodhiRelationsPanel v-if=\"selectedPage && !editingPage\"\n"
        "                :knowledge-base-id=\"props.knowledgeBaseId\" :slug=\"selectedPage.slug\"\n"
        "                :page-type=\"ontologySourceType(selectedPage)\" :page-title=\"selectedPage.title\"\n"
        "                :can-edit=\"props.canEdit\" @changed=\"onRelationsChanged\" @navigate=\"navigateToSlug\" />\n"
        "              <div v-else-if=\"selectedPage && editingPage\" class=\"wiki-rel-editing-hint\">\n"
        "                正在编辑正文：保存后再维护本体关系，避免与本地编辑互相覆盖。\n"
        "              </div>\n"
        "\n"
        "              <!-- Page footer: backlinks + sources -->",
        "关系面板标签")

    text = replace_once(
        text,
        "// graphFilterTypesToArray returns the active allow-list as an array, or",
        "// bodhi2 v6：关系面板改动后同步当前页（正文与版本都变了）\n"
        "// 本体关系面板要查的「本体类」：本体库页的 page_type 是统一的 ontology:Class/Relation/…，\n"
        "// 真正的类在 page_metadata.ontology.class（模型页写本地名，需要补 module 前缀）。\n"
        "function ontologySourceType(page: any): string {\n"
        "  if (!page) return ''\n"
        "  const pt = String(page.page_type || '')\n"
        "  // 实例页（bmm:Goal / ea:Activity …）一律以 **page_type 为准**：page_metadata 里的 class\n"
        "  // 可能是抽取时写入的旧值，用它会出现「列出来的关系类型不是该类出发的」（2026-09-20）\n"
        "  if (pt && pt.indexOf('ontology:') !== 0) return pt\n"
        "  const ont = (page.page_metadata && page.page_metadata.ontology) || {}\n"
        "  const cls = String(ont.class || '')\n"
        "  if (!cls) return pt\n"
        "  if (cls.indexOf(':') >= 0) return cls\n"
        "  return ont.model ? `${ont.model}:${cls}` : cls\n"
        "}\n"
        "\n"
        "async function onRelationsChanged() {\n"
        "  if (!selectedPage.value) return\n"
        "  const slug = selectedPage.value.slug\n"
        "  try {\n"
        "    const res = await getWikiPage(props.knowledgeBaseId, slug)\n"
        "    selectedPage.value = (res as any).data || res as any\n"
        "    await loadStats()\n"
        "  } catch (e) {\n"
        "    console.error('关系变更后刷新页面失败:', e)\n"
        "  }\n"
        "}\n"
        "\n"
        "// graphFilterTypesToArray returns the active allow-list as an array, or",
        "onRelationsChanged")

    if ".wiki-rel-editing-hint {" not in text:
        text += ("\n<style scoped>\n"
                 "/* bodhi2 v6：编辑正文时关系面板的替代提示 */\n"
                 ".wiki-rel-editing-hint {\n"
                 "  margin: 14px 0;\n"
                 "  padding: 8px 10px;\n"
                 "  border: 1px dashed var(--td-component-stroke, #e7e7e7);\n"
                 "  border-radius: 4px;\n"
                 "  font-size: 12px;\n"
                 "  color: var(--td-text-color-placeholder, #999);\n"
                 "}\n"
                 "</style>\n")
        print("  + 应用：关系编辑提示样式")

    path.write_text(text, encoding="utf-8")
    print("  WikiBrowser.vue v7 完成（%d 字节）" % len(text.encode("utf-8")))
    patch_wikibrowser_v8(fe)


def patch_wikibrowser_v8(fe: pathlib.Path) -> None:
    """v8（用户 2026-09-20）：**待确认合并页不要进图谱**。

    上游有个捷径：`graphFilterTypesToArray()` 在「内建类型全勾选」时返回 `undefined`，
    意即“不传类型过滤”；而后端**不过滤**就会把 `ontology:PendingMerge` 这类页一起返回，
    于是「疑似与存量相同、待确认」的工作项就混进了图谱。改为**始终传显式类型清单**，
    配合 v1 段里默认集合去掉 PENDING_MERGE_TYPE，待确认页默认不再进图。
    """
    path = fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "  const all = ['summary', 'entity', 'concept', 'synthesis', 'comparison', 'index']\n"
        "  if (all.every(t => graphFilterTypes.value.has(t))) {\n"
        "    return undefined\n"
        "  }\n"
        "  return Array.from(graphFilterTypes.value)",
        "  // bodhi2 v8：不再用「全都勾选 ⇒ 不传过滤」的捷径 —— 后端收到空类型等于“不过滤”，\n"
        "  // 会把 ontology:PendingMerge（待确认合并）等页也带进图里。始终传显式清单。\n"
        "  return Array.from(graphFilterTypes.value)",
        "graphFilterTypesToArray 去掉全选捷径")
    path.write_text(text, encoding="utf-8")
    print("  WikiBrowser.vue v8 完成（待确认合并默认不进图谱）")
    patch_wikibrowser_v9(fe)


def patch_wikibrowser_v9(fe: pathlib.Path) -> None:
    """v9（用户 2026-09-20）：**待确认合并的裁决 UI 放进「待确认合并」tab**。

    之前它长在「本体图谱」tab 顶部（`BodhiGraphTab.vue`），让图谱页看起来混着工作项；
    用户口径：图只管图，裁决应该在「待确认合并」里。这里改成：
    打开一张 `ontology:PendingMerge` 页时，在正文上方出现「合并到候选页 / 作为新页新增」。
    """
    path = fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    text = path.read_text(encoding="utf-8")

    # 1) 正文上方的裁决条
    text = replace_once(
        text,
        "              <!-- Content -->",
        "              <!-- bodhi2 v9：待确认合并（疑似与存量相同）的裁决入口 -->\n"
        "              <div v-if=\"selectedPage && isPendingMergeType(selectedPage.page_type)\"\n"
        "                class=\"wiki-pending-bar\">\n"
        "                <span class=\"wiki-pending-bar-text\">\n"
        "                  疑似与存量相同（相似度介于两个阈值之间），请裁决：\n"
        "                </span>\n"
        "                <t-button size=\"small\" theme=\"primary\" :loading=\"pendingResolving\"\n"
        "                  @click=\"resolvePendingPage('merge')\">合并到候选页</t-button>\n"
        "                <t-button size=\"small\" variant=\"outline\" :loading=\"pendingResolving\"\n"
        "                  @click=\"resolvePendingPage('create')\">作为新页新增</t-button>\n"
        "                <span class=\"wiki-pending-bar-hint\">\n"
        "                  合并 = 写入候选页（原页生成新版本，可回退）\n"
        "                </span>\n"
        "              </div>\n"
        "\n"
        "              <!-- Content -->",
        "待确认合并裁决条")

    # 2) 裁决逻辑
    text = replace_once(
        text,
        "async function onRelationsChanged() {",
        "// bodhi2 v9：裁决待确认合并页（merge = 合到候选页 / create = 作为新页保留）\n"
        "const pendingResolving = ref(false)\n"
        "async function resolvePendingPage(action: 'merge' | 'create') {\n"
        "  if (!selectedPage.value) return\n"
        "  const slug = selectedPage.value.slug\n"
        "  pendingResolving.value = true\n"
        "  try {\n"
        "    const res = await fetch(`/bodhi/resolve?kb_id=${encodeURIComponent(props.knowledgeBaseId)}`\n"
        "      + `&slug=${encodeURIComponent(slug)}&action=${action}`)\n"
        "    const data = await res.json().catch(() => ({}))\n"
        "    if (!res.ok || data.error) throw new Error((data && data.error) || ('HTTP ' + res.status))\n"
        "    MessagePlugin.success(action === 'merge'\n"
        "      ? '已合并到候选页（原页生成新版本，可回退）'\n"
        "      : '已作为新页新增')\n"
        "    selectedPage.value = null\n"
        "    await loadPages()\n"
        "    await loadStats()\n"
        "  } catch (e) {\n"
        "    MessagePlugin.error(`裁决失败：${((e as any) && (e as any).message) || e}`)\n"
        "  } finally {\n"
        "    pendingResolving.value = false\n"
        "  }\n"
        "}\n"
        "\n"
        "async function onRelationsChanged() {",
        "resolvePendingPage")

    if ".wiki-pending-bar {" not in text:
        text += ("\n<style scoped>\n"
                 "/* bodhi2 v9：待确认合并页的裁决条 */\n"
                 ".wiki-pending-bar {\n"
                 "  display: flex;\n"
                 "  align-items: center;\n"
                 "  gap: 8px;\n"
                 "  margin: 10px 0;\n"
                 "  padding: 8px 10px;\n"
                 "  border: 1px solid var(--td-warning-color-3, #e37318);\n"
                 "  border-radius: 4px;\n"
                 "  background: var(--td-warning-color-1, #fff3e0);\n"
                 "  font-size: 12px;\n"
                 "  flex-wrap: wrap;\n"
                 "}\n"
                 ".wiki-pending-bar-text { font-weight: 600; }\n"
                 ".wiki-pending-bar-hint { color: var(--td-text-color-secondary, #666); }\n"
                 "</style>\n")
        print("  + 应用：待确认裁决条样式")

    path.write_text(text, encoding="utf-8")
    print("  WikiBrowser.vue v9 完成（待确认裁决移入待确认 tab）")
    patch_knowledgebase_v5(fe)


def patch_wikibrowser_v5(fe: pathlib.Path) -> None:
    """v5（用户 2026-09-19 §5.3）：树/列表**多选批量删除**。

    后端：`POST /bodhi/delete`（tools/ontology-mcp/server.py → ke_pages.delete_pages）
    —— **硬删除**（PG 记录 + 版本快照 + 目录树；用户 2026-09-20 口径）、**永不动 index**。
    """
    path = fe / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    text = path.read_text(encoding="utf-8")

    # 1) 工具条 + 二次确认弹窗（插在列表容器之前）
    text = replace_once(
        text,
        "              <!-- Active-tab list -->",
        "              <!-- bodhi2 v5（§5.3）：多选批量删除工具条（有选中才出现） -->\n"
        "              <div v-if=\"selectedSlugs.size > 0\" class=\"wiki-bulk-bar\">\n"
        "                <span class=\"wiki-bulk-count\">已选 {{ selectedSlugs.size }} 项</span>\n"
        "                <span class=\"wiki-bulk-hint\">软删除、可回溯；index 与上游页不受影响</span>\n"
        "                <span class=\"wiki-bulk-spacer\" />\n"
        "                <t-button size=\"small\" variant=\"text\" @click=\"clearSlugSelection\">清空选择</t-button>\n"
        "                <t-button size=\"small\" theme=\"danger\" @click=\"bulkDeleteVisible = true\">\n"
        "                  删除（{{ selectedSlugs.size }}）\n"
        "                </t-button>\n"
        "              </div>\n"
        "              <t-dialog v-model:visible=\"bulkDeleteVisible\" theme=\"danger\" header=\"批量删除 wiki 页\"\n"
        "                :body=\"bulkDeleteBody\" :confirm-btn=\"{ content: '删除', theme: 'danger', loading: bulkDeleting }\"\n"
        "                @confirm=\"doBulkDelete\" />\n\n"
        "              <!-- Active-tab list -->",
        "多选工具条 + 确认弹窗")

    # 2) 树视图：页面行加复选框
    text = replace_once(
        text,
        '                      @dragend="onPageDragEnd">\n'
        '                      <t-icon :name="getPageIcon(item.page)"',
        '                      @dragend="onPageDragEnd">\n'
        '                      <span class="wiki-page-checkbox" @click.stop>\n'
        '                        <t-checkbox :checked="selectedSlugs.has(item.page.slug)"\n'
        '                          @change="(v: any) => toggleSlugSelected(item.page.slug, v)" />\n'
        '                      </span>\n'
        '                      <t-icon :name="getPageIcon(item.page)"',
        "树视图复选框")

    # 3) 列表视图：页面行加复选框（列表项的 id/slug 同为 slug 语义的 key）
    text = replace_once(
        text,
        "                  <div :class=\"['wiki-page-item', 'wiki-page-item--list', { active: selectedPage?.id === item.id }]\"\n"
        "                    @click=\"selectPage(item)\">\n"
        '                    <div class="wiki-page-item-title">',
        "                  <div :class=\"['wiki-page-item', 'wiki-page-item--list', { active: selectedPage?.id === item.id }]\"\n"
        "                    @click=\"selectPage(item)\">\n"
        '                    <span class="wiki-page-checkbox" @click.stop>\n'
        '                      <t-checkbox :checked="selectedSlugs.has(item.slug)"\n'
        '                        @change="(v: any) => toggleSlugSelected(item.slug, v)" />\n'
        '                    </span>\n'
        '                    <div class="wiki-page-item-title">',
        "列表视图复选框")

    path.write_text(text, encoding="utf-8")
    print("  WikiBrowser.vue v5 模板完成（%d 字节）" % len(text.encode("utf-8")))
    patch_wikibrowser_v5_script(fe)


def patch_knowledgebase_v5(fe: pathlib.Path) -> None:
    """v5（用户 2026-09-19 §5.2）：知识库头部加「上传自动生成 wiki」开关。

    - 开关读写 KB 能力位 `indexing_strategy.wiki_enabled`；
    - 写走后端 `POST /bodhi/kb/wiki-flag`（直接改 jsonb 字段）：
      上游 `PUT /knowledge-bases/:id` 要求整份 config（chunking_config 等是值类型，
      缺字段会被清空），所以不用它；
    - 关闭后上传不再自动生成 wiki；**同时后端会拒 wiki 列表接口**（`wiki_enabled=false`
      → /wiki/pages 400），所以文案里必须写明「界面暂时看不到 wiki/图谱，需要时再打开」。
    """
    path = fe / "src" / "views" / "knowledge" / "KnowledgeBase.vue"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "const isWiki = computed(() => true)",
        "const isWiki = computed(() => true)\n"
        "\n"
        "// bodhi2 v5（§5.2）：上传是否自动生成 wiki（KB 能力位 indexing_strategy.wiki_enabled）。\n"
        "// 注意：关闭期间后端会拒绝 /wiki/pages 与 /wiki/folders（error code 1000），\n"
        "// 界面上 wiki/图谱会「看起来是空的」——所以开关旁边必须有明确提示。\n"
        "const wikiAutoEnabled = ref<boolean | null>(null)\n"
        "const wikiAutoSaving = ref(false)\n"
        "watch(() => (kbInfo.value && kbInfo.value.indexing_strategy\n"
        "  ? kbInfo.value.indexing_strategy.wiki_enabled : undefined), (v) => {\n"
        "  wikiAutoEnabled.value = typeof v === 'boolean' ? v : null\n"
        "}, { immediate: true })\n"
        "\n"
        "async function onWikiAutoToggle(value: boolean | number) {\n"
        "  const target = !!value\n"
        "  const previous = wikiAutoEnabled.value\n"
        "  const id = kbId.value\n"
        "  if (!id) return\n"
        "  wikiAutoSaving.value = true\n"
        "  wikiAutoEnabled.value = target\n"
        "  try {\n"
        "    const res = await fetch('/bodhi/kb/wiki-flag', {\n"
        "      method: 'POST', headers: { 'Content-Type': 'application/json' },\n"
        "      body: JSON.stringify({ kb_id: id, enabled: target }),\n"
        "    })\n"
        "    const data = await res.json().catch(() => ({}))\n"
        "    if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status))\n"
        "    if (kbInfo.value && kbInfo.value.indexing_strategy) {\n"
        "      kbInfo.value.indexing_strategy.wiki_enabled = target\n"
        "    }\n"
        "    MessagePlugin.success(target\n"
        "      ? '已开启：上传文档会自动生成 wiki'\n"
        "      : '已关闭：上传文档不再自动生成 wiki')\n"
        "  } catch (e) {\n"
        "    wikiAutoEnabled.value = previous\n"
        "    MessagePlugin.error(((e as any) && (e as any).message) || 'wiki 开关切换失败')\n"
        "  } finally {\n"
        "    wikiAutoSaving.value = false\n"
        "  }\n"
        "}",
        "wiki 自动生成开关（script）")

    text = replace_once(
        text,
        "                <t-tooltip content=\"本体要素 + 带语义的关系（bodhi-mcp）\" placement=\"bottom\">\n"
        "                  <span :class=\"['breadcrumb-tab', { active: activeKbTab === 'bodhi-graph' }]\"\n"
        "                    @click=\"activeKbTab = 'bodhi-graph'\">\n"
        "                    本体图谱\n"
        "                  </span>\n"
        "                </t-tooltip>\n"
        "              </template>",
        "                <t-tooltip content=\"本体要素 + 带语义的关系（bodhi-mcp）\" placement=\"bottom\">\n"
        "                  <span :class=\"['breadcrumb-tab', { active: activeKbTab === 'bodhi-graph' }]\"\n"
        "                    @click=\"activeKbTab = 'bodhi-graph'\">\n"
        "                    本体图谱\n"
        "                  </span>\n"
        "                </t-tooltip>\n"
        "                <t-tooltip placement=\"bottom\"\n"
        "                  content=\"关闭 = 上传文档不再自动生成 wiki（省时省 token）；注意："
        "关闭期间 wiki 列表接口会被后端拒绝，界面暂时看不到 wiki 与图谱，需要时请重新打开\">\n"
        "                  <span class=\"wiki-auto-switch\">\n"
        "                    <span class=\"wiki-auto-switch-label\">上传自动生成 wiki</span>\n"
        "                    <t-switch size=\"small\" :value=\"wikiAutoEnabled === true\"\n"
        "                      :loading=\"wikiAutoSaving\" :disabled=\"wikiAutoEnabled === null\"\n"
        "                      @change=\"onWikiAutoToggle\" />\n"
        "                  </span>\n"
        "                </t-tooltip>\n"
        "              </template>",
        "wiki 自动生成开关（模板）")

    if ".wiki-auto-switch {" not in text:
        text = text.replace("</style>", (
            "\n/* bodhi2 v5（§5.2）：wiki 自动生成开关（面包屑右侧） */\n"
            ".wiki-auto-switch {\n"
            "  display: inline-flex;\n"
            "  align-items: center;\n"
            "  gap: 6px;\n"
            "  margin-left: 10px;\n"
            "  font-size: 12px;\n"
            "  color: var(--td-text-color-secondary, #666);\n"
            "}\n"
            ".wiki-auto-switch-label { white-space: nowrap; }\n"
            "</style>"), 1)
        print("  + 应用：wiki 开关样式")

    path.write_text(text, encoding="utf-8")
    print("  KnowledgeBase.vue v5 完成（%d 字节）" % len(text.encode("utf-8")))


def patch_knowledgebase_v6(fe: pathlib.Path) -> None:
    """v6（用户 2026-09-20 §6.1）：本体模型知识库页加**「上传本体文件」**入口。

    命名口径：这里的 v6 = **KnowledgeBase.vue 的第 6 版补丁**（v1 tab、v5 wiki 开关之后），
    与 WikiBrowser.vue 的 v6「编辑页类型下拉」不是一回事。

    - 组件 `BodhiOntologyUpload.vue` 自包含（补丁只插一行标签 + 一个回调）；
    - 入口只挂在本体模型库：`isOntologyKb` 比对构建期常量 `BODHI_ONTOLOGY_KB_ID`（与
      `tools/ke-core/ke_admin.ONTOLOGY_KB` 同源，env `ONTOLOGY_KB_ID` 可覆盖）；
    - 回调 `onOntologyUploaded()` 刷新一次 wiki 状态（上传会整体重投影本体 wiki 页）。
    """
    path = fe / "src" / "views" / "knowledge" / "KnowledgeBase.vue"
    text = path.read_text(encoding="utf-8")
    if "BodhiOntologyUpload" in text:
        print("  - 已应用：KnowledgeBase.vue 上传本体文件入口")
        return

    text = replace_once(
        text,
        "import BodhiGraphTab from './wiki/BodhiGraphTab.vue'",
        "import BodhiGraphTab from './wiki/BodhiGraphTab.vue'\n"
        "import BodhiOntologyUpload from './wiki/BodhiOntologyUpload.vue'",
        "KnowledgeBase.vue import（上传本体文件）")

    kb_const = "const BODHI_ONTOLOGY_KB_ID = '%s'\n" % BODHI_ONTOLOGY_KB
    text = replace_once(
        text,
        "const kbId = computed(() => (route.params as any).kbId as string || '');",
        "const kbId = computed(() => (route.params as any).kbId as string || '');\n"
        "\n"
        "// bodhi2 v6（§6.1）：本体模型知识库页的「上传本体文件」入口。\n"
        "// 只有本体模型库才显示：id 常量是**构建期**注入（与 tools/ke-core/ke_admin.ONTOLOGY_KB\n"
        "// 同源，env ONTOLOGY_KB_ID 可覆盖 —— 见 frontend/patch_frontend.py 的 BODHI_ONTOLOGY_KB）。\n"
        + kb_const +
        "const isOntologyKb = computed(() => !!kbId.value && kbId.value === BODHI_ONTOLOGY_KB_ID)\n"
        "// 上传后本体 wiki 页已整体重投影 → 刷新一次 wiki 状态（面包屑上的 wiki 指示）\n"
        "async function onOntologyUploaded() {\n"
        "  await fetchWikiStatusOnce()\n"
        "}",
        "isOntologyKb / onOntologyUploaded")

    text = replace_once(
        text,
        '                    <span class="wiki-auto-switch-label">上传自动生成 wiki</span>\n'
        '                    <t-switch size="small" :value="wikiAutoEnabled === true"\n'
        '                      :loading="wikiAutoSaving" :disabled="wikiAutoEnabled === null"\n'
        '                      @change="onWikiAutoToggle" />\n'
        '                  </span>\n'
        '                </t-tooltip>\n',
        '                    <span class="wiki-auto-switch-label">上传自动生成 wiki</span>\n'
        '                    <t-switch size="small" :value="wikiAutoEnabled === true"\n'
        '                      :loading="wikiAutoSaving" :disabled="wikiAutoEnabled === null"\n'
        '                      @change="onWikiAutoToggle" />\n'
        '                  </span>\n'
        '                </t-tooltip>\n'
        '                <!-- bodhi2 v6（§6.1）：上传本体 .ttl（同名模块整体替换；只在本体模型库显示） -->\n'
        '                <BodhiOntologyUpload v-if="isOntologyKb" @uploaded="onOntologyUploaded" />\n',
        "上传本体文件入口")

    path.write_text(text, encoding="utf-8")
    print("  KnowledgeBase.vue v6 完成（上传本体文件，KB=%s）" % BODHI_ONTOLOGY_KB)


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

    # 1b) v4：wiki/图谱 tab 与 wiki 主区域**常显**（用户 2026-09-19 需求 1）
    #     上游把整块 UI 包在 `v-if="isWiki"`，而 isWiki 取自 KB 能力位
    #     `indexing_strategy.wiki_enabled`；我们关掉该能力位是为了**阻止上传后自动生成 wiki**
    #     （省时省 token），但界面上的 wiki 树与本体图谱仍需可用，所以这里让它恒为真。
    text = replace_once(
        text,
        "const isWiki = computed(() => !!kbInfo.value?.indexing_strategy?.wiki_enabled)",
        "// bodhi2 v4：wiki / 图谱 / 本体图谱 三个 tab 与 wiki 主区域常显；\n"
        "// KB 的 indexing_strategy.wiki_enabled 只用来控制「上传后是否自动生成 wiki」，\n"
        "// 不再影响界面（否则关掉它就看不到自己的本体页与图谱了）\n"
        "const isWiki = computed(() => true)",
        "isWiki 常显（v4）")

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
    shutil.copy2(HERE / "BodhiRelationsPanel.vue",
                 fe / "src" / "views" / "knowledge" / "wiki" / "BodhiRelationsPanel.vue")
    print("  + src/views/knowledge/wiki/BodhiRelationsPanel.vue")
    shutil.copy2(HERE / "BodhiOntologyUpload.vue",
                 fe / "src" / "views" / "knowledge" / "wiki" / "BodhiOntologyUpload.vue")
    print("  + src/views/knowledge/wiki/BodhiOntologyUpload.vue")

    print("== 2) 补丁 WikiBrowser.vue（两阶段，便于定位失败） ==")
    patch_wikibrowser(fe)
    patch_wikibrowser_part2(fe)
    print("== 3) 补丁 KnowledgeBase.vue ==")
    patch_knowledgebase(fe)
    # v4→v7 链式调用（v4 圆点 → v5 多选删 → v6 类型下拉 → v7 关系面板 → KB 的 wiki 开关）。
    # 放在 patch_knowledgebase 之后：v4 链末尾的 patch_knowledgebase_v5 依赖总部已插入的
    # 「本体图谱」面包屑锚点。
    print("== 4) v5 批次（§5.1 圆点 / §5.2 wiki 开关 / §5.3 多选删 + 需求 1/2） ==")
    patch_wikibrowser_v4(fe)
    print("== 5) v6 批次（2026-09-20 §6.1 本体上传入口） ==")
    # 放在 patch_wikibrowser_v4 之后：v4 链里最后一步 patch_knowledgebase_v5 已经把
    # 「上传自动生成 wiki」开关插进面包屑，v6 的锚点就是它。
    patch_knowledgebase_v6(fe)
    print("== 完成 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
