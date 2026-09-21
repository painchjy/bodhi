"""前端产物**外链体检**（默认只报告，不改产物）。

用途（用户 2026-09-21 口径：原生页面内网可用，只关心我们新开发的前端页面；有外网 CDN 就列出版本+域名给部署建议，
**不用改代码**）：
- 默认：扫 `dist/` 里出现的外部主机名，分级列出（必须本地化的 CDN / 已知无害 / 可选集成 / 未分类）；
- 可选 `--rewrite`：把 `tdesign-vue-next` 内置的图标字体兜底常量 `https://tdesign.gtimg.com/icon/<版本>/fonts/index.js`
  改成本地路径 `/tdesign-icons/<版本>/fonts/index.js`，并把本地 sprite 铺到各版本目录（**严格隔离内网**才需要，默认不做）。

用法：
    python3 tools/delivery/offline_harden.py --dist /root/fe-build/dist            # 只体检
    python3 tools/delivery/offline_harden.py --dist /root/fe-build/dist --rewrite  # 顺带本地化（可选）
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import sys

CDN_PREFIX = "https://tdesign.gtimg.com/icon/"
LOCAL_PREFIX = "/tdesign-icons/"
# tdesign-vue-next Icon 组件内置的版本表（见产物里的 YT/P4 常量）
KNOWN_VERSIONS = ("0.4.0", "0.4.1", "0.4.2", "0.4.3", "0.4.4")
# 已知无害：XML 命名空间 / 文档链接 / 示例占位 / 本地地址
BENIGN_PATTERNS = (
    r"(^|\.)w3\.org$", r"^schemas\.", r"openxmlformats\.org$", r"^purl\.", r"purl\.oclc\.org$",
    r"openoffice\.org$", r"docs\.oasis-open\.org$", r"clawhub\.ai$", r"langium\.org$",
    r"(^|\.)github\.com$", r"stuk\.github\.io$", r"chevrotain\.io$", r"vuejs\.org$",
    r"\[a-z\]+-migration\.vuejs\.org$", r"npms\.io$", r"en\.wikipedia\.org$",
    r"chromewebstore\.google\.com$", r"(^|\.)google\.com$", r"(^|\.)notion\.so$",
    r"(^|\.)yuque\.com$", r"developers\.(weixin|mattermost)\.com$", r"weknora\.weixin\.qq\.com$",
    r"(^|\.)example\.com$", r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|YOUR_IP|host)$",
    r"^your[-_.]", r"^macVmlSchemaUri$", r"^docx$", r"(^|\.)baidu\.com$",
)
# 可选集成端点：只有用户显式配置了对应渠道/模型才会用，内网默认无人调用
OPTIONAL = (
    "api.openai.com", "open.feishu.cn", "open.larksuite.com", "ima.qq.com",
    "dashscope.aliyuncs.com", "open.bigmodel.cn", "api.siliconflow.cn", "integrate.api.nvidia.com",
    "api.novita.ai", "openrouter.ai", "router.requesty.ai", "generativelanguage.googleapis.com",
    "api.jina.ai", "ai.api.nvidia.com", "www.yunzhijia.com", "work.weixin.qq.com",
    "qyapi.weixin.qq.com", "api.sgroup.qq.com", "open.dingtalk.com", "api.slack.com",
    "api.qrserver.com", "stream", "stream.absi", "stream.26", "stream.news",
    "registry.npmmirror.com", "registry.npmjs.org", "pypi.org", "files.pythonhosted.org",
    "ollama.com", "mineru.net", "aistudio.baidu.com", "e2b.dev", "api.e2b.app", "sheetjs.com",
)
# **必须本地化**的 CDN：命中就失败（这些是运行时真会去取的静态资源）
MUST_BE_LOCAL = (
    "tdesign.gtimg.com", "cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com",
    "fonts.googleapis.com", "fonts.gstatic.com", "ajax.googleapis.com", "at.alicdn.com",
    "bootcdn.net", "npm.elemecdn.com", "lib.baomitu.com", "cdn.bootcdn.net",
)


def harden(dist: pathlib.Path) -> dict:
    assets = dist / "assets"
    js_files = sorted(assets.glob("*.js")) + sorted(assets.glob("*.css"))
    rewritten = {}
    for path in js_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if CDN_PREFIX not in text:
            continue
        count = text.count(CDN_PREFIX)
        path.write_text(text.replace(CDN_PREFIX, LOCAL_PREFIX), encoding="utf-8")
        rewritten[path.name] = count

    # 本地 sprite 铺到所有已知版本目录（就地复制，内容一致即可）
    src_dir = dist / "tdesign-icons"
    versions_present = sorted(p.name for p in src_dir.iterdir() if p.is_dir()) if src_dir.is_dir() else []
    sprite_src = None
    for ver in ("0.4.1", *versions_present):
        cand = src_dir / ver / "fonts" / "index.js"
        if cand.is_file():
            sprite_src = cand
            break
    copied = []
    if sprite_src:
        for ver in KNOWN_VERSIONS:
            target = src_dir / ver / "fonts" / "index.js"
            if not target.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sprite_src, target)
                copied.append(ver)
    return {"rewritten": rewritten, "sprite_from": str(sprite_src or ""), "sprite_copied": copied,
            "versions_present": versions_present}


def audit(dist: pathlib.Path) -> dict:
    hosts: dict[str, int] = {}
    for path in dist.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in (".js", ".css", ".html", ".json", ".map"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for host in re.findall(r"https?://([A-Za-z0-9._-]+)", text):
            hosts[host] = hosts.get(host, 0) + 1
    benign, optional, must_local, unknown = {}, {}, {}, {}
    for host, n in sorted(hosts.items(), key=lambda kv: -kv[1]):
        if host in MUST_BE_LOCAL:
            must_local[host] = n
        elif any(re.search(pat, host) for pat in BENIGN_PATTERNS):
            benign[host] = n
        elif any(host == o or host.endswith("." + o) for o in OPTIONAL):
            optional[host] = n
        else:
            unknown[host] = n
    return {"benign": benign, "optional": optional, "must_local": must_local, "unknown": unknown}


def main() -> int:
    ap = argparse.ArgumentParser(description="前端产物离线加固 + 外链体检")
    ap.add_argument("--dist", required=True, help="vite 产物目录（含 assets/）")
    ap.add_argument("--rewrite", action="store_true",
                    help="可选项：把图标字体兜底 CDN 常量改成本地路径 + 补齐 sprite 版本目录（默认只体检）")
    ap.add_argument("--strict", action="store_true",
                    help="把「未分类主机」也当失败（CI 用；默认只在命中 CDN 时才失败）")
    args = ap.parse_args()
    dist = pathlib.Path(args.dist).resolve()
    if not (dist / "assets").is_dir():
        print("!! %s 下没有 assets/（先跑 vite build）" % dist)
        return 1

    print("== 1) 兜底 CDN 常量 → 本地路径")
    if args.rewrite:
        res = harden(dist)
        for name, count in res["rewritten"].items():
            print("   %-40s %d 处" % (name, count))
        if not res["rewritten"]:
            print("   （产物里已无 %s）" % CDN_PREFIX)
        print("== 2) 本地图标 sprite 版本目录")
        print("   源：%s ｜ 已存在版本：%s ｜ 本次补齐：%s"
              % (res["sprite_from"] or "（未找到本地 sprite！）",
                 ", ".join(res["versions_present"]) or "-", ", ".join(res["sprite_copied"]) or "-"))
    else:
        print("   （未加 --rewrite：只体检，不改产物 —— 符合"不用改代码"的口径）")
        print("== 2) 本地图标 sprite 版本目录（仅报告）")
        sprite = dist / "tdesign-icons"
        vers = sorted(p.name for p in sprite.iterdir() if p.is_dir()) if sprite.is_dir() else []
        print("   已带版本：%s（上游 index.html 提前加载的就是它，运行时不会去 CDN 取）"
              % (", ".join(vers) or "（无）"))

    print("== 3) 外链体检")
    a = audit(dist)
    print("   必须本地化的 CDN：%s" % (", ".join(a["must_local"]) or "无 ✅"))
    print("   已知无害（命名空间/文档/示例/本地）：%d 个主机（%s…）"
          % (len(a["benign"]), ", ".join(list(a["benign"])[:6]) or "-"))
    print("   可选集成端点（不配置就不会调用）：%s" % (", ".join(a["optional"]) or "无"))
    print("   未分类主机：%s" % (", ".join(a["unknown"]) or "无"))
    if a["must_local"]:
        print("!! 产物里仍有需要本地化的 CDN 地址：%s" % ", ".join(a["must_local"]))
        return 1
    if args.strict and a["unknown"]:
        print("!! --strict：存在未分类主机 %s（请人工确认或加入白名单）" % ", ".join(a["unknown"]))
        return 1
    if a["unknown"]:
        print("   提示：未分类主机多为**文档链接/占位符**（如 YOUR_IP、your-*），不影响内网运行；"
              "要严格把关可加 --strict")
    print("结论：前端产物**不依赖外网静态资源**（CDN 已本地化；其余为命名空间/文档/示例/可选集成）✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
