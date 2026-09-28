<!--
  bodhi2 v12（2026-09-28）：wiki 阅读页的「跨库上下文」面板（**只读渲染**）。

  口径（用户拍板，见 docs/context-mapping-plan.md §13.11/§13.12）：
    · 领域库**不写 uuid**、**不互相引用**；关联靠"按 slug 同名查询"「企业共享概念模型」里的概念页；
    · 跨域关系**必须经企业概念页转换** —— 本面板只**展示**（企业标准概念 + 各领域映射表 + 同名领域页 + 告警），
      **不提供跨库直跳**（那等于跨库引用；要跳请复制对端 slug 到对应库查）；
    · 映射关系的**事实源**是概念页正文的「各领域映射」表；`state/context_map/*.json` 只是缓存。

  数据源：`GET /bodhi/context/page?kb_id=&slug=`（同源反代，只读）。
  挂载点见 patch_frontend.py 的 `patch_wikibrowser_v12_context_panel`（与关系面板同级，编辑正文时隐藏）。
-->
<template>
  <div class="bodhi-ctx-panel">
    <div class="bodhi-ctx-head" @click="open = !open">
      <span class="bodhi-ctx-title">跨库上下文（企业共享概念模型）</span>
      <span class="bodhi-ctx-badge" :class="badgeClass">{{ badgeText }}</span>
      <span class="bodhi-ctx-toggle">{{ open ? '收起' : '展开' }}</span>
    </div>

    <div v-if="open" class="bodhi-ctx-body">
      <div v-if="loading" class="bodhi-ctx-dim">加载中…</div>
      <div v-else-if="error" class="bodhi-ctx-err">取跨库上下文失败：{{ error }}</div>
      <template v-else-if="data">
        <div v-for="w in (data.warnings || [])" :key="w.kind" class="bodhi-ctx-warn" :class="'sev-' + w.severity">
          <b>{{ w.kind }}</b>：{{ w.detail }}
        </div>

        <div class="bodhi-ctx-block">
          <div class="bodhi-ctx-label">企业标准概念</div>
          <template v-if="data.concept_page && data.concept_page.exists">
            <div class="bodhi-ctx-row">
              <code>{{ data.concept_page.slug }}</code>
              <span class="bodhi-ctx-dim">（{{ data.concept_page.page_type }}，v{{ data.concept_page.version }}）</span>
            </div>
            <div class="bodhi-ctx-def">{{ data.concept_page.standard_definition || '（标准定义待补）' }}</div>
          </template>
          <div v-else class="bodhi-ctx-dim">
            {{ (data.concept_page && data.concept_page.note) || '概念库里还没有同名页（按 slug 同名查不到）' }}
          </div>
        </div>

        <div class="bodhi-ctx-block">
          <div class="bodhi-ctx-label">各领域映射（事实源＝概念页正文）</div>
          <table class="bodhi-ctx-table">
            <thead>
              <tr><th>上下文</th><th>页 slug</th><th>类</th><th>版本</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="p in domainPeers" :key="p.kb + '|' + p.slug">
                <td>{{ p.kb_name }}</td>
                <td><code>{{ p.slug }}</code></td>
                <td>{{ p.page_type }}</td>
                <td>v{{ p.version }}</td>
                <td>{{ mappingOf(p.slug) }}</td>
              </tr>
              <tr v-if="!domainPeers.length">
                <td colspan="5" class="bodhi-ctx-dim">本库之外没有同名页（无需跨库映射）</td>
              </tr>
            </tbody>
          </table>
          <div class="bodhi-ctx-note">
            领域库之间不互相引用；跨域关系一律<b>经企业概念页转换</b>。
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

const props = defineProps<{ knowledgeBaseId?: string; slug?: string }>()
const open = ref(true)
const loading = ref(false)
const error = ref('')
const data = ref<any>(null)

const domainPeers = computed<any[]>(() =>
  ((data.value && data.value.peers) || []).filter((p: any) => p.role === 'domain'))

