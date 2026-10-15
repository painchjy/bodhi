<template>
  <section class="bodhi-attr-panel">
    <div class="bodhi-attr-head">
      <span class="bodhi-attr-title">数据属性</span>
      <span class="bodhi-attr-sub">{{ pageType || data.page_type }}</span>
      <span class="bodhi-attr-spacer" />
      <!-- 2026-10-15 用户口径：**不做可编辑判断**，总是显示（非法键由后端写口拒绝） -->
      <t-button size="small" variant="outline" theme="primary" @click="openEdit">编辑</t-button>
      <t-button size="small" variant="outline" :loading="syncing" @click="syncFromSummary">以摘要为准同步</t-button>
      <t-button size="small" variant="text" :loading="loading" @click="reload">刷新</t-button>
    </div>

    <div v-if="error" class="bodhi-attr-error">{{ error }}</div>
    <div v-if="!editable" class="bodhi-attr-ro">{{ hint }}</div>
    <div v-else-if="drift.length" class="bodhi-attr-warn">
      元数据 / 图 / 正文三处不一致：<code>{{ drift.join('、') }}</code> —— 点「编辑」保存一次即对齐。
    </div>

    <div v-for="(r, i) in rows" :key="i" class="bodhi-attr-row">
      <span class="bodhi-attr-key">{{ r.label }}</span>
      <pre class="bodhi-attr-val">{{ r.value }}</pre>
    </div>
    <div v-if="!rows.length && !loading" class="bodhi-attr-empty">本页暂无数据属性。</div>

    <t-dialog v-model:visible="dlg" header="编辑数据属性" width="760px">
      <div class="bodhi-attr-tip">
        事实源＝数据属性；正文是该值的**只读渲染**。保存会**三处同写**：页面元数据 + 图节点属性 + 正文属性段。
        <b>留空 / 点「清空」＝删除该属性</b>。
        与 WeKnora 原生「定义」框同步的键（<code>skos:definition</code>）：改完会**一起写 <code>summary</code>**
        （即编辑弹窗里的「定义」），并在正文首段保留定义原文。
      </div>
      <div v-for="(f, i) in fields" :key="i" class="bodhi-attr-field">
        <div class="bodhi-attr-field-head">
          <span class="bodhi-attr-field-label">{{ f.label }}</span>
          <code class="bodhi-attr-field-key">{{ f.key }}</code>
          <span class="bodhi-attr-spacer" />
          <t-button size="small" variant="text" theme="danger" @click="clearKey(i)">清空</t-button>
        </div>
        <t-textarea v-model="f.value" :autosize="{ minRows: 1, maxRows: 8 }" placeholder="留空 = 删除该属性" />
      </div>
      <div v-if="previewText" class="bodhi-attr-preview">
        <div class="bodhi-attr-preview-head">预演（未写库）</div>
        <pre>{{ previewText }}</pre>
      </div>
      <template #footer>
        <t-button variant="outline" @click="dlg = false">取消</t-button>
        <t-button theme="default" :loading="previewing" @click="preview">预演</t-button>
        <t-button theme="primary" :loading="saving" :disabled="!previewText" @click="apply">确认写入</t-button>
      </template>
    </t-dialog>
  </section>
</template>

<script setup lang="ts">
/**
 * Bodhi「数据属性」面板（bodhi2 方案 D，2026-10-13）
 *
 * 口径（用户拍板）：**entity 渲染模式**的知识页 —— 正文是**只读渲染**、数据属性是**事实源**；
 * 改值走本面板（三处同写：`page_metadata.ontology.attributes` + 图节点属性 + 正文属性段重渲染）。
 * **document / report 模式以正文为主**：面板只读，并提示「请直接改正文」。
 *
 * 后端（经 nginx 的 `/bodhi/` 反代到 bodhi-mcp）：
 *   GET  /bodhi/page/attributes?kb_id=&slug=   现值 + 模式 + 可编辑项 + 漂移
 *   POST /bodhi/page/attributes                写（`dry_run=true` 只回 diff；本面板**先预演再写**）
 *
 * 与 `BodhiRelationsPanel.vue` 同款：自包含（只依赖 vue + tdesign + fetch），补丁只插一行标签。
 */
