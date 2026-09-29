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
          <div class="bodhi-ctx-label">
            企业标准概念
            <span v-if="conceptState" class="bodhi-ctx-state" :class="'st-' + conceptState">{{ stateLabel }}</span>
          </div>
          <template v-if="data.concept_page && data.concept_page.exists">
            <div class="bodhi-ctx-row">
              <code>{{ data.concept_page.slug }}</code>
              <span class="bodhi-ctx-dim">（{{ data.concept_page.page_type }}，v{{ data.concept_page.version }}）</span>
              <button class="bodhi-ctx-btn" @click="openConceptPage">在概念库里打开</button>
              <button class="bodhi-ctx-btn" @click="copyConceptSlug">复制 slug</button>
            </div>
            <div v-if="data.concept_page.reviewed_by" class="bodhi-ctx-dim">
              评审：{{ data.concept_page.reviewed_by }} · {{ data.concept_page.reviewed_at }}
            </div>
            <div class="bodhi-ctx-def">{{ data.concept_page.standard_definition || '（标准定义待补）' }}</div>
          </template>
          <div v-else class="bodhi-ctx-dim">
            {{ (data.concept_page && data.concept_page.note) || '概念库里还没有同名页（按 slug 同名查不到）' }}
          </div>
        </div>

        <div v-if="auth" class="bodhi-ctx-block">
          <div class="bodhi-ctx-label">
            权威 / 副本（同义知识：认定一个领域为权威，其余只读）
            <span v-if="myRole" class="bodhi-ctx-state"
                  :class="myRole === 'master' ? 'st-approved' : 'st-reviewed'">
              本库：{{ myRole === 'master' ? '权威' : '副本·只读' }}
            </span>
          </div>
          <div class="bodhi-ctx-row">
            <span>权威</span>
            <template v-if="auth.master && auth.master.kb">
              <b>{{ auth.master.kb_name }}</b>
              <code>{{ auth.master.slug }}</code>
              <span v-if="auth.master_version" class="bodhi-ctx-dim">v{{ auth.master_version }}</span>
            </template>
            <span v-else class="bodhi-ctx-dim">
              （还没认定权威 → 由有写权限的人在概念页执行 `authority-decide`）
            </span>
          </div>
          <table class="bodhi-ctx-table">
            <thead>
              <tr><th>上下文</th><th>角色</th><th>版本</th><th>同步状态</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="r in (auth.replicas || [])" :key="r.kb + '|' + r.slug">
                <td>{{ r.kb_name }}{{ r.kb === myKb ? '（本库）' : '' }}</td>
                <td>{{ r.kb === myKb ? '副本·只读' : '副本' }}</td>
                <td>v{{ r.version }}</td>
                <td>
                  <span class="bodhi-ctx-state" :class="'st-' + r.state">{{ stateLabelOf(r.state) }}</span>
                  <span v-if="r.synced_version" class="bodhi-ctx-dim">已同步 v{{ r.synced_version }}</span>
                </td>
                <td>
                  <button v-if="r.kb === myKb && myRole === 'replica'" class="bodhi-ctx-btn primary"
                          @click="askPull">
                    {{ pullPreview ? '取消' : '从权威复制' }}
                  </button>
                  <span v-else class="bodhi-ctx-dim">—</span>
                </td>
              </tr>
              <tr v-if="!(auth.replicas || []).length">
                <td colspan="5" class="bodhi-ctx-dim">还没有副本（其它领域还没从权威复制过）</td>
              </tr>
            </tbody>
          </table>

          <div v-if="pullPreview" class="bodhi-ctx-warn sev-medium">
            <b>从权威复制的影响面</b>：{{ myKbName }} 的 <code>{{ auth.slug }}</code>
            v{{ pullPreview.replica_version }} → <b>v{{ (pullPreview.replica_version || 0) + 1 }}</b>
            （权威 v{{ pullPreview.master_version }}）—— 正文会被权威**覆盖**，本页版本 +1（可回退）；
            风险：{{ (pullPreview.required_risks || []).join('、') }}
            <div style="margin-top: 6px; display: flex; gap: 6px;">
              <button class="bodhi-ctx-btn primary" :disabled="busy" @click="doPull">
                {{ busy ? '复制中…' : '确认复制' }}
              </button>
              <button class="bodhi-ctx-btn" :disabled="busy" @click="pullPreview = null">取消</button>
            </div>
          </div>
          <div v-if="pullError" class="bodhi-ctx-err">{{ pullError }}</div>
          <div v-if="pullDone" class="bodhi-ctx-note">{{ pullDone }}</div>
          <div class="bodhi-ctx-note">
            权威 = **唯一可编辑**的领域；副本正文只能由 <code>authority_pull</code> 从权威复制（服务端拒写，见
            <code>replica_guard</code>）。权威前进**不自动推送**，由人决定何时升级。
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
import { useRouter } from 'vue-router'

