// 构建前闸门：用 @vue/compiler-sfc 解析所有 .vue，找出「被截断 / 语法坏」的文件。
// 2026-09-19 实例：src/views/settings/SandboxSettings.vue 从 CDN 补拉时被截断
// （7,594B vs 真实 30,112B），vite build 在 4020 模块后才报
// "Element is missing end tag"，很难定位。先跑本脚本能秒级指出文件与尾部片段。
//
// 用法（在含 node_modules 的前端目录里）：
//   node deploy/weknora-fork/frontend/check_sfc.mjs [src目录=./src]
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
// 用 CJS 入口，避免 ESM-browser 版与 node 环境的差异
const { parse } = require('@vue/compiler-sfc')

const ROOT = process.argv[2] || 'src'
const bad = []
const all = []

function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) walk(p)
    else if (name.endsWith('.vue')) all.push(p)
  }
}
walk(ROOT)

for (const file of all) {
  const src = readFileSync(file, 'utf8')
  try {
    const { errors } = parse(src, { filename: file })
    if (errors && errors.length) {
      bad.push({ file: relative(ROOT, file), size: src.length, err: String(errors[0].message).slice(0, 120),
                 tail: src.slice(-120).replace(/\s+/g, ' ') })
    }
  } catch (e) {
    bad.push({ file: relative(ROOT, file), size: src.length, err: String(e.message).slice(0, 120),
               tail: src.slice(-120).replace(/\s+/g, ' ') })
  }
}

console.log(`扫描 ${all.length} 个 .vue，损坏 ${bad.length} 个`)
for (const b of bad) {
  console.log(`  ✗ ${b.file}  size=${b.size}  err=${b.err}`)
  console.log(`      tail: ...${b.tail}`)
}
process.exit(bad.length ? 1 : 0)
