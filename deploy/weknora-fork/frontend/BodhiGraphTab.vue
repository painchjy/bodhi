<template>
  <div class="bodhi-graph-tab">
    <div class="bodhi-toolbar">
      <span class="bodhi-title">Bodhi 本体图谱</span>
      <t-select v-model="model" :options="modelOptions" size="small" style="width: 170px" @change="refresh" />
      <t-button size="small" variant="outline" @click="refresh">刷新</t-button>
      <t-button size="small" variant="outline" @click="openInWindow">在新窗口打开</t-button>
      <span class="bodhi-hint">
        节点＝本体要素（按本体类着色）｜边＝本体关系（带关系语义与方向）<br />
        待确认合并（疑似与存量相同）不在这里——它在左侧 wiki 列表的「待确认合并」tab 里裁决
      </span>
    </div>

    <iframe :key="frameKey" class="bodhi-frame" :src="src" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ONTOLOGY_MODULES } from '@/utils/ontologyTypes'

const props = defineProps<{ knowledgeBaseId: string }>()

const model = ref('')
const frameKey = ref(0)

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
}

function openInWindow() {
  window.open(src.value, '_blank')
}
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
