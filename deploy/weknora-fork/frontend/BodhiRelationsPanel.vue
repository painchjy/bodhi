<template>
  <section class="bodhi-rel-panel">
    <div class="bodhi-rel-head">
      <span class="bodhi-rel-title">本体关系</span>
      <span class="bodhi-rel-sub">{{ relations.type_label || pageType }}</span>
      <span class="bodhi-rel-spacer" />
      <t-button v-if="canEdit" size="small" variant="outline" theme="primary" @click="openCreate">
        新增关系
      </t-button>
      <t-button size="small" variant="text" :loading="loading" @click="reload">刷新</t-button>
    </div>

    <div v-if="error" class="bodhi-rel-error">{{ error }}</div>

    <div v-if="relations.out.length" class="bodhi-rel-block">
      <div class="bodhi-rel-block-title">出边（本页 → 其它要素，可维护）</div>
      <div v-for="(rel, i) in relations.out" :key="'o' + i" class="bodhi-rel-row">
        <t-tag size="small" variant="light">{{ rel.type_label }}</t-tag>
        <code class="bodhi-rel-type">{{ rel.type }}</code>
        <a href="#" class="bodhi-rel-target" @click.prevent="$emit('navigate', rel.target_slug)">
          {{ rel.target_title }}
        </a>
        <span class="bodhi-rel-spacer" />
        <template v-if="canEdit">
          <t-button size="small" variant="text" theme="primary" @click="openEdit(rel)">修改</t-button>
          <t-button size="small" variant="text" theme="danger" @click="remove(rel)">删除</t-button>
        </template>
      </div>
    </div>
    <div v-else-if="!loading" class="bodhi-rel-empty">
      本页暂无出边关系（「新增关系」按本体对象属性建立；可选类型含父类继承）。
    </div>

    <div v-if="relations.in.length" class="bodhi-rel-block">
      <div class="bodhi-rel-block-title">入边（其它要素 → 本页，只读）</div>
      <div v-for="(rel, i) in relations.in" :key="'i' + i" class="bodhi-rel-row bodhi-rel-row--ro">
        <t-tag size="small" variant="light">{{ rel.type_label }}</t-tag>
        <code class="bodhi-rel-type">{{ rel.type }}</code>
        <span class="bodhi-rel-source">{{ rel.source_title }}</span>
        <span class="bodhi-rel-spacer" />
        <span class="bodhi-rel-hint">反向关系需在对方页面里修改</span>
      </div>
    </div>

    <t-dialog v-model:visible="dialogVisible" :header="editing ? '修改本体关系' : '新增本体关系'" width="600px"
      :confirm-btn="{ content: editing ? '保存' : '建立关系', loading: saving }" @confirm="submit">
      <div class="bodhi-rel-form">
        <div class="bodhi-rel-form-row">
          <span class="bodhi-rel-form-label">关系类型</span>
          <t-select v-model="form.relType" :options="typeOptions" :loading="typesLoading" filterable clearable
            placeholder="该类型可用的对象属性（含继承）" class="bodhi-rel-form-field" @change="onRelTypeChange" />
        </div>
        <div v-if="form.relType" class="bodhi-rel-form-row">
          <span class="bodhi-rel-form-label">目标要素</span>
          <t-select v-model="form.targetSlug" :options="targetOptions" :loading="targetsLoading" filterable
            placeholder="range 范围内（含子类）的 wiki 页" class="bodhi-rel-form-field" />
        </div>
        <div v-if="rangeDisplay" class="bodhi-rel-form-tip">可连类型（range 闭包）：{{ rangeDisplay }}</div>
        <div v-if="form.relType && !targetsLoading && !targetOptions.length" class="bodhi-rel-form-tip">
          该 range 下暂无可选页面 —— 需要先有对应类型的 wiki 页。
        </div>
      </div>
    </t-dialog>
  </section>
</template>