import { computed, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'

const props = defineProps<{
  knowledgeBaseId: string
  slug: string
  pageType?: string
  canEdit?: boolean
}>()

const data = ref<any>({})
const loading = ref(false)
const error = ref('')
const dlg = ref(false)
const saving = ref(false)
const syncing = ref(false)
const previewing = ref(false)
const previewText = ref('')
const fields = ref<any[]>([])

async function api(path: string, init?: RequestInit): Promise<any> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...(init || {}),
  })
  const text = await res.text()
  let payload: any = {}
  try { payload = text ? JSON.parse(text) : {} } catch { payload = { error: text } }
  if (!res.ok && payload.error) throw new Error(String(payload.error))
  return payload
}

const mode = computed(() => data.value.mode || '')
const editable = computed(() => !!data.value.editable)
const canEditNow = computed(() => editable.value && props.canEdit !== false)
const hint = computed(() => data.value.hint
  || `本页是 ${mode.value} 模式：以正文为主，数据属性只作结构化摘要`)
const drift = computed<string[]>(() => data.value.drift || [])
const attrs = computed<Record<string, string>>(() => data.value.attributes || {})
const rows = computed(() => Object.entries(attrs.value)
  .map(([k, v]) => ({ label: labelOf(k), value: String(v) })))

function labelOf(key: string): string {
  const hit = (data.value.allowed || []).find((a: string) => a.endsWith(`（${key}）`))
  return hit || key
}

async function reload() {
  if (!props.knowledgeBaseId || !props.slug) return
  loading.value = true
  error.value = ''
  try {
    data.value = await api('/bodhi/page/attributes?kb_id='
      + encodeURIComponent(props.knowledgeBaseId) + '&slug=' + encodeURIComponent(props.slug))
    if (data.value && data.value.ok === false) error.value = String(data.value.error || '读取失败')
  } catch (e: any) {
    error.value = String(e?.message || e)
  } finally {
    loading.value = false
  }
}
watch(() => [props.knowledgeBaseId, props.slug], reload, { immediate: true })

function openEdit() {
  previewText.value = ''
  fields.value = (data.value.allowed || []).map((item: string) => {
    const cut = item.lastIndexOf('（')
    const key = cut >= 0 ? item.slice(cut + 1, item.length - 1) : item
    return { key, label: cut >= 0 ? item.slice(0, cut) : item, value: String(attrs.value[key] ?? '') }
  })
  dlg.value = true
}
function clearKey(i: number) { fields.value[i].value = '' }

function patchPayload(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const f of fields.value) {
    const old = String(attrs.value[f.key] ?? '')
    const now = String(f.value ?? '').trim()
    if (now !== old) out[f.key] = now            // 空串 = 删除该属性
  }
  return out
}

async function preview() {
  const patch = patchPayload()
  if (!Object.keys(patch).length) { MessagePlugin.info('没有改动'); return }
  previewing.value = true
  try {
    const r = await api('/bodhi/page/attributes', {
      method: 'POST',
      body: JSON.stringify({ kb_id: props.knowledgeBaseId, slug: props.slug, patch,
                             actor: '前端·数据属性面板', dry_run: true }),
    })
    previewText.value = r.ok
      ? `将改动的键：${(r.keys_changed || []).join('、') || '（无）'}\n`
        + `正文会变：${r.content_changed}　版本 ${r.before_version} → ${r.after_version}\n\n`
        + `--- 新正文（前 900 字）---\n${r.content_preview || ''}`
      : `被拒：${r.error || ''}`
  } catch (e: any) {
    previewText.value = '预演失败：' + String(e?.message || e)
  } finally { previewing.value = false }
}

