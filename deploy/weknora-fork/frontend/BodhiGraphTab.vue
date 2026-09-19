<template>
  <div class="bodhi-graph-tab">
    <div class="bodhi-toolbar">
      <span class="bodhi-title">Bodhi 本体图谱</span>
      <t-select v-model="model" :options="modelOptions" size="small" style="width: 170px" @change="refresh" />
      <t-button size="small" variant="outline" @click="refresh">刷新</t-button>
      <t-button size="small" variant="outline" @click="openInWindow">在新窗口打开</t-button>
      <span class="bodhi-hint">
        节点＝本体要素（按本体类着色）｜边＝本体关系（带关系语义与方向）
      </span>
      <span class="bodhi-grow" />
      <t-tag v-if="pending.length" theme="warning" variant="light-outline">
        {{ pending.length }} 条待确认合并
      </t-tag>
    </div>

    <div v-if="pending.length" class="bodhi-pending">
      <div class="bodhi-pending-head">
        疑似与存量相同（相似度介于两个阈值之间），请裁决：
      </div>
      <div v-for="item in pending" :key="item.slug" class="bodhi-pending-row">
        <span class="bodhi-pending-title">{{ item.title }}</span>
        <span class="bodhi-pending-meta">
          候选页 {{ item.candidate }} ｜ 相似度 {{ item.similarity }}
        </span>
        <t-button size="small" theme="primary" :loading="busy === item.slug" @click="resolve(item, 'merge')">
          合并到候选页
        </t-button>
        <t-button size="small" variant="outline" :loading="busy === item.slug" @click="resolve(item, 'create')">
          作为新页新增
        </t-button>
      </div>
    </div>

    <iframe :key="frameKey" class="bodhi-frame" :src="src" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { ONTOLOGY_MODULES } from '@/utils/ontologyTypes'

const props = defineProps<{ knowledgeBaseId: string }>()

const model = ref('')
const frameKey = ref(0)
const busy = ref('')
const pending = ref<Array<{ slug: string; title: string; candidate: string; similarity: number }>>([])

const modelOptions = [
  { label: '全部模型', value: '' },
  ...ONTOLOGY_MODULES.map(m => ({ label: m.label, value: m.key })),
]

const src = computed(() => {
  const base = `/bodhi/view?kb_id=${encodeURIComponent(props.knowledgeBaseId)}`
  return model.value ? `${base}&model=${encodeURIComponent(model.value)}` : base
})

function refresh() {
  frameKey.value += 1
  void loadPending()
}

function openInWindow() {
  window.open(src.value, '_blank')
}

async function loadPending() {
  try {
    const url = `/bodhi/pending?kb_id=${encodeURIComponent(props.knowledgeBaseId)}`
    const res = await fetch(url, { headers: { Accept: 'application/json' } })
    const data = await res.json()
    pending.value = (data.items || []).map((i: any) => ({
      slug: i.pending_slug,
      title: i.title,
      candidate: i.candidate_slug,
      similarity: i.similarity,
    }))
  } catch (e) {
    // 服务不可用时静默（图谱 iframe 自己会显示错误）
    pending.value = []
  }
}

async function resolve(item: { slug: string; title: string }, action: 'merge' | 'create') {
  busy.value = item.slug
  try {
    const url = `/bodhi/resolve?kb_id=${encodeURIComponent(props.knowledgeBaseId)}`
      + `&slug=${encodeURIComponent(item.slug)}&action=${action}`
    const res = await fetch(url, { headers: { Accept: 'application/json' } })
    const data = await res.json()
    if (data.error) throw new Error(data.error)
    MessagePlugin.success(action === 'merge' ? '已合并到候选页（原页生成新版本，可回退）' : '已作为新页新增')
    refresh()
  } catch (e: any) {
    MessagePlugin.error(`裁决失败：${e?.message || e}`)
  } finally {
    busy.value = ''
  }
}

onMounted(loadPending)
</script>

<style scoped>
.bodhi-graph-tab {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.bodhi-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--td-component-stroke, #e7e7e7);
  flex: 0 0 auto;
}

.bodhi-title {
  font-weight: 600;
}

.bodhi-hint {
  color: var(--td-text-color-placeholder, #999);
  font-size: 12px;
}

.bodhi-grow {
  flex: 1;
}

.bodhi-pending {
  flex: 0 0 auto;
  padding: 8px 12px;
  background: var(--td-warning-color-1, #fef3e6);
  border-bottom: 1px solid var(--td-component-stroke, #e7e7e7);
}

.bodhi-pending-head {
  font-size: 12px;
  margin-bottom: 6px;
}

.bodhi-pending-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 3px 0;
  font-size: 12px;
}

.bodhi-pending-title {
  font-weight: 500;
  max-width: 32ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bodhi-pending-meta {
  color: var(--td-text-color-placeholder, #999);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bodhi-frame {
  flex: 1;
  width: 100%;
  border: 0;
  min-height: 0;
}
</style>