<script setup lang="ts">
/**
 * Bodhi 本体关系维护面板（bodhi2 v6，2026-09-19）
 *
 * 为什么单独成一个组件：上游 WikiBrowser.vue 有 5600+ 行，把关系维护塞进去会
 * 让补丁脚本越来越脆。本组件自包含（只依赖 tdesign + fetch），补丁只需插入一行标签。
 *
 * 后端（tools/ontology-mcp/server.py，经 nginx 的 /bodhi/ 反代）：
 *   GET  /bodhi/relations?kb_id=&slug=                  出边 + 入边
 *   GET  /bodhi/ontology/relation-types?page_type=      该类的对象属性（含父类继承）
 *   GET  /bodhi/ontology/targets?kb_id=&slug=&rel_type= range 闭包内的目标页
 *   POST /bodhi/relations/add | update | delete         写正文「## 本体关系」小节（带版本快照）
 *
 * 规则（用户口径）：出边只能在本页改；反向边（入边）必须在对方页面改，这里只读展示。
 */
import { onMounted, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'

const props = defineProps<{
  knowledgeBaseId: string
  slug: string
  pageType: string
  pageTitle?: string
  canEdit?: boolean
}>()

/**
 * 本体读取（类型 / 关系类型）**统一走 bodhi-mcp 的 `/bodhi/*`**：
 * 与本前端同源（nginx `/bodhi/` 反代到 WSL:8765）、读的就是 Neo4j 本体投影，无 CORS 问题。
 * 关系的写入、「按 range 找 wiki 目标页」也走同一条。
 * （本体数据的维护——加载 TTL / 清理模型 / 重投影 wiki——由 bodhi-mcp 的
 *   `/bodhi/ontology/*` 提供，即本前端已反代的同一个服务；原先独立的维护应用
 *   `src/`（:8001）已于 2026-09-20 删除，无需再考虑反代第二端口。）
 */

const emit = defineEmits<{
  (e: 'changed'): void
  (e: 'navigate', slug: string): void
}>()

const relations = ref<any>({ out: [], in: [] })
const loading = ref(false)
const error = ref('')
const dialogVisible = ref(false)
const editing = ref(false)
const saving = ref(false)
const typesLoading = ref(false)
const targetsLoading = ref(false)
const typeOptions = ref<any[]>([])
const targetOptions = ref<any[]>([])
const rangeDisplay = ref('')
const form = ref({ relType: '', targetSlug: '', oldTarget: '' })

async function api(path: string, init?: RequestInit): Promise<any> {
  const res = await fetch(path, init)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`)
  return data
}

function post(path: string, body: any): Promise<any> {
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                     body: JSON.stringify(body) })
}

function qs(params: Record<string, any>): string {
  return new URLSearchParams(params).toString()
}

async function reload() {
  loading.value = true
  error.value = ''
  try {
    relations.value = await api(`/bodhi/relations?${qs({ kb_id: props.knowledgeBaseId, slug: props.slug })}`)
  } catch (e: any) {
    error.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}

async function loadTypes() {
  typesLoading.value = true
  try {
    // bodhi-mcp：按源类（含父类继承）给可用关系类型，读的是 Neo4j 本体投影
    const data = await api(`/bodhi/ontology/relation-types?${qs({ page_type: props.pageType })}`)
    typeOptions.value = (data.relation_types || []).map((r: any) => ({
      label: `${r.label}（${r.prefixed}）${r.inherited_from ? ' · 继承自 ' + r.inherited_from : ''}`,
      value: r.prefixed,
      group: r.module,
      range: r.range_display || '',
    }))
  } catch (e: any) {
    error.value = e?.message || String(e)
  } finally {
    typesLoading.value = false
  }
}

async function loadTargets(relType: string) {
  if (!relType) return
  targetsLoading.value = true
  try {
    const data = await api(`/bodhi/ontology/targets?${qs({ kb_id: props.knowledgeBaseId, slug: props.slug, rel_type: relType })}`)
    targetOptions.value = (data.pages || []).map((p: any) => ({
      label: `${p.title}（${p.type_label}）`, value: p.slug, group: p.page_type,
    }))
  } catch (e: any) {
    MessagePlugin.error(e?.message || '目标页加载失败')
  } finally {
    targetsLoading.value = false
  }
}

function syncRange(relType: string) {
  const hit = typeOptions.value.find(o => o.value === relType)
  rangeDisplay.value = (hit && hit.range) || ''
}

function openCreate() {
  editing.value = false
  form.value = { relType: '', targetSlug: '', oldTarget: '' }
  rangeDisplay.value = ''
  targetOptions.value = []
  // ⚠️ 必须**每次都按当前页的类型**重新拉（此前是 `if (!length)`，导致切换 wiki 后
  //    下拉里还是上一页/第一次新增那条 wiki 的可用关系类型）
  void loadTypes()
  dialogVisible.value = true
}

async function openEdit(rel: any) {
  editing.value = true
  form.value = { relType: rel.type, targetSlug: rel.target_slug, oldTarget: rel.target_slug }
  dialogVisible.value = true
  await loadTypes()
  syncRange(rel.type)
  await loadTargets(rel.type)
}

function onRelTypeChange(value: any) {
  form.value.targetSlug = ''
  syncRange(String(value || ''))
  void loadTargets(String(value || ''))
}

async function submit() {
  if (!form.value.relType) {
    MessagePlugin.warning('请先选择关系类型')
    return
  }
  if (!form.value.targetSlug) {
    MessagePlugin.warning('请选择目标要素')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await post('/bodhi/relations/update', {
        kb_id: props.knowledgeBaseId, slug: props.slug, target_slug: form.value.oldTarget,
        new_rel_type: form.value.relType, new_target_slug: form.value.targetSlug,
      })
    } else {
      await post('/bodhi/relations/add', {
        kb_id: props.knowledgeBaseId, slug: props.slug,
        rel_type: form.value.relType, target_slug: form.value.targetSlug,
      })
    }
    dialogVisible.value = false
    MessagePlugin.success(editing.value ? '关系已修改' : '关系已建立')
    await reload()
    emit('changed')
  } catch (e: any) {
    MessagePlugin.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(rel: any) {
  try {
    await post('/bodhi/relations/delete', {
      kb_id: props.knowledgeBaseId, slug: props.slug,
      target_slug: rel.target_slug, rel_type: rel.type,
    })
    MessagePlugin.success('出边已删除（反向边不受影响）')
    await reload()
    emit('changed')
  } catch (e: any) {
    MessagePlugin.error(e?.message || '删除失败')
  }
}

watch(() => props.slug, () => {
  // 切页时把「上一条 wiki 的类型/目标页候选」清掉，避免残留（下拉必须随页刷新）
  typeOptions.value = []
  targetOptions.value = []
  rangeDisplay.value = ''
  form.value = { relType: '', targetSlug: '', oldTarget: '' }
  void reload()
})
watch(() => props.pageType, () => {
  typeOptions.value = []
  targetOptions.value = []
})
onMounted(() => { void reload() })
</script>

<style scoped>
.bodhi-rel-panel {
  margin: 18px 0 6px;
  border: 1px solid var(--td-component-stroke, #e7e7e7);
  border-radius: 6px;
  padding: 10px 12px;
  background: var(--td-bg-color-container, #fff);
}
.bodhi-rel-head { display: flex; align-items: center; gap: 8px; }
.bodhi-rel-title { font-weight: 600; }
.bodhi-rel-sub { color: var(--td-text-color-secondary, #666); font-size: 12px; }
.bodhi-rel-spacer { flex: 1 1 auto; }
.bodhi-rel-block { margin-top: 8px; }
.bodhi-rel-block-title { font-size: 12px; color: var(--td-text-color-secondary, #666); margin: 6px 0 4px; }
.bodhi-rel-row { display: flex; align-items: center; gap: 6px; padding: 4px 0; flex-wrap: wrap; }
.bodhi-rel-row--ro { opacity: 0.85; }
.bodhi-rel-type { font-size: 11px; color: var(--td-text-color-placeholder, #999); }
.bodhi-rel-target { color: var(--td-brand-color, #0052d9); text-decoration: none; }
.bodhi-rel-target:hover { text-decoration: underline; }
.bodhi-rel-source { color: var(--td-text-color-primary, #333); }
.bodhi-rel-hint { font-size: 11px; color: var(--td-text-color-placeholder, #999); }
.bodhi-rel-empty { color: var(--td-text-color-placeholder, #999); font-size: 12px; margin-top: 8px; }
.bodhi-rel-error { color: var(--td-error-color, #d54941); font-size: 12px; margin-top: 6px; }
.bodhi-rel-form-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.bodhi-rel-form-label { flex: 0 0 68px; color: var(--td-text-color-secondary, #666); font-size: 13px; }
.bodhi-rel-form-field { flex: 1 1 auto; }
.bodhi-rel-form-tip { font-size: 12px; color: var(--td-text-color-placeholder, #999); }
</style>


