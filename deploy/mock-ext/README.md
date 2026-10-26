# `deploy/mock-ext/` —— 模拟 IT 工作台的**种子与边界**

> 用户裁定（2026-10-26）：这个目录**不是**演示数据仓库，只放"必须随交付走的文本种子"。
> 二进制与抓取产物**不进 git、不进发布包**（由现场生成），避免仓库/包体膨胀与版权风险。

## 1. 目录边界

| 路径 | 进 git？ | 进 04 包？ | 用途 |
|---|---|---|---|
| `it-platform/allowlist.json` / `denylist.json` | ✓ | ✓ | 文档白名单/黑名单（导入时校验） |
| `it-platform/issues/` / `flows/` | ✓ | ✓ | 台账/流程**结构样例**（空表样例，非业务数据） |
| `it-platform/docs/` | ✗ | ✗ | docx 切节产物（`upload_doc.sh` 生成） |
| `it-platform/files/` | ✗ | ✗ | 原始附件（pdf/docx/图片） |
| `it-platform/images/` | ✗ | ✗ | 抽出的内嵌图 |
| `it-platform/state/` | ✗ | ✗ | 运行时容器根（容器里是 `BODHI_EXT_ROOT`） |
| `upload_doc.sh` | ✓ | ✓ | **排障后备**：二进制直传（正常链路只用 `ext_doc_access(action="import")`） |
| `seed-demo.sh` | ✓ | ✗（不进包） | 演示/联调时灌样例（要显式执行） |

## 2. 契约文件在哪

`it-platform/schema.json` **已迁到** `tools/it-platform-mcp/schema.json`（契约跟服务版本走）。

读取顺序（`tools/it-platform-mcp/schemas.py::_contract`）：
1. `tools/it-platform-mcp/schema.json`（**包内必有**）；
2. `$BODHI_EXT_SEED/it-platform/schema.json`（现场覆盖，可选）；
3. `$BODHI_EXT_ROOT/it-platform/schema.json`（首次 bootstrap 的旧副本，兜底）。

> 判据：`curl -s localhost:8766/mcp -d '{"method":"tools/list"}'` 里 `x-ext.schemaHash` 变了 ⇒ 契约生效。

## 3. 本地联调

```bash
# ① 生成一份合成文档并入库（仅本地）
bash deploy/mock-ext/seed-demo.sh            # 默认只建空台账/流程样例
bash deploy/mock-ext/upload_doc.sh 202610-000001 ./技术方案v2.pdf "技术方案v2"

# ② 让服务读本目录当容器根（可选）
export BODHI_EXT_ROOT=$PWD/deploy/mock-ext
```

## 4. 单测不用这里的二进制

`tests/fixtures/mock-ext/` 有**合成**的 2KB docx + 最小 `index.json`/`allowlist.json`，
单测（`tests/test_ext_tools.py` 等）用它，**不依赖**真实的客户文档 ✓。
