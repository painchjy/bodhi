# deploy —— 图库与本体投影落库（对上游 WeKnora 零侵入）

## 1. 这个目录解决什么问题

上游 WeKnora 自己会起一个 Neo4j（`--profile neo4j`），那是它**自己的**实例。
本项目的本体投影（`artifacts/neo4j/*.cypher`）必须落到**同一张图**里，否则
「本体约束实例」这件事在物理上就不成立。

所以本目录只做两件事：**起/连 Neo4j** + **把本体投影灌进去**。
不改上游一行代码，不 fork 上游 compose。

## 2. 三条硬约束（先读）

1. **同库才有效**：本体投影节点与 WeKnora 抽取出的知识点必须落同一个 Neo4j 实例
   ——`NEO4J_URI` 指向哪张图，本体就灌哪张图。两侧都走驱动默认库（上游默认值
   `bolt://neo4j:7687` 不带 database 路径）。
2. **投影靠标记区分**：本体投影节点全部带 `bodhi_projection = 'ontology'`；
   该属性是本项目加的标记（上游不会写），清理和核对范围都按它筛。
3. **先有产物再灌库**：`artifacts/` 是可从 TTL 重建的产物、已被 `.gitignore` 忽略。
   灌库前先在仓库根目录执行：

   ```powershell
   python tools/ontology-compiler/compile.py compile
   ```

   灌库脚本会检查 `00_constraints.cypher` / `10_ontology.cypher` 是否存在，
   缺失时直接失败并提示这条命令。

## 3. 文件清单

| 文件 | 用途 |
| --- | --- |
| `docker-compose.yml` | 拓扑 B：本仓库自持 Neo4j + 自动灌本体（一条 `up -d` 搞定） |
| `docker-compose.weknora.yml` | 拓扑 A：叠加到上游 compose，给上游的 `neo4j` 灌本体 |
| `bootstrap-neo4j.sh` | 灌库脚本（幂等、带连接重试）；被上面两个 compose 调用 |
| `check_deploy.py` | 自检：compose 结构 + 变量插值仿真 + 挂载路径 + 合并语义 + 脚本检查（不需要 Docker） |
| `.env.example` | 变量模板，复制成 `.env`（`deploy/.env` 已被 `.gitignore` 忽略） |
| `README.md` | 本文件 |

脚本必须保持 **LF** 换行（容器里的 `sh` 会把 CRLF 的 `\r` 当命令内容而报错），
仓库根 `.gitattributes` 已用 `*.sh text eol=lf` 锁住。

## 4. 拓扑 A：用上游自带的 Neo4j（推荐，最省事）

上游契约（取证来源见第 8 节）：

```env
# 上游仓库的 .env
NEO4J_ENABLE=true
NEO4J_URI=bolt://neo4j:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password      # 上游示例值，生产必须改
```

```bash
docker-compose --profile neo4j up -d       # 上游文档里的原样命令
```

再把本仓库的本体投影灌进这个图库（在上游仓库根目录执行，`<BODHI2>` 换成本仓库绝对路径）：

```powershell
python <BODHI2>\tools\ontology-compiler\compile.py compile

$env:BODHI_DEPLOY_DIR       = '<BODHI2>/deploy'
$env:BODHI_NEO4J_CYPHER_DIR = '<BODHI2>/artifacts/neo4j'

docker compose -f docker-compose.yml `
  -f <BODHI2>/deploy/docker-compose.weknora.yml `
  --profile neo4j --profile bodhi up -d
```

叠加层只新增 `bodhi-ontology` 一个一次性服务：加入上游已定义的
`WeKnora-network`，用上游 `.env` 里的 `NEO4J_URI/USERNAME/PASSWORD` 连库，
执行顺序固定为 `00_constraints.cypher` → `10_ontology.cypher`。
上游那个 `neo4j` 服务一个字都没改。

## 5. 拓扑 B：本仓库自持图库

适合「WeKnora 与图库分开部署」或想用独立端口/密码的场景：

