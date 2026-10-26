# 04-it-platform-mcp（独立服务包）

> 用户裁定（2026-10-26）：**it-platform 单独打包**（只在这个包里），`02-mcp-server` **排除**它 ——
> 两者是不同服务、不同端口（本体 8765 / ext 8766）、不同数据根，混在一起会让"只换一个服务"变成换整包。

## 1. 包内容

| 路径 | 说明 |
|---|---|
| `tools/it-platform-mcp/` | 服务代码 + `schema.json`（**契约真源**，随服务版本走） |
| `tools/ke-core/ke_ext_*.py` + 依赖 | 台账 / 文档 / 流程 / 契约投射 |
| `deploy/mock-ext/{allowlist.json,denylist.json,issues/,flows/}` | 文本种子（**不含** docs/files/images 二进制） |
| `deploy/delivery/04-it-platform-mcp/{install.sh,selfcheck.py,.env.sample,it-platform-mcp.service}` | 安装/自检 |
| `CONFIG-MATRIX.md` | 哪些能改、改哪、怎么生效、怎么校验 |

## 2. 安装

```bash
sudo bash deploy/delivery/04-it-platform-mcp/install.sh \
     --prefix /opt/bodhi-ext --data /var/lib/bodhi-ext
```

它做四件事（幂等）：拷代码 + 种子（跳过二进制目录）→ 建数据根 → 装 systemd 单元 → 跑 `selfcheck.py`。

## 3. 自检（发布后必跑）

```bash
python3 deploy/delivery/04-it-platform-mcp/selfcheck.py --base /opt/bodhi-ext
```

检查：契约可读 + 带 `x_ext`｜服务/种子两份 `schema.json` **一致**｜`ext_*` 工具都有契约且 `schemaHash` 可下发｜
种子文件齐（二进制目录允许缺失）｜智能体配置无漂移（`apply_config.py --check`，可用 `--skip-agent`）。

## 4. 与 02 包的分工

| 项 | 02（bodhi-mcp，8765） | 04（本包，8766） |
|---|---|---|
| 工具 | 本体/知识/巡检/导入 38 个 | ext 台账/文档/流程（`ext_*`） |
| 数据 | WeKnora PG + Neo4j | `BODHI_EXT_ROOT`（JSON 台账/附件） |
| 契约 | 本体 TTL / 编译产物 | `schema.json` 的 `x_ext` |
| 升级影响 | 本体侧全量 | 只影响 ext 面板/评审链路 |

> `knowledge_lookup(mode="page")` 属于 **02**（口径读取），不依赖本包；两者可独立升级 ✓。

## 5. 回滚

```bash
systemctl disable --now it-platform-mcp
# 数据不删：$BODHI_EXT_ROOT 保留，重新指向旧版本 prefix 即可
```