const props = defineProps<{ knowledgeBaseId?: string; slug?: string }>()
const router = useRouter()
const open = ref(true)
const loading = ref(false)
const error = ref('')
const data = ref<any>(null)

/** 治理状态（draft / reviewed / approved；空=未标）—— 事实源在概念页 page_metadata.concept.state */
const conceptState = computed(() => String((data.value && data.value.concept_page &&
  data.value.concept_page.exists && data.value.concept_page.state) || ''))
const stateLabel = computed(() => ({ draft: '草稿·待评审', reviewed: '已评审', approved: '企业标准·已批准' }[conceptState.value] || ''))

/** 打开概念库里的那一页（**只是 UI 导航到治理库**；领域库之间仍不互相引用）。 */
function openConceptPage(): void {
  const c = (data.value && data.value.concept_page) || {}
  if (!c.kb || !c.slug) return
  void router.push(`/platform/knowledge-bases/${c.kb}?slug=${encodeURIComponent(c.slug)}`)
}

/** 复制概念页 slug（给"到概念库里粘贴打开"或贴到工单里用）。 */
async function copyConceptSlug(): Promise<void> {
  const c = (data.value && data.value.concept_page) || {}
  if (!c.slug) return
  try {
    await navigator.clipboard.writeText(String(c.slug))
  } catch (e) {
    /* 剪贴板不可用（非 https / 无权限）→ 静默：用户可手选文本 */
  }
}

const domainPeers = computed<any[]>(() =>
  ((data.value && data.value.peers) || []).filter((p: any) => p.role === 'domain'))

/* ------------------------------------------------------------------
   权威 / 副本（2026-09-29 口径）：
     · 事实源 = 概念页正文「## 权威与副本」表；`GET /bodhi/context/authority?slug=` 是**只读**视图
       （含 master、各副本同步状态、pull 预览 ticket）；
     · **写只写本库**：`POST /bodhi/context/authority/pull {kb, slug, apply}` —— 服务端按
       `X-Bodhi-Tenant`/`BODHI_TENANT_ID` 判写权限，副本页只能这样改（本地写被 replica_guard 拒）。
   ------------------------------------------------------------------ */
const auth = ref<any>(null)
const myKb = computed(() => String(props.knowledgeBaseId || ''))
const myRole = computed(() => {
  const a = auth.value || {}
  if (a.master && a.master.kb && String(a.master.kb) === myKb.value) return 'master'
  const hit = (a.replicas || []).find((r: any) => String(r.kb || '') === myKb.value)
  return hit ? 'replica' : ''
})
const replicaState = computed(() => {
  const hit = ((auth.value || {}).replicas || []).find((r: any) => String(r.kb || '') === myKb.value)
  return String((hit || {}).state || '')
})
const myKbName = computed(() => {
  const a = auth.value || {}
  if (myRole.value === 'master') return String((a.master || {}).kb_name || '本库')
  const hit = (a.replicas || []).find((r: any) => String(r.kb || '') === myKb.value)
  return String((hit || {}).kb_name || '本库')
})
function stateLabelOf(state: string): string {
  const labels: any = { in_sync: '已同步', outdated: '落后于权威', local_drift: '本地漂移', unbound: '未绑定' }
  return labels[String(state || '')] || (state ? String(state) : '—')
}