```powershell
cd deploy
Copy-Item .env.example .env          # 首次；按需改 NEO4J_PASSWORD / 端口
docker compose up -d                 # 起 neo4j + 自动灌本体（灌完容器退出，属正常）
docker compose ps                    # neo4j healthy；bodhi2-ontology-bootstrap 显示 exited(0)
```

然后在上游仓库的 `.env` 里把连接指向这张图：

| 部署形态 | `NEO4J_URI` |
| --- | --- |
| 上游 app 与本图库同机（Docker Desktop） | `bolt://host.docker.internal:7687` |
| 上游 app 与本图库同机（Linux 宿主） | `bolt://<宿主机IP>:7687`（或给上游服务加 `extra_hosts: host.docker.internal:host-gateway`） |
| 图库在另一台机器 | `bolt://<图库主机IP>:7687` |

`NEO4J_USERNAME` / `NEO4J_PASSWORD` 必须与 `deploy/.env` 一致，否则 WeKnora 连的是
另一张（空的）图，本体约束静默失效——这是最容易踩的坑。


## 6. 手动灌库 / 重灌 / 清理

自动灌库失败（例如产物缺失、密码错）时可手动跑同一个脚本：

```powershell
# 借用 compose 里的一次性服务，容器内才有 sh
cd deploy
docker compose up -d neo4j                       # 先保证图库在跑
docker compose run --rm --no-deps bodhi-ontology # 重跑灌库，观察脚本日志
```

或直接用本机 cypher-shell（写法与 `artifacts/neo4j/README.md` 一致）：

```bash
cypher-shell -a bolt://localhost:7687 -u neo4j -p '<password>' -f artifacts/neo4j/00_constraints.cypher
cypher-shell -a bolt://localhost:7687 -u neo4j -p '<password>' -f artifacts/neo4j/10_ontology.cypher
```

本体变更后重灌（两个脚本都是 `MERGE` + `IF NOT EXISTS`，重复执行安全）：

```powershell
python tools/ontology-compiler/compile.py compile
docker compose -f deploy/docker-compose.yml up -d --force-recreate bodhi-ontology
```

只删本体投影、不动实例数据（例如本体大改后想彻底重建）：

```cypher
MATCH (n) WHERE n.bodhi_projection = 'ontology' DETACH DELETE n;
```

## 7. 验证清单

| # | 检查 | 命令/位置 | 期望 |
| --- | --- | --- | --- |
| 1 | 图库活着 | `docker compose ps`，或浏览器开 `http://localhost:7474` | `neo4j` 为 healthy |
| 2 | 约束已建 | Browser 里 `SHOW CONSTRAINTS` | 5 条 `bodhi*_iri` 唯一约束 + 5 条索引 |
| 3 | 投影规模 | 见下方查询 | 见下方预期数字 |
| 4 | 上游图开关 | 上游仓库 `.env`：`NEO4J_ENABLE=true` | 与 `--profile neo4j` 一起生效 |
| 5 | 知识库抽取开关 | 知识库设置页启用「实体与关系提取」 | 即 `indexing_strategy.graph_enabled=true` 且该库 `extract_config.enabled=true` |
| 6 | 本体注入知识库 | 把 `artifacts/weknora/extract_config.{module}.json` 整体写进该库的 `extract_config` | 一个知识库一份；见 `tools/ontology-compiler/README.md` |
| 7 | 端到端 | 上传文档后 Browser 里 `MATCH (n) RETURN (n)` | 既有本体节点（带 `bodhi_projection`），也有 WeKnora 抽出的实例节点 |

投影规模查询与预期（数字取自 2026-09-17 一版编译产物；本体改动后重跑 compile 并同步本表）：

```cypher
MATCH (n) WHERE n.bodhi_projection = 'ontology'
RETURN labels(n)[0] AS kind, count(*) AS count ORDER BY kind;
```

| kind | 预期 count | 说明 |
| --- | --- | --- |
| `BodhiModule` | 5 | 2 基座 + 3 扩展 |
| `BodhiOntClass` | 53 | 47 个声明类（含 2 枚举）+ 6 个 `external:'True'` 占位节点 |
| `BodhiEnumValue` | 7 | 枚举取值 |
| `BodhiRestriction` | 26 | 基数 / 值域限制 |
| `BodhiOntProperty` | 90 | 对象属性 70 + 数据属性 20 |

