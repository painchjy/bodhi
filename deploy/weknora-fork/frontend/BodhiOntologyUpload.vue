<template>
  <span class="bodhi-onto-upload">
    <t-tooltip placement="bottom"
      content="上传本体 .ttl：同名模块整体替换（先级联删除依赖它的下游模块）；图谱 / 关系类型接口 / wiki 页同步更新">
      <t-button size="small" variant="outline" theme="primary" :loading="busy" @click="openPicker">
        上传本体文件
      </t-button>
    </t-tooltip>
    <!-- 真 input 藏起来：点按钮才触发；选完文件即进入对话框（不用 multipart，发文本） -->
    <input ref="fileInput" class="bodhi-onto-file" type="file" accept=".ttl,.turtle,text/turtle"
      @change="onFileChange" />

    <t-dialog v-model:visible="visible" :header="header" width="720px" :close-on-overlay-click="false"
      :confirm-btn="confirmBtn" :cancel-btn="cancelBtn" @confirm="onConfirm" @cancel="close"
      @close="close">

      <!-- ① 选文件 + 模块名（可改） -->
      <div v-if="stage === 'form'" class="bodhi-onto-form">
        <div class="bodhi-onto-row">
          <span class="bodhi-onto-label">文件</span>
          <span class="bodhi-onto-value">{{ fileName }}</span>
          <span class="bodhi-onto-dim">{{ sizeText }}</span>
        </div>
        <div class="bodhi-onto-row">
          <span class="bodhi-onto-label">模块名</span>
          <t-input v-model="moduleId" size="small" class="bodhi-onto-field" clearable
            placeholder="模块 key（同名 = 整体替换）" />
        </div>
        <div v-if="ttlNamespace" class="bodhi-onto-row">
          <span class="bodhi-onto-label">命名空间</span>
          <span class="bodhi-onto-value bodhi-onto-dim">{{ ttlNamespace }}</span>
        </div>
        <div v-if="ttlIri" class="bodhi-onto-row">
          <span class="bodhi-onto-label">本体 IRI</span>
          <span class="bodhi-onto-value bodhi-onto-dim">{{ ttlIri }}</span>
        </div>
        <div class="bodhi-onto-row">
          <span class="bodhi-onto-label">编译生效</span>
          <t-checkbox :checked="compileAfter" @change="onCompileAfterChange">
            <b>上传后编译并生效</b>（默认开）：把 TTL 落进本体真源
            <code>ontology/extensions/</code> 并自动编译 → 类型校验 / 类型下拉 / 本体库 wiki 都认得新类
          </t-checkbox>
        </div>
        <div v-if="!compileAfter" class="bodhi-onto-warn">
          已关闭「编译生效」：本次只更新图谱（老行为）。新类在智能体校验里会被判「本体里没有这个类」，
          需要时再打开本开关重传，或让运维跑 <code>ke_admin.py repair</code>。
        </div>
        <div class="bodhi-onto-row">
          <span class="bodhi-onto-label">同步 wiki</span>
          <t-checkbox :checked="projectWiki" @change="onProjectWikiChange">
            重投影本体 wiki 页（不勾 = 只更新图谱与接口）
          </t-checkbox>
        </div>
        <div v-if="moduleId && moduleId !== inferredModule" class="bodhi-onto-warn">
          模块名已改（TTL 推断值：{{ inferredModule }}）：TTL 的命名空间不会跟着变，
          库里该命名空间的节点会被改成新模块名 —— 只有确实要改名时才这么填。
        </div>
        <div class="bodhi-onto-note">
          模块名只是预填（取 TTL 里第一个与本体 IRI 同命名空间的前缀，否则取 owl:Ontology IRI 末段），
          可改；真正的模块名 / 命名空间 / 前缀一律以 <b>TTL 自身声明</b>为准。
          同名上传 = <b>整体替换</b>：先级联删下游，再解析入库（不写编译产物）。
        </div>
      </div>

      <!-- ② 级联删除确认（只在有下游模块时出现） -->
      <div v-else-if="stage === 'confirm'" class="bodhi-onto-form">
        <div class="bodhi-onto-warn">
          将级联删除下游模块：{{ dependentModules.join('、') || '（无）' }}（按此顺序：{{ purgeOrder.join(' → ') }}）；
          依赖者需重新上传才能恢复。确认替换？
        </div>
        <ol class="bodhi-onto-order">
          <li v-for="m in purgeOrder" :key="m">
            <code>{{ m }}</code>
            <span v-if="m === moduleId" class="bodhi-onto-dim">（本次上传的模块，随后重建）</span>
          </li>
        </ol>
        <div class="bodhi-onto-note">
          级联删除 = B 案：<b>只删不重建</b>。被删掉的模块要各自重新上传（依赖顺序 bmm → ea → 扩展模块）。
        </div>
      </div>

      <!-- ③ 结果报告 -->
      <div v-else class="bodhi-onto-report">
        <div class="bodhi-onto-report-head">
          <t-tag size="small" theme="success" variant="light">已导入</t-tag>
          <b>{{ report?.module }}</b>
          <span class="bodhi-onto-dim">
            前缀 {{ report?.prefix || '（默认 :）' }} · {{ report?.ontology_iri }}
          </span>
        </div>
        <div class="bodhi-onto-grid">
          <div>本模块类：<b>{{ report?.module_classes }}</b>（库内已定义共 {{ report?.classes }}）</div>
          <div>本模块属性：<b>{{ report?.module_properties }}</b></div>
          <div>执行语句：<b>{{ report?.cypher_statements }}</b>（跳过非本模块 {{ report?.statements_skipped }}）</div>
          <div>占位跳过：<b>{{ (report?.placeholders_skipped || []).length }}</b></div>
        </div>

        <div v-if="purgeDeleted.length" class="bodhi-onto-block">
          <div class="bodhi-onto-block-title">级联删除（{{ purgeDeleted.length }} 个模块）</div>
          <div v-for="d in purgeDeleted" :key="d.model" class="bodhi-onto-dim">
            {{ d.model }}：图节点 {{ d.neo4j_nodes_deleted }} / wiki 页 {{ d.wiki_pages_deleted }}
          </div>
        </div>

        <div v-if="(report?.placeholders_skipped || []).length" class="bodhi-onto-block">
          <div class="bodhi-onto-block-title">跳过的外部占位（不造孤立节点）</div>
          <code class="bodhi-onto-codes">{{ (report?.placeholders_skipped || []).join('、') }}</code>
        </div>

        <div v-if="(report?.cross_refs || []).length" class="bodhi-onto-block">
          <div class="bodhi-onto-block-title">交叉引用（引用到别的模块的类）</div>
          <div v-for="(r, i) in report.cross_refs" :key="i" class="bodhi-onto-ref"
            :class="{ 'bodhi-onto-ref--bad': !r.defined }">
            <t-tag size="small" :theme="r.defined ? 'success' : 'danger'" variant="light">
              {{ r.defined ? '已定义' : '未定义' }}
            </t-tag>
            <code>{{ r.iri }}</code>
            <span class="bodhi-onto-dim">{{ refKind(r.kind) }} · {{ r.module }}</span>
          </div>
        </div>

        <div v-if="report?.compiled" class="bodhi-onto-block">
          <div class="bodhi-onto-block-title">本次编译（产物已更新）</div>
          <div class="bodhi-onto-dim">
            真源：<code>{{ report?.source?.file || '-' }}</code>
            <span v-if="(report?.source?.injected || []).length">
              · 自动补了：{{ (report.source.injected || []).join('、') }}</span>
          </div>
          <div class="bodhi-onto-dim">
            模块 {{ report.compiled.totals_before?.modules }} → {{ report.compiled.totals_after?.modules }}
            · 类 {{ report.compiled.totals_before?.classes }} → {{ report.compiled.totals_after?.classes }}
            · 对象属性 {{ report.compiled.totals_before?.object_properties }} → {{ report.compiled.totals_after?.object_properties }}
            · 数据属性 {{ report.compiled.totals_before?.datatype_properties }} → {{ report.compiled.totals_after?.datatype_properties }}
          </div>
          <code class="bodhi-onto-codes">模块：{{ (report.compiled.modules || []).join('、') }}</code>
          <div v-if="(report.compiled.modules_added || []).length" class="bodhi-onto-note">
            新增模块：{{ (report.compiled.modules_added || []).join('、') }}
          </div>
          <div class="bodhi-onto-dim">
            投影回放 {{ report?.apply?.statements ?? '-' }} 条语句 / 类 {{ report?.apply?.classes ?? '-' }}
          </div>
        </div>

        <div v-if="report?.hint" class="bodhi-onto-note">{{ report.hint }}</div>
        <div v-if="report?.warning" class="bodhi-onto-warn">{{ report.warning }}</div>

        <div v-if="report?.wiki" class="bodhi-onto-block">
          <div class="bodhi-onto-block-title">wiki 重投影</div>
          <div class="bodhi-onto-dim">
            build {{ report.wiki.build?.ok ? 'ok' : '失败' }} ·
            project {{ report.wiki.project?.ok ? 'ok' : '失败' }}
          </div>
          <pre class="bodhi-onto-tail">{{ wikiTail }}</pre>
        </div>

        <div class="bodhi-onto-note">
          图谱与 <code>/bodhi/ontology/*</code> 接口已同步；到「本体图谱」tab 点「刷新」即可看到新要素。
        </div>
      </div>

      <div v-if="error" class="bodhi-onto-error">{{ error }}</div>
    </t-dialog>
  </span>
</template>

<script setup lang="ts">
/**
 * 本体文件上传面板（bodhi2 v6，2026-09-20）
 *
 * 为什么单独成一个组件：上游 KnowledgeBase.vue 有 3800+ 行，把上传逻辑塞进去会让补丁脚本
 * 越来越脆。本组件自包含（只依赖 tdesign + fetch），补丁只插「一行标签 + 一个回调」。
 *
 * 挂载位置：本体模型知识库页（面包屑里，「上传自动生成 wiki」开关右侧；父组件用
 * `isOntologyKb` 限定只有本体模型库才渲染 —— 见 patch_frontend.py 的 patch_knowledgebase_v6）。
 * 判定口径（2026-09-22 改）：父组件先按构建期常量兜底，再问服务端
 * `GET /bodhi/ontology/kb?kb_id=…`（按 env / wiki_config 标记 / 库名 / ontology:* 页数认库），
 * 所以**换本体库 uuid 不需要重建前端**；上传目标库始终由服务端决定（见下方"不传 kb_id"）。
 *
 * 后端（tools/ontology-mcp/server.py，经 nginx `/bodhi/` 反代到 8765）：
 *   GET  /bodhi/ontology/deps?model_id=<key>  → {model_id, dependent_modules[], purge_order[]}
 *                                                （只读：先看会连带删谁）
 *   POST /bodhi/ontology/upload               → 200 报告 / 400 {error}（依赖未就绪等校验错误）
 *        {filename, content, module_id, project_wiki, write_source, compile_after}
 *        （**不用 multipart**：FileReader 读成文本）
 *   口径（2026-09-24 打通）：同名模块整体替换、级联删下游；**默认 write_source + compile_after**
 *        —— 把 TTL 落进真源 `ontology/extensions/<key>-ext.ttl` 并登记 `_registry.json`，
 *        然后自动编译 artifacts + 回灌 Neo4j 投影（+ 可选重投影 wiki），回执里给出 compiled.delta；
 *        两个开关都关掉 = 老行为（只更新图谱，不动产物）。
 *   注意：**不传 kb_id** —— 让服务端用它自己的本体库解析（比构建期常量权威）。
 */