async function loadAuth(): Promise<void> {
  if (!props.slug) return
  try {
    const res = await fetch('/bodhi/context/authority?slug=' + encodeURIComponent(props.slug))
    const body = await res.json().catch(() => ({}))
    auth.value = res.ok && !body.error ? body : null
  } catch (e) {
    auth.value = null      // 只读增强：取不到就不显示本块（不影响主面板）
  }
}

const pullPreview = ref<any>(null)
const pullError = ref('')
const pullDone = ref('')
const busy = ref(false)

/** 「从权威复制」第一段：**只预览**（`apply=false`），拿影响面 + ticket，用户确认后才写。 */
async function askPull(): Promise<void> {
  if (pullPreview.value) { pullPreview.value = null; return }
  pullError.value = ''
  pullDone.value = ''
  try {
    const res = await fetch('/bodhi/context/authority/pull', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kb: myKb.value, slug: props.slug, apply: false }),
    })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error((body && body.error) || ('HTTP ' + res.status))
    if (body && body.error) throw new Error(String(body.error))
    pullPreview.value = body
  } catch (e: any) {
    pullError.value = (e && e.message) || String(e)
  }
}

/** 第二段：apply=true（ticket + 风险确认一致；**只写本库**的副本页）。 */
async function doPull(): Promise<void> {
  const p = pullPreview.value || {}
  busy.value = true
  pullError.value = ''
  try {
    const res = await fetch('/bodhi/context/authority/pull', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kb: myKb.value, slug: props.slug, ticket: p.ticket,
                             acknowledge_risks: p.required_risks || [],
                             actor: 'ui:context-panel', apply: true }),
    })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error((body && body.error) || ('HTTP ' + res.status))
    if (body && body.error) throw new Error(String(body.error))
    pullPreview.value = null
    pullDone.value = '已从权威复制：v' + body.replica_version_before + ' → v' +
      body.replica_version_after + '（权威 v' + body.synced_version + '）'
    await load()
    void loadAuth()
  } catch (e: any) {
    pullError.value = (e && e.message) || String(e)
  } finally {
    busy.value = false
  }
}

const badgeText = computed(() => {
  if (loading.value) return '加载中'
  if (error.value) return '取数失败'
  if (!data.value) return '—'
  if (myRole.value === 'master') return `权威（其余领域只能从此复制）｜同名领域页 ${domainPeers.value.length}`
  if (myRole.value === 'replica') {
    const m = (auth.value && auth.value.master) || {}
    return `副本·只读（${stateLabelOf(replicaState.value)}）｜权威 ${m.kb_name || ''}`
  }
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
  await loadAuth()          // 权威/副本分工（只读增强；取不到不影响主面板）
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
.bodhi-ctx-state {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 8px;
  font-weight: 400;
  background: var(--td-bg-color-secondarycontainer, #f3f3f3);
  color: var(--td-text-color-secondary, #666);
}
.bodhi-ctx-state.st-draft { background: #fff7e6; color: #b26a00; }
.bodhi-ctx-state.st-in_sync { background: #e8f5e9; color: #2e7d32; }
.bodhi-ctx-state.st-outdated { background: #fff7e6; color: #b26a00; }
.bodhi-ctx-state.st-local_drift { background: #fdecea; color: #c0392b; }
.bodhi-ctx-state.st-unbound { background: #f3f3f3; color: #666; }
.bodhi-ctx-state.st-reviewed { background: #e8f0fe; color: #1a56db; }
.bodhi-ctx-state.st-approved { background: #e8f5e9; color: #2e7d32; }
.bodhi-ctx-btn {
  border: 1px solid var(--td-component-stroke, #e7e7e7);
  background: var(--td-bg-color-container, #fff);
  border-radius: 4px;
  padding: 1px 8px;
  font-size: 12px;
  cursor: pointer;
}
.bodhi-ctx-btn:hover {
  border-color: var(--td-brand-color, #0052d9);
  color: var(--td-brand-color, #0052d9);
}
.bodhi-ctx-btn.primary { border-color: var(--td-brand-color, #0052d9); color: var(--td-brand-color, #0052d9); }
.bodhi-ctx-btn[disabled] { opacity: 0.5; cursor: default; }
</style>