跨层查询模板 `20_cross_layer_queries.cypher` **不灌库**：它是 TBox 侧的人工查询
（Q1–Q7），刻意不硬编码 WeKnora 的实例 schema（避免与上游版本耦合），实例层的
关联推理在 Python（`ke-core`）侧做。

## 8. 变量表

`deploy/.env`（拓扑 B；叠加模式下同名变量取上游 `.env` 的值）：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `NEO4J_IMAGE` | `neo4j:5-community` | 图库 / 灌库容器镜像。产物里的约束是 Neo4j 5 语法（`CREATE CONSTRAINT ... IF NOT EXISTS ... REQUIRE ... IS UNIQUE`），4.x 起不来 |
| `NEO4J_HTTP_PORT` / `NEO4J_BOLT_PORT` | `7474` / `7687` | 宿主机映射端口（与上游 profile 起的 Neo4j 冲突时改这里） |
| `NEO4J_USERNAME` | `neo4j` | 官方镜像初始用户名固定 `neo4j`；改这里只影响灌库侧引用，不影响镜像 |
| `NEO4J_PASSWORD` | `password` | ⚠️ POC 默认值，生产必须改；必须与上游 `.env` 一致 |
| `NEO4J_URI` | `bolt://neo4j:7687` | 灌库容器连接串；跨 compose 时改宿主地址 |
| `BODHI_NEO4J_CYPHER_DIR` | `../artifacts/neo4j` | Cypher 产物目录（相对 `deploy/` 解析）；叠加模式下必须显式给绝对路径 |
| `BODHI_DEPLOY_DIR` | 无（叠加模式必填） | 本 `deploy/` 目录绝对路径，供叠加层挂载 `bootstrap-neo4j.sh` |

脚本额外可调（一般不用）：`BODHI_RETRY_TRIES`（默认 60 次）、`BODHI_RETRY_SLEEP`（默认 2 秒）。

## 9. 上游契约取证（2026-09-17 复核 Tencent/WeKnora `main`）

复核通道：编写环境 `github.com` / `codeload` / `raw.githubusercontent.com` 均不可达，故经
jsDelivr 的 GitHub 只读镜像（`cdn.jsdelivr.net/gh/Tencent/WeKnora@main/...`）取原文，
镜像可拉性用 Docker Registry v2 匿名 token 流程实测（不经 `docker`）。

| 事实 | 来源 |
| --- | --- |
| `NEO4J_ENABLE` / `NEO4J_URI=bolt://neo4j:7687` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` | `docs/KnowledgeGraph.md` |
| 启动方式 `docker-compose --profile neo4j up -d`、`http://localhost:7474`、`match (n) return (n)` | 同上 |
| 知识图谱**唯一**开关是 `NEO4J_ENABLE`；`ENABLE_GRAPH_RAG` 自 v0.1.6 起已被取代、Go 主应用不再读取 | `docker-compose.yml`「知识图谱唯一开关」注释 + `.env.example` §C2（`.env.lite.example` 仍留旧键，**不可**作为依据） |
| `neo4j` 服务：镜像 `neo4j:2025.10.1`、profiles `neo4j`/`full`、端口 7474/7687、`NEO4J_AUTH=${NEO4J_USERNAME}/${NEO4J_PASSWORD}` | `docker-compose.yml` |
| 网络名 `WeKnora-network`、数据卷 `neo4j-data`（叠加层据此加入同一网络） | `docker-compose.yml` |
| `app` 服务本地挂载只有 `./config/config.yaml`；栈内其余镜像均在 Docker Hub | `docker-compose.yml` |
| 抽取配置 6 键（`enabled`/`text`/`tags`/`nodes`/`relations`/`custom_instructions`） | `tools/ontology-compiler/README.md`「WeKnora 抽取契约」 |

`NEO4J_URI` 的默认值证明上游图库的服务 DNS 名就是 `neo4j`，所以本目录两个 compose
都用 `bolt://neo4j:7687` 作为默认连接串。上游若改网络名或服务名，改动点只有本目录的
两个 compose 文件。

