"""deploy/ 静态自检：不装 Docker 也能验证部署配置。

用法：python deploy/check_deploy.py      # 退出码 0 通过 / 1 有失败项

本机若无 Docker（没法跑 `docker compose config`），本脚本用 PyYAML + 插值仿真
覆盖同样几件事：

1. 两个 compose 文件能被 YAML 解析，services / volumes / networks 结构正确；
2. 按 Compose 规则仿真插值（`$$` 转义、`${V:-def}`、`${V:?err}`），并断言转义还原正确；
3. 只引用「上游已有的 + deploy/.env.example 声明过的」变量，不现造变量名；
4. 挂载源路径在磁盘上真实存在（含 artifacts 产物必备文件）；
5. 仿真与上游 docker-compose.yml 合并后，bodhi-ontology 依赖的 WeKnora-network 确实存在，
   且上游服务定义未被改写；
6. bootstrap-neo4j.sh 的静态检查（LF、无 TAB、do/done 配平、引用的 cypher 文件存在）。

有 Docker 的机器上建议再补一次真实校验：

    cd deploy; docker compose --env-file .env.example config
"""
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
OK, BAD = [], []


def _configure_stdout() -> None:
    """Windows 控制台/管道默认编码可能装不下中文，能改就统一成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - 非标准流
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):  # pragma: no cover - 取决于终端
            pass


def check(name: str, cond: bool, detail: str = "") -> None:
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "失败", name, (" — " + detail) if detail else ""))


def load_env_example():
    """解析 .env.example -> (已声明变量集合, 取值字典)；注释里的 `# VAR=` 也算已声明。"""
    declared, values = set(), {}
    for line in (DEPLOY / ".env.example").read_text(encoding="utf-8").splitlines():
        matched = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
        if matched:
            declared.add(matched.group(1))
            values[matched.group(1)] = matched.group(2).strip()
            continue
        matched = re.match(r"^#\s*([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
        if matched:
            declared.add(matched.group(1))
    return declared, values


VARPAT = re.compile(r"\$(?:\$|\{([A-Za-z_][A-Za-z0-9_]*)((:?[-?+])(.*?))?\})")
USED = {}


def interpolate(text: str, env: dict, origin: str) -> str:
    """仿真 Compose 插值：`$$` -> `$`；`${V}` / `${V:-d}` / `${V:?e}`；缺失必填抛 KeyError。"""
    def repl(match) -> str:
        if match.group(0) == "$$":
            return "\x00D\x00"
        name, op, arg = match.group(1), match.group(3), match.group(4)
        USED.setdefault(name, set()).add(origin)
        if op in (":-", "-"):
            return env.get(name) or (arg or "")
        if op in (":?", "?"):
            if env.get(name, "") == "":
                raise KeyError(arg or ("%s 未设置" % name))
            return env[name]
        if env.get(name, "") == "":
            USED.setdefault("__MISSING__", set()).add("%s@%s" % (name, origin))
            return ""
        return env[name]

    return VARPAT.sub(repl, text).replace("\x00D\x00", "$")


def main() -> int:
    print("=== 1. YAML 解析与结构 ===")
    docs = {}
    for name in ("docker-compose.yml", "docker-compose.weknora.yml"):
        raw = (DEPLOY / name).read_text(encoding="utf-8")
        try:
            docs[name] = yaml.safe_load(raw)
            check("%s 可被 YAML 解析" % name, True)
        except Exception as exc:  # noqa: BLE001
            docs[name] = {}
            check("%s 可被 YAML 解析" % name, False, repr(exc))

    std = docs["docker-compose.yml"]
    ovl = docs["docker-compose.weknora.yml"]
    check("标准文件顶层含 name/services/volumes/networks",
          std.get("name") == "bodhi2" and {"services", "volumes", "networks"} <= set(std.keys()))
    check("标准文件服务集合 = {neo4j, bodhi-ontology}",
          set(std.get("services", {})) == {"neo4j", "bodhi-ontology"},
          str(sorted(std.get("services", {}))))
    check("标准文件 neo4j 端口映射为 7474/7687",
          std["services"]["neo4j"]["ports"] == ["${NEO4J_HTTP_PORT:-7474}:7474",
                                                "${NEO4J_BOLT_PORT:-7687}:7687"])
    check("标准文件 neo4j 只注入 NEO4J_AUTH 一键",
          [str(e).split("=")[0] for e in std["services"]["neo4j"]["environment"]] == ["NEO4J_AUTH"],
          "不单独注入 NEO4J_PASSWORD：镜像入口脚本会把它写进 neo4j.conf")
    check("标准文件 bodhi-ontology 用挂载脚本做 entrypoint",
          std["services"]["bodhi-ontology"]["entrypoint"] == ["/bin/sh", "/bodhi-deploy/bootstrap-neo4j.sh"])
    check("叠加层只新增 bodhi-ontology，不定义 neo4j/网络/卷",
          set(ovl.get("services", {})) == {"bodhi-ontology"}
          and "networks" not in ovl and "volumes" not in ovl)

    print()
    print("=== 2/3. 插值仿真与变量来源 ===")
    declared, values = load_env_example()
    print("  .env.example 已声明: %s" % ", ".join(sorted(declared)))

    std_env = dict(values)
    up_env = {"NEO4J_URI": "bolt://neo4j:7687", "NEO4J_USERNAME": "neo4j",
              "NEO4J_PASSWORD": "upstream-secret",
              "BODHI_DEPLOY_DIR": "C:/repo/deploy",
              "BODHI_NEO4J_CYPHER_DIR": "C:/repo/artifacts/neo4j"}

    raw_std = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    raw_ovl = (DEPLOY / "docker-compose.weknora.yml").read_text(encoding="utf-8")

    USED.clear()
    std_out = interpolate(raw_std, std_env, "docker-compose.yml")
    check("标准文件插值后无未转义的 ${VAR}", not VARPAT.search(std_out))
    check("$$ 转义被还原（容器侧变量保留、且无残留 $$）",
          "$CS" in std_out and "$$" not in std_out and "$(" in std_out)
    std_final = None
    try:
        std_final = yaml.safe_load(std_out)
        check("插值后的标准 compose 仍是合法 YAML", True)
    except Exception as exc:  # noqa: BLE001
        check("插值后的标准 compose 仍是合法 YAML", False, repr(exc))
    if std_final:
        check("插值后 neo4j 认证行 = NEO4J_AUTH=neo4j/password（取自 .env.example）",
              std_final["services"]["neo4j"]["environment"] == ["NEO4J_AUTH=neo4j/password"],
              str(std_final["services"]["neo4j"]["environment"]))
        check("插值后灌库容器连接串 = bolt://neo4j:7687",
              "NEO4J_URI=bolt://neo4j:7687" in std_final["services"]["bodhi-ontology"]["environment"])
        healthcheck = std_final["services"]["neo4j"]["healthcheck"]["test"][1]
        check("插值后健康检查仍把变量留给容器侧展开（$CS / ${NEO4J_AUTH#*/}）",
              "$CS" in healthcheck and "${NEO4J_AUTH#*/}" in healthcheck and "$$" not in healthcheck)
    used_std = {v for v in USED if v != "__MISSING__"}
    check("标准文件引用的变量均已在 .env.example 声明",
          used_std <= declared, "未声明: %s" % sorted(used_std - declared))
    check("标准文件没有缺值的必填变量", "__MISSING__" not in USED, str(sorted(USED.get("__MISSING__", []))))

    USED.clear()
    ovl_out = interpolate(raw_ovl, up_env, "docker-compose.weknora.yml")
    try:
        yaml.safe_load(ovl_out)
        check("插值后的叠加层仍是合法 YAML", True)
    except Exception as exc:  # noqa: BLE001
        check("插值后的叠加层仍是合法 YAML", False, repr(exc))
    check("叠加层只消费上游 NEO4J_* 与本目录 BODHI_* 变量",
          set(USED) <= {"NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_IMAGE",
                        "BODHI_DEPLOY_DIR", "BODHI_NEO4J_CYPHER_DIR"}, str(sorted(USED)))

    for missing in ("NEO4J_PASSWORD", "BODHI_DEPLOY_DIR", "BODHI_NEO4J_CYPHER_DIR"):
        env2 = dict(up_env)
        env2.pop(missing, None)
        try:
            interpolate(raw_ovl, env2, "docker-compose.weknora.yml")
            check("叠加层缺 %s 时应报错" % missing, False, "却插值通过")
        except KeyError as exc:
            check("叠加层缺 %s 时明确报错：%s" % (missing, str(exc)[:44]), True)

    print()
    print("=== 4. 挂载源路径 ===")
    check("deploy/bootstrap-neo4j.sh 存在", (DEPLOY / "bootstrap-neo4j.sh").is_file())
    check("deploy/.env.example 存在", (DEPLOY / ".env.example").is_file())
    cypher_dir = REPO / "artifacts" / "neo4j"
    check("artifacts/neo4j 产物目录存在（编译产物，不入库）", cypher_dir.is_dir())
    for name in ("00_constraints.cypher", "10_ontology.cypher", "20_cross_layer_queries.cypher"):
        check("产物存在：artifacts/neo4j/%s" % name, (cypher_dir / name).is_file())
    check("脚本默认挂载 ../artifacts/neo4j 相对 deploy/ 解析正确",
          (DEPLOY / ".." / "artifacts" / "neo4j").resolve() == cypher_dir.resolve())

    print()
    print("=== 5. 与上游 compose 合并仿真 ===")
    # 上游结构取证自 Tencent/WeKnora main 的 docker-compose.yml：
    # 网络 WeKnora-network、数据卷 neo4j-data、profile 下的 neo4j 服务
    upstream = {
        "services": {
            "frontend": {"networks": ["WeKnora-network"]},
            "app": {"networks": ["WeKnora-network"]},
            "neo4j": {"networks": ["WeKnora-network"], "profiles": ["neo4j"]},
        },
        "networks": {"WeKnora-network": {"driver": "bridge"}},
        "volumes": {"neo4j-data": None, "postgres-data": None},
    }
    merged_services = dict(upstream["services"])
    merged_services.update(ovl["services"])
    merged_networks = dict(upstream["networks"])
    merged_networks.update(ovl.get("networks") or {})
    bootstrap = merged_services["bodhi-ontology"]
    check("合并后 bodhi-ontology 引用的 WeKnora-network 由上游提供",
          all(n in merged_networks for n in bootstrap["networks"]), str(bootstrap["networks"]))
    check("合并后上游 neo4j 服务未被改写（仍只在 neo4j profile 下）",
          merged_services["neo4j"] == upstream["services"]["neo4j"])
    check("合并后 app/frontend 未被改写",
          merged_services["app"] == upstream["services"]["app"]
          and merged_services["frontend"] == upstream["services"]["frontend"])
    check("bodhi-ontology 是新服务名（不会与上游服务合并覆盖）",
          "bodhi-ontology" not in upstream["services"])
    check("容器名不与上游 WeKnora-* 命名冲突",
          bootstrap["container_name"] == "bodhi2-ontology-bootstrap"
          and not bootstrap["container_name"].startswith("WeKnora-"))
    check("bodhi-ontology 在 bodhi profile 下（普通 up -d 不会误启动）",
          bootstrap.get("profiles") == ["bodhi"])
    check("叠加层不写 depends_on（避开跨 profile 依赖）", "depends_on" not in bootstrap)

    print()
    print("=== 6. bootstrap-neo4j.sh 静态检查 ===")
    sh_raw = (DEPLOY / "bootstrap-neo4j.sh").read_bytes()
    sh = sh_raw.decode("utf-8")
    check("脚本为 LF（无 CR）", b"\r" not in sh_raw, "CR 数=%d" % sh_raw.count(b"\r"))
    check("脚本无 BOM", not sh_raw.startswith(b"\xef\xbb\xbf"))
    check("脚本无 TAB 缩进", "\t" not in sh)
    check("shebang 为 #!/bin/sh", sh.splitlines()[0] == "#!/bin/sh")
    check("开启 set -eu", "set -eu" in sh)
    check("缺 NEO4J_PASSWORD 时直接失败（不猜默认口令）", 'if [ -z "$DB_PASS" ]; then' in sh)
    check("灌库顺序固定为 00 -> 10",
          'FILES="00_constraints.cypher 10_ontology.cypher"' in sh)
    check("只执行 00/10，不执行 20_cross_layer_queries",
          'apply_file "$CYPHER_DIR/20' not in sh and "20_cross_layer_queries" in sh)
    check("脚本引用的两个 cypher 文件在产物目录里存在",
          all((cypher_dir / n).is_file() for n in ("00_constraints.cypher", "10_ontology.cypher")))
    check("结构配平：fi 至少 2 处（密码校验 + cypher-shell 查找）",
          len(re.findall(r"^\s*fi\s*$", sh, re.M)) >= 2)
    do_count = len(re.findall(r"\bdo\s*$", sh, re.M))
    done_count = len(re.findall(r"^\s*done\s*$", sh, re.M))
    check("结构配平：do 与 done 数量一致", do_count == done_count == 3,
          "do=%d done=%d" % (do_count, done_count))
    check("重试循环是 while :; do", len(re.findall(r"^\s*while :; do\s*$", sh, re.M)) == 1)
    check("应用函数有返回值语义（成功 return 0 / 失败 return 2）",
          "return 0" in sh and "return 2" in sh)
    check("退出码约定写在注释里",
          "退出码：0 成功 / 1 参数或文件缺失 / 2 连接成功但执行失败" in sh)
    # 预期投影规模：有编译产物时现场从 artifacts/neo4j/README.md 换算，再与脚本提示比对；
    # artifacts/ 是可重建物（已被 .gitignore 忽略），缺席时退化为只查脚本是否给了口径。
    proj_readme = cypher_dir / "README.md"
    expect = {}
    if proj_readme.is_file():
        got = dict(re.findall(
            r"^\| (classes|object_properties|data_properties|restrictions|外部占位节点) \| (\d+) \|",
            proj_readme.read_text(encoding="utf-8"), re.M))
        if len(got) == 5:
            expect["BodhiOntClass"] = int(got["classes"]) + int(got["外部占位节点"])
            expect["BodhiOntProperty"] = int(got["object_properties"]) + int(got["data_properties"])
            expect["BodhiRestriction"] = int(got["restrictions"])
    if expect:
        missing = [k for k, v in sorted(expect.items()) if ("%s %d" % (k, v)) not in sh]
        check("脚本提示的投影规模与产物一致（%s）"
              % " / ".join("%s %d" % (k, v) for k, v in sorted(expect.items())),
              not missing, "脚本缺：%s" % ", ".join(missing))
    else:
        check("脚本给出了预期投影规模（未找到 artifacts/neo4j/README.md，跳过与产物比对）",
              "BodhiOntClass" in sh and "BodhiOntProperty" in sh)

    print()
    print("=== 汇总 ===")
    print("  通过 %d 项，失败 %d 项" % (len(OK), len(BAD)))
    for name in BAD:
        print("  !! %s" % name)
    return 1 if BAD else 0


if __name__ == "__main__":
    _configure_stdout()
    sys.exit(main())