const badgeText = computed(() => {
  if (loading.value) return '加载中'
  if (error.value) return '取数失败'
  if (!data.value) return '—'
  const c = data.value.concept_page || {}
  const n = domainPeers.value.length
  if (c.exists) return `企业标准概念 v${c.version}｜同名领域页 ${n}`
  return n ? `尚未建企业标准概念（同名领域页 ${n}）` : '仅本库'
})
const badgeClass = computed(() => {
  const c = (data.value && data.value.concept_page) || {}
  if (error.value) return 'is-bad'
  if (c.exists) return 'is-ok'
  return domainPeers.value.length > 1 ? 'is-warn' : ''
})

/** 「各领域映射」表里该 slug 的结论（事实源是概念页正文；这里只读渲染）。 */
function mappingOf(slug: string): string {
  const rows = (data.value && data.value.concept_page && data.value.concept_page.mapping_rows) || []
  const hit = rows.find((r: any) => String(r['页 slug'] || '').replace(/`/g, '').trim() === slug)
  return (hit && String(hit['结论'] || '')) || '—'
}

async function load(): Promise<void> {
  if (!props.slug) return
  loading.value = true
  error.value = ''
  try {
    const qs = `kb_id=${encodeURIComponent(props.knowledgeBaseId || '')}` +
      `&slug=${encodeURIComponent(props.slug)}`
    const res = await fetch('/bodhi/context/page?' + qs)
    const body = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error((body && body.error) || ('HTTP ' + res.status))
    data.value = body
  } catch (e: any) {
    error.value = (e && e.message) || String(e)
  } finally {
    loading.value = false
  }
}

watch(() => [props.knowledgeBaseId, props.slug], () => { void load() })
onMounted(() => { void load() })
defineExpose({ reload: load })
</script>

<style scoped>
.bodhi-ctx-panel {
  margin: 12px 0;
  border: 1px solid var(--td-component-stroke, #e7e7e7);
  border-radius: 6px;
  background: var(--td-bg-color-container, #fff);
  font-size: 13px;
}
.bodhi-ctx-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  cursor: pointer;
  border-bottom: 1px solid var(--td-component-stroke, #e7e7e7);
}
.bodhi-ctx-title { font-weight: 600; }
.bodhi-ctx-badge {
  padding: 0 6px;
  border-radius: 8px;
  background: var(--td-bg-color-secondarycontainer, #f3f3f3);
  color: var(--td-text-color-secondary, #666);
}
.bodhi-ctx-badge.is-ok { background: #e8f5e9; color: #2e7d32; }
.bodhi-ctx-badge.is-warn { background: #fff7e6; color: #b26a00; }
.bodhi-ctx-badge.is-bad { background: #fdecea; color: #c0392b; }
.bodhi-ctx-toggle { margin-left: auto; color: var(--td-text-color-secondary, #666); }
.bodhi-ctx-body { padding: 8px 10px; }
.bodhi-ctx-block { margin-bottom: 10px; }
.bodhi-ctx-label { font-weight: 600; margin-bottom: 4px; }
.bodhi-ctx-row { display: flex; gap: 6px; align-items: baseline; flex-wrap: wrap; }
.bodhi-ctx-def { margin-top: 4px; white-space: pre-wrap; }
.bodhi-ctx-note { margin-top: 4px; color: var(--td-text-color-secondary, #666); }
.bodhi-ctx-dim { color: var(--td-text-color-placeholder, #999); }
.bodhi-ctx-err { color: #c0392b; }
.bodhi-ctx-warn { padding: 4px 8px; margin-bottom: 6px; border-radius: 4px; background: #f7f7f7; }
.bodhi-ctx-warn.sev-high { background: #fdecea; color: #c0392b; }
.bodhi-ctx-warn.sev-medium { background: #fff7e6; color: #b26a00; }
.bodhi-ctx-table { width: 100%; border-collapse: collapse; }
.bodhi-ctx-table th, .bodhi-ctx-table td {
  border: 1px solid var(--td-component-stroke, #e7e7e7);
  padding: 3px 6px;
  text-align: left;
  vertical-align: top;
}
.bodhi-ctx-table th { background: var(--td-bg-color-secondarycontainer, #f3f3f3); font-weight: 600; }
</style>
