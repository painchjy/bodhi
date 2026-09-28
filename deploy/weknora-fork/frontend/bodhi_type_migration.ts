/**
 * bodhi2（2026-09-27）：本体类型「迁移」通道 —— **两段式**（预览 → 用户确认 → 执行）。
 *
 * 为什么需要它
 * ------------
 * 上游 `PUT /wiki/pages` 只认内建类型；本体类型的落点是 **slug**（实例页三段式
 * `模块/类/名称`）与页面里的**引用**。所以「改本体类型」= 迁移 slug + 改写引用
 * （关系行 / 反向链接 / 正文 / `## 溯源` / `page_metadata` / 建模会话状态）。
 * 影响面是真实存在的（旧链接失效、引用页被改、可能违反新类的 domain-range），
 * 因此**不允许静默改**：
 *
 *   1. `POST /bodhi/page/retag/preview` → 新 slug / 引用处数 / 预计违规 / `required_risks` / `ticket`
 *   2. 弹确认框（新 slug、引用处数、风险原文、ticket 一起展示）
 *   3. `POST /bodhi/page/retag/apply`（带 `ticket` + `acknowledge_risks = required_risks`）
 *
 * 服务端守门：缺 `ticket` / 缺风险确认 / ticket 与当前影响面不匹配 → 一律拒绝
 * （`need_repreview=true`）。回滚：`POST /bodhi/page/retag/rollback`（运维/CLI 亦可）。
 *
 * 不适用迁移的场景（slug 非三段式、新类推不出 slug、preview 不可用）→ **退回旧的
 * `POST /bodhi/page/type`**（只改类型，不动 slug），保持既有行为不倒退。
 */
import { h } from 'vue'
import { DialogPlugin } from 'tdesign-vue-next'

export interface BodhiRetagResult {
  /** 是否真的做了「迁移」（slug + 引用联动） */
  applied: boolean
  /** 类型是否已变成目标（含退回「只改类型」通道的成功） */
  changed_type: boolean
  /** 迁移后的新 slug（仅 applied=true 时有意义） */
  new_slug?: string
  /** 目标类型的展示名 */
  type_label?: string
  /** 未改动时的原因（取消 / 影响面变化 / 不适用…） */
  message?: string
}

const RISK_TEXT: Record<string, string> = {
  url_break: '旧链接 / 书签失效（页 slug 会变）',
  refs_rewrite: '引用会被改写（关系行 / 反向链接 / 正文 / 溯源 / 元数据）',
  agent_session: '建模会话状态里记着的旧 slug 会被一并改写',
  cross_kb_binding: '存在跨库绑定引用（别的库的页也指向这一页）',
  schema_violation: '按新类的 domain-range / 必填属性，本页可能不合规',
}

async function postJson(url: string, body: unknown): Promise<any> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((data && (data.error || data.message)) || 'HTTP ' + res.status)
  return data
}

/** 退回旧通道：只改类型、不动 slug（上游不接受本体类型时也是这条路）。 */
async function legacyChangeType(kbId: string, slug: string, newType: string,
                                why?: string): Promise<BodhiRetagResult> {
  try {
    const data = await postJson('/bodhi/page/type', { kb_id: kbId, slug, page_type: newType })
    return { applied: false, changed_type: true, type_label: data && data.type_label }
  } catch (e: any) {
    return { applied: false, changed_type: false,
             message: ((e && e.message) || '本体类型修改失败') + (why ? '（' + why + '）' : '') }
  }
}

function confirmBody(prev: any) {
  const to = (prev.slug_change && prev.slug_change.to) || prev.slug || ''
  const total = (prev.refs && prev.refs.total) || 0
  const kinds = (prev.refs && prev.refs.by_kind) || {}
  const kindText = Object.keys(kinds).map((k) => k + '×' + kinds[k]).join('、') || '无'
  const reqs: string[] = prev.required_risks || []
  const rows: any[] = [
    h('p', {}, ['类型：', h('b', {}, (prev.old && prev.old.class_label) || (prev.old && prev.old.page_type) || ''),
                ' → ', h('b', {}, (prev.new && prev.new.class_label) || prev.new?.page_type || '')]),
    h('p', {}, ['新 slug：', h('code', {}, to)]),
    h('p', {}, ['引用改写：' + total + ' 处（' + kindText + '）']),
  ]
  if ((prev.schema_violations || []).length) {
    rows.push(h('p', {}, ['预计不合规：' + prev.schema_violations.length + ' 条 —— '
                          + prev.schema_violations.slice(0, 3).map((v: any) =>
                              (v.rule || v.kind || 'violation') + (v.detail ? '：' + v.detail : '')).join('；')]))
  }
  rows.push(h('p', {}, '需要你确认的风险：'))
  rows.push(h('ul', { style: 'margin:4px 0 0 18px' },
              reqs.map((r) => h('li', {}, RISK_TEXT[r] || r))))
  rows.push(h('p', { style: 'color:#888;font-size:12px;margin-top:8px' },
              '执行后会写入迁移记录，可回滚（ticket ' + (prev.ticket || '-') + '）。'))
  return () => h('div', {}, rows)
}

/**
 * 改本体类型（两段式）。返回「是否已改/已迁移」，调用方据此刷新页面。
 * 取消时**不**改类型（调用方应把下拉还原成原值）。
 */
export async function bodhiMigratePageType(kbId: string, slug: string, newType: string,
                                           typeLabel?: string): Promise<BodhiRetagResult> {
  if (!kbId || !slug || !newType) {
    return { applied: false, changed_type: false, message: '参数不完整' }
  }
  let prev: any
  try {
    prev = await postJson('/bodhi/page/retag/preview',
                          { kb_id: kbId, slug, new_type: newType })
  } catch (e: any) {
    // preview 不可用（老服务/网络）→ 不阻塞用户，退回只改类型
    return legacyChangeType(kbId, slug, newType, '迁移预览不可用：' + ((e && e.message) || e))
  }
  if (prev.already) {
    return { applied: false, changed_type: false, message: prev.apply_hint || '类型与 slug 都已是目标状态' }
  }
  if (!prev.applicable) {
    const why = (prev.warnings || []).join('；') || '该页不适用类型迁移'
    return legacyChangeType(kbId, slug, newType, why)
  }
  const label = typeLabel || (prev.new && (prev.new.class_label || prev.new.page_type)) || newType
  return await new Promise<BodhiRetagResult>((resolve) => {
    const dialog = DialogPlugin.confirm({
      header: '改本体类型 = 迁移页面（需确认）',
      body: confirmBody(prev),
      confirmBtn: '确认迁移',
      cancelBtn: '取消（不改）',
      theme: 'warning',
      onConfirm: async () => {
        dialog.destroy()
        try {
          const ap = await postJson('/bodhi/page/retag/apply', {
            kb_id: kbId, slug, new_type: newType,
            ticket: prev.ticket,
            acknowledge_risks: prev.required_risks || [],
          })
          resolve({
            applied: true, changed_type: true, type_label: label,
            new_slug: ap.new_slug || ap.slug
                      || (ap.slug_change && ap.slug_change.to) || slug,
          })
        } catch (e: any) {
          const msg = (e && e.message) || '类型迁移失败'
          resolve({ applied: false, changed_type: false,
                    message: /need_repreview|ticket/.test(String(msg))
                             ? '影响面已变化，已取消本次修改，请重新保存再确认' : msg })
        }
      },
      onCancel: () => {
        dialog.destroy()
        resolve({ applied: false, changed_type: false, message: '已取消类型迁移（类型未改）' })
      },
    })
  })
}