## 10. 已知限制

- **已在真机实跑（2026-09-18 更新）**：原先「本机未实跑」的限制已解除。实测环境：
  Windows 11 → WSL2 Ubuntu 26.04（Docker Engine 29.1.3 + Compose v2.40.3，`docker.io` 来自 Ubuntu 源，
  `download.docker.com` 不可达）。真实起栈 + 真实灌库结果：

  | 项 | 实测 |
  | --- | --- |
  | 灌库容器 | `bodhi2-ontology-bootstrap` 首次运行经 3 次重试等到 neo4j 就绪，随后应用 `00_constraints.cypher` + `10_ontology.cypher`，**`Exited (0)`** |
  | 投影规模 | `BodhiModule 5` / `BodhiOntClass 53` / `BodhiOntProperty 90` / `BodhiRestriction 26` / `BodhiEnumValue 7`（181 节点），与 `bootstrap-neo4j.sh` 打印的预期**逐项一致** |
  | 关系 | 407 条：`BODHI_DECLARES 137` / `BODHI_RANGE 92` / `BODHI_DOMAIN 90` / `BODHI_HAS_RESTRICTION 26` / `BODHI_ON_PROPERTY 26` / `BODHI_SUBCLASS_OF 24` / `BODHI_ENUM_MEMBER 7` / `BODHI_INVERSE_OF 5` |
  | 约束 | 5 条 `UNIQUENESS`（各投影标签的 `iri`） |
  | 叠加层契约 | `docker compose --profile neo4j --profile bodhi up -d --no-build` 全程无需改动上游服务；`--no-build` 必需（无源码） |
  | 环境侧闸门① | `WeKnora-app` 容器内 `NEO4J_ENABLE=true`、`NEO4J_URI=bolt://neo4j:7687`，日志 `Successfully connected to Neo4j after 12 attempts` |

  注意：投影属性名只有 `bodhi_projection` 带前缀，其余是普通名（`module`/`label`/`comment`/
  `property_kind` 等），且布尔值是字符串（`external = 'True'`，不能写 `= true`）。
- **镜像仓库与源码可达性**：`registry-1.docker.io` / `hub.docker.com` / `download.docker.com` 超时，
  必需 `registry-mirrors`（实测 `docker.m.daocloud.io`、`docker.1ms.run` 可解析本栈全部镜像）且
  镜像要预拉；`github.com` / `codeload` 在宿主不可达，拿不到上游源码时**必须**
  `docker compose up -d --no-build`（上游 6 个服务带 `build:`，一旦镜像缺失，Compose 会
  退化成本地构建并失败在缺构建上下文上）。
- `neo4j:5-community` 是官方滚动 tag；产物语法要求 Neo4j 5+，与上游 `neo4j` 主版本
  不一致时用 `NEO4J_IMAGE` 覆盖（灌库容器只用镜像里的 `cypher-shell`）。
- 本目录的 `NEO4J_IMAGE` **只影响我们这一侧**，不会改变上游 compose 里的图库镜像。
- 叠加层不写 `depends_on`：上游 `neo4j` 在 profile 下、健康检查未知，等待就绪完全由
  `bootstrap-neo4j.sh` 的重试承担（默认最长约 2 分钟），重试耗尽退出码为 2。
- 灌库容器 `bodhi2-ontology-bootstrap` 跑完即退（`restart: "no"`）：
  `docker compose ps` 里显示 `Exited (0)` 是正常结果，不是故障。

## 11. 自检

```powershell
python deploy/check_deploy.py        # 退出码 0 通过 / 1 有失败项
```

不需要 Docker：用 PyYAML + 插值仿真校验两个 compose 的结构、变量来源与取值、
挂载路径、与上游 compose 合并后的语义，以及 `bootstrap-neo4j.sh` 的换行 / 结构 /
引用的 cypher 文件。改动本目录任何文件后都应重跑一次。

有 Docker 的机器上再补一次真实解析（Compose 会自己报出语法与变量问题）：

```bash
cd deploy && docker compose --env-file .env.example config
```