async function syncFromSummary() {
  // 「以摘要为准同步」（2026-10-15 用户口径）：把 WeKnora 原生「定义」框（PG `summary`）
  // 写进数据属性的 **元数据 + 图节点**（**不含正文**）。
  syncing.value = true
  try {
    const r = await api('/bodhi/page/attributes', {
      method: 'POST',
      body: JSON.stringify({ kb_id: props.knowledgeBaseId, slug: props.slug, patch: {},
                             sync_from_summary: true, dry_run: false, actor: '前端·以摘要为准同步' }),
    })
    if (!r.ok) throw new Error(String(r.error || '同步失败'))
    MessagePlugin.success(r.applied === false ? '已一致，无需同步' : `已同步（v${r.after_version}）`)
    await reload()
  } catch (e: any) {
    MessagePlugin.error('同步失败：' + String(e?.message || e))
  } finally { syncing.value = false }
}

async function apply() {
  const patch = patchPayload()
  if (!Object.keys(patch).length) { MessagePlugin.info('没有改动'); return }
  saving.value = true
  try {
    const r = await api('/bodhi/page/attributes', {
      method: 'POST',
      body: JSON.stringify({ kb_id: props.knowledgeBaseId, slug: props.slug, patch,
                             actor: '前端·数据属性面板', dry_run: false }),
    })
    if (!r.ok || r.applied === false) throw new Error(String(r.error || r.reason || '未写入'))
    MessagePlugin.success(`已写入（v${r.after_version}）`)
    dlg.value = false
    await reload()
  } catch (e: any) {
    MessagePlugin.error('写入失败：' + String(e?.message || e))
  } finally { saving.value = false }
}
</script>

<style scoped>
.bodhi-attr-panel { margin-top: 12px; border-top: 1px solid var(--td-component-stroke); padding-top: 10px; }
.bodhi-attr-head { display: flex; align-items: center; gap: 8px; }
.bodhi-attr-title { font-weight: 600; }
.bodhi-attr-sub { color: var(--td-text-color-placeholder); font-size: 12px; }
.bodhi-attr-spacer { flex: 1; }
.bodhi-attr-row { display: flex; gap: 10px; padding: 6px 0; border-bottom: 1px dashed var(--td-component-stroke); }
.bodhi-attr-key { min-width: 190px; color: var(--td-text-color-secondary); }
.bodhi-attr-val { margin: 0; white-space: pre-wrap; word-break: break-word; font-family: inherit; }
.bodhi-attr-warn { margin: 8px 0; padding: 6px 8px; background: #fff7e6; border: 1px solid #ffd591; border-radius: 4px; font-size: 13px; }
.bodhi-attr-ro { margin: 8px 0; padding: 6px 8px; background: var(--td-bg-color-container-hover); border-radius: 4px; font-size: 13px; }
.bodhi-attr-error { margin: 8px 0; color: var(--td-error-color); font-size: 13px; }
.bodhi-attr-empty { color: var(--td-text-color-placeholder); font-size: 13px; padding: 6px 0; }
.bodhi-attr-tip { font-size: 13px; color: var(--td-text-color-secondary); margin-bottom: 8px; }
.bodhi-attr-field { margin-bottom: 10px; }
.bodhi-attr-field-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.bodhi-attr-field-label { font-size: 13px; }
.bodhi-attr-field-key { color: var(--td-text-color-placeholder); font-size: 12px; }
.bodhi-attr-preview { margin-top: 8px; background: var(--td-bg-color-container-hover); padding: 8px; border-radius: 4px; }
.bodhi-attr-preview-head { font-size: 12px; color: var(--td-text-color-placeholder); margin-bottom: 4px; }
.bodhi-attr-preview pre { margin: 0; max-height: 260px; overflow: auto; font-size: 12px; white-space: pre-wrap; }
</style>