import { computed, ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'

const emit = defineEmits<{
  (e: 'uploaded', report: any): void
}>()

/** 与 tools/ke-core/ke_admin.py 的输入闸门一致（2MB）——超了服务端也会 400，这里提前拦 */
const MAX_BYTES = 2_000_000
/** 标准词汇前缀：命中时不算「本模块前缀」（口径同 ke_admin.inspect_ttl / 依赖检查） */
const STD_PREFIXES = new Set(['xsd', 'owl', 'rdf', 'rdfs', 'skos', 'dc', 'dcterms', 'sh', 'bodhi'])

const fileInput = ref<HTMLInputElement | null>(null)
const visible = ref(false)
const busy = ref(false)
const stage = ref<'form' | 'confirm' | 'done'>('form')
const error = ref('')

const fileName = ref('')
const content = ref('')
const fileSize = ref(0)
const moduleId = ref('')
/** 从 TTL 推断出来的模块名：用户改了它要提示（改模块名不会改 TTL 的命名空间） */
const inferredModule = ref('')
const projectWiki = ref(true)
/** 上传后编译并生效（2026-09-24，默认开）：落真源 ontology/extensions/ + 登记 + 编译 + 灌投影 */
const compileAfter = ref(true)
const ttlPrefix = ref('')
const ttlNamespace = ref('')
const ttlIri = ref('')

const dependentModules = ref<string[]>([])
const purgeOrder = ref<string[]>([])
const report = ref<any>(null)

async function api(path: string, init?: RequestInit): Promise<any> {
  const res = await fetch(path, init)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`)
  return data
}

function post(path: string, body: any): Promise<any> {
  return api(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
}

function qs(params: Record<string, any>): string {
  return new URLSearchParams(params).toString()
}

const header = computed(() => {
  if (stage.value === 'done') return `导入完成：${report.value?.module || moduleId.value}`
  if (stage.value === 'confirm') return '确认整体替换（级联删除下游）'
  return '导入本体文件（.ttl）'
})

const confirmBtn = computed(() => {
  if (stage.value === 'done') return { content: '关闭' }
  if (stage.value === 'confirm') return { content: '确认替换', theme: 'danger', loading: busy.value }
  return { content: '上传并加载', loading: busy.value, disabled: !content.value }
})

const cancelBtn = computed(() => (stage.value === 'done' ? null : { content: '取消', disabled: busy.value }))

const sizeText = computed(() => (fileSize.value < 1024
  ? `${fileSize.value} B`
  : `${(fileSize.value / 1024).toFixed(1)} KB`))

const purgeDeleted = computed<any[]>(() => report.value?.purge?.deleted || [])

const wikiTail = computed(() => {
  const w = report.value?.wiki || {}
  const lines = [...(w.build?.tail || []), ...(w.project?.tail || [])]
  return lines.join('\n')
})

function refKind(kind: string): string {
  return String(kind || '').replace(/^BODHI_/, '').toLowerCase()
}

/** 勾选框写成方法（而不是模板里内联赋值）——避免在模板表达式里给 ref 赋值 */
function onProjectWikiChange(v: any) {
  projectWiki.value = !!v
}

function onCompileAfterChange(v: any) {
  compileAfter.value = !!v
}

function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error || new Error('读取文件失败'))
    reader.readAsText(file, 'utf-8')
  })
}

/**
 * 从 TTL 文本推断模块名（§6.1 口径：首个非默认 @prefix / owl:Ontology IRI 末段）。
 *
 * 为什么不能「第一个非默认前缀」就完事：扩展模块文件（如 ea-ownership-ext.ttl）内部只用
 * 默认前缀 `: <…/ea-ownership#>`，而 `ea` / `bmm` 这些**别的模块**的前缀也非标准 —— 直接取
 * 第一个会推成 `ea`（错的）。所以：先找**与本模块命名空间一致**的前缀（名字非空），找不到
 * 再用 owl:Ontology IRI 的末段（`…/ext/ea-ownership` → `ea-ownership`），最后才退回文件名。
 */
function inferModule(text: string, filename: string): {
  module: string, namespace: string, iri: string, prefix: string,
} {
  const prefixes: Record<string, string> = {}
  const re = /@prefix\s+([A-Za-z0-9_.\-]*):\s*<([^>]+)>/g
  let hit: RegExpExecArray | null
  while ((hit = re.exec(text))) prefixes[hit[1]] = hit[2]

  const ont = /<([^>\s]+)>\s+(?:a|rdf:type)\s+owl:Ontology/.exec(text)
  const iri = ont ? ont[1] : ''
  const namespace = iri ? (iri.endsWith('#') || iri.endsWith('/') ? iri : `${iri}#`) : ''
  const tail = iri ? (iri.replace(/[#/]+$/, '').split(/[#/]/).filter(Boolean).pop() || '') : ''

  let prefix = ''
  if (namespace) {
    prefix = Object.keys(prefixes)
      .find(p => p && prefixes[p] === namespace && !STD_PREFIXES.has(p)) || ''
  }
  if (!prefix && !tail) {
    prefix = Object.keys(prefixes).find(p => p && !STD_PREFIXES.has(p)) || ''
  }
  const ns = namespace || (prefix ? prefixes[prefix] || '' : '')
  const raw = prefix || tail || filename.replace(/\.(ttl|turtle)$/i, '')
  return { module: raw.trim().toLowerCase().replace(/\s+/g, '-'), namespace: ns, iri, prefix }
}

function reset() {
  stage.value = 'form'
  error.value = ''
  report.value = null
  dependentModules.value = []
  purgeOrder.value = []
}

function openPicker() {
  reset()
  fileInput.value?.click()
}

async function onFileChange(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files && input.files[0]
  input.value = '' // 允许重复选同一个文件（否则第二次 change 不触发）
  if (!file) return
  if (file.size > MAX_BYTES) {
    MessagePlugin.error(`文件 ${(file.size / 1024 / 1024).toFixed(2)}MB 超过 2MB 上限，请拆分模块`)
    return
  }
  try {
    const text = await readText(file)
    const info = inferModule(text, file.name)
    fileName.value = file.name
    fileSize.value = file.size
    content.value = text
    moduleId.value = info.module
    inferredModule.value = info.module
    ttlPrefix.value = info.prefix
    ttlNamespace.value = info.namespace
    ttlIri.value = info.iri
    stage.value = 'form'
    error.value = ''
    report.value = null
    visible.value = true
  } catch (e: any) {
    MessagePlugin.error(e?.message || '读取文件失败')
  }
}

function close() {
  visible.value = false
}

async function onConfirm() {
  if (stage.value === 'done') { close(); return }
  if (stage.value === 'confirm') { await doUpload(); return }
  await prepare()
}

/** 先看会连带删谁（只读）；有下游就先摆确认框，没有就直接传（§6.1 流程） */
async function prepare() {
  const module = (moduleId.value || '').trim()
  if (!module) { MessagePlugin.warning('请填写模块名'); return }
  busy.value = true
  error.value = ''
  try {
    const deps = await api(`/bodhi/ontology/deps?${qs({ model_id: module })}`)
    dependentModules.value = deps.dependent_modules || []
    purgeOrder.value = deps.purge_order || [module]
    if (dependentModules.value.length) { stage.value = 'confirm'; return }
    await doUpload()
  } catch (e: any) {
    error.value = e?.message || String(e)
  } finally {
    busy.value = false
  }
}

async function doUpload() {
  busy.value = true
  error.value = ''
  try {
    report.value = await post('/bodhi/ontology/upload', {
      filename: fileName.value,
      content: content.value,
      module_id: (moduleId.value || '').trim(),
      project_wiki: projectWiki.value,
      // 2026-09-24：默认"落真源 + 编译并生效"；关掉则只更新图谱（老行为）
      write_source: compileAfter.value,
      compile_after: compileAfter.value,
    })
    stage.value = 'done'
    MessagePlugin.success(`已导入 ${report.value.module}：`
      + `类 ${report.value.module_classes} / 属性 ${report.value.module_properties}`
      + (report.value.compiled ? '（已编译）' : ''))
    emit('uploaded', report.value)
  } catch (e: any) {
    // 400 = 校验类错误（如「依赖未就绪…已自动回滚」）→ 原文展示，回表单改模块名/换文件后重试
    error.value = e?.message || String(e)
    stage.value = 'form'
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.bodhi-onto-upload { display: inline-flex; align-items: center; margin-left: 10px; }
.bodhi-onto-file { display: none; }
.bodhi-onto-form { display: flex; flex-direction: column; gap: 10px; }
.bodhi-onto-row { display: flex; align-items: center; gap: 8px; }
.bodhi-onto-label { flex: 0 0 72px; color: var(--td-text-color-secondary, #666); font-size: 13px; }
.bodhi-onto-value { color: var(--td-text-color-primary, #333); font-size: 13px; word-break: break-all; }
.bodhi-onto-field { flex: 1 1 auto; max-width: 420px; }
.bodhi-onto-dim { color: var(--td-text-color-placeholder, #999); font-size: 12px; }
.bodhi-onto-note {
  color: var(--td-text-color-secondary, #666); font-size: 12px; line-height: 1.6;
  background: var(--td-bg-color-secondarycontainer, #f5f5f5); border-radius: 4px; padding: 6px 8px;
}
.bodhi-onto-warn {
  color: var(--td-warning-color-6, #8b4513); font-size: 13px; line-height: 1.6;
  border: 1px solid var(--td-warning-color-3, #e37318); border-radius: 4px;
  background: var(--td-warning-color-1, #fff3e0); padding: 8px 10px;
}
.bodhi-onto-order { margin: 4px 0 0 18px; padding: 0; font-size: 12px; line-height: 1.8; }
.bodhi-onto-report { display: flex; flex-direction: column; gap: 8px; }
.bodhi-onto-report-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.bodhi-onto-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; font-size: 13px; }
.bodhi-onto-block { border-top: 1px dashed var(--td-component-stroke, #e7e7e7); padding-top: 6px; }
.bodhi-onto-block-title { color: var(--td-text-color-secondary, #666); font-size: 12px; margin-bottom: 4px; }
.bodhi-onto-codes { font-size: 11px; word-break: break-all; }
.bodhi-onto-ref { display: flex; align-items: center; gap: 6px; font-size: 12px; padding: 2px 0; flex-wrap: wrap; }
.bodhi-onto-ref--bad { background: var(--td-error-color-1, #fff0ed); border-radius: 3px; }
.bodhi-onto-tail {
  margin: 4px 0 0; max-height: 120px; overflow: auto; font-size: 11px; line-height: 1.5;
  background: var(--td-bg-color-secondarycontainer, #f5f5f5); border-radius: 4px; padding: 6px 8px;
  white-space: pre-wrap;
}
.bodhi-onto-error {
  margin-top: 10px; color: var(--td-error-color, #d54941); font-size: 12px; line-height: 1.6;
  border: 1px solid var(--td-error-color-3, #d54941); border-radius: 4px;
  background: var(--td-error-color-1, #fff0ed); padding: 8px 10px; white-space: pre-wrap;
}
</style>
