"""把 `skills/dist/*.zip` 注册成 WeKnora **原生技能**（Admin API），并盯安装结果。

前置（见 deploy/weknora-fork/enable_sandbox.sh）：
  - app 容器挂载 docker.sock 且 `WEKNORA_SANDBOX_DOCKER_ENABLED=true`（已重建）；
  - 沙箱基础镜像在 daemon 上：`docker pull wechatopenai/weknora-sandbox:latest`。

流程（全走 app 的公开 API，不直接写库）：
  1) （可选）建 sandbox config：`POST /api/v1/sandbox-configs`
     body `{"name":..., "description":..., "config":{"sandbox_type":"docker","docker":{"image":...}}}`
  2) 每个 bundle：`POST /api/v1/sandbox-configs/{cfg}/skills`（multipart 字段名 `file`）
  3) 轮询 `GET /api/v1/sandbox-configs/{cfg}/skills` 直到全部 ready/failed
  4) `GET /api/v1/skills` 看智能体可见的技能清单

鉴权：DB `auth_tokens` 里最新一条未过期 access_token（与 try_agent_chat.py 同口径）。

用法
----
    python3 deploy/weknora-fork/register_native_skills.py --list
    python3 deploy/weknora-fork/register_native_skills.py --ensure-config
    python3 deploy/weknora-fork/register_native_skills.py --upload      # 传 dist/*.zip 并盯安装
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools" / "ke-core"))
import ke_db  # noqa: E402

BASE = "http://127.0.0.1:8080/api/v1"
REPO = pathlib.Path(__file__).resolve().parents[2]
DIST = REPO / "skills" / "dist"
CFG_NAME = "bodhi-skills"
BASE_IMAGE = "wechatopenai/weknora-sandbox:latest"


def token() -> str:
    rows = ke_db.psql_csv("SELECT token FROM auth_tokens WHERE is_revoked = false "
                          "AND token_type = 'access_token' AND expires_at > now() "
                          "ORDER BY created_at DESC LIMIT 1")
    if not rows:
        raise SystemExit("DB 里没有有效 access_token（先在 UI 登录一次）")
    return rows[0]["token"]


def call(method: str, path: str, tok: str, body: dict | None = None,
         files: list[tuple[str, pathlib.Path]] | None = None, timeout: int = 180):
    headers = {"Authorization": "Bearer " + tok}
    data = None
    if files:
        boundary = "----bodhi%d" % int(time.time() * 1000)
        chunks = []
        for field, fp in files:
            chunks.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                           "Content-Type: application/zip\r\n\r\n" % (boundary, field, fp.name)).encode())
            chunks.append(fp.read_bytes())
            chunks.append(b"\r\n")
        chunks.append(("--%s--\r\n" % boundary).encode())
        data = b"".join(chunks)
        headers["Content-Type"] = "multipart/form-data; boundary=%s" % boundary
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return {"error": exc.code, "body": exc.read().decode("utf-8", "replace")[:500]}
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__, "body": str(exc)[:300]}


def configs(tok: str) -> list[dict]:
    res = call("GET", "/sandbox-configs", tok)
    data = res.get("data") if isinstance(res, dict) else None
    if isinstance(data, dict):
        return data.get("items") or data.get("configs") or []
    return data or []


def skills_of(tok: str, cfg_id: str) -> list[dict]:
    res = call("GET", "/sandbox-configs/%s/skills" % cfg_id, tok)
    data = res.get("data") if isinstance(res, dict) else None
    if isinstance(data, dict):
        return data.get("items") or data.get("skills") or []
    return data or []


def ensure_config(tok: str) -> dict:
    for cfg in configs(tok):
        if cfg.get("name") == CFG_NAME:
            print("   已有配置 %s（%s）" % (cfg.get("id"), cfg.get("sandbox_type")))
            return cfg
    res = call("POST", "/sandbox-configs", tok,
               {"name": CFG_NAME, "description": "bodhi2 技能库（与 MCP 同源打包）",
                "config": {"sandbox_type": "docker", "docker": {"image": BASE_IMAGE}}})
    print("   建配置：%s" % json.dumps(res, ensure_ascii=False)[:400])
    data = (res or {}).get("data") or res
    return data if isinstance(data, dict) else {}


def main() -> int:
    ap = argparse.ArgumentParser(description="注册原生技能（上传 bundle + 盯安装）")
    ap.add_argument("--list", action="store_true", help="列配置与技能")
    ap.add_argument("--ensure-config", action="store_true", help="只确保 sandbox config 存在")
    ap.add_argument("--upload", action="store_true", help="上传 skills/dist/*.zip 并盯安装")
    ap.add_argument("--minutes", type=int, default=12, help="盯安装的最长分钟数")
    args = ap.parse_args()
    tok = token()

    if args.list:
        print("== sandbox configs")
        for cfg in configs(tok):
            print("   %s | %s | %s | %s"
                  % (cfg.get("id"), cfg.get("name"), cfg.get("sandbox_type"), cfg.get("updated_at")))
        print("== 技能（/skills 可用清单）")
        print("   " + json.dumps(call("GET", "/skills", tok), ensure_ascii=False)[:600])
        return 0

    cfg = ensure_config(tok)
    if not cfg.get("id"):
        print("!! 没有可用的 sandbox config（先 enable_sandbox.sh --apply）")
        return 1
    if args.ensure_config and not args.upload:
        return 0

    if args.upload:
        zips = sorted(DIST.glob("*.zip"))
        if not zips:
            print("!! skills/dist 里没有 zip：先跑 python3 tools/skills/bundle.py")
            return 1
        for zp in zips:
            res = call("POST", "/sandbox-configs/%s/skills" % cfg["id"], tok, files=[("file", zp)])
            print("   %-34s %s" % (zp.name, json.dumps(res, ensure_ascii=False)[:240]))

    deadline = time.time() + args.minutes * 60
    while time.time() < deadline:
        items = skills_of(tok, cfg["id"])
        print("   %s  %s" % (time.strftime("%H:%M:%S"),
                             [(i.get("name"), i.get("status")) for i in items]))
        if items and all(i.get("status") in ("ready", "failed") for i in items):
            for i in items:
                if i.get("status") == "failed":
                    print("   ✗ %s: %s" % (i.get("name"), str(i.get("error"))[:200]))
            print("   /skills → %s" % json.dumps(call("GET", "/skills", tok),
                                                ensure_ascii=False)[:400])
            break
        time.sleep(20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
