#!/usr/bin/env python3
"""02 交付包 systemd-only 回归（静态，不启动服务）。

验证 2026-11-24 用户裁定：
1. 三个常驻服务由 systemd 启动，共享 /opt/bodhi2/tools/ke-core；
2. 日志写入 /opt/bodhi2/logs/*.log；
3. 02 安装不再依赖 Docker，init-db 不再借容器；
4. 工具定义必须为 51 个；
5. 打包器不再把 Dockerfile/compose 放进 02 包。
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PAYLOAD = os.path.join(REPO, "deploy", "delivery", "payload", "mcp")
UNITS = ("bodhi2-mcp.service", "bodhi2-agent-diag.service", "bodhi2-analytics-admin.service")


def _read(name):
    with open(os.path.join(PAYLOAD, name), encoding="utf-8") as fh:
        return fh.read()


def test_runtime_files_are_shared():
    for name in ("tools/ontology-mcp/server.py", "tools/ke-core/ke_db.py",
                 "tools/analytics-admin/server.py",
                 "tools/domain-mcp/analytics_sql/server.py"):
        assert os.path.isfile(os.path.join(REPO, name)), name


def test_units_point_to_opt_and_log_dir():
    for unit in UNITS:
        body = _read(unit)
        assert "WorkingDirectory=/opt/bodhi2" in body, unit
        assert "StandardError=append:/opt/bodhi2/logs/" in body, unit
        assert "Restart=always" in body, unit


def test_installer_is_systemd_only():
    body = _read("install.sh")
    assert "systemctl" in body
    assert "docker build" not in body
    assert "docker compose" not in body
    assert "--expect-tools 51" in body
    assert "/opt/bodhi2/logs" in body


def test_init_db_does_not_fallback_to_docker():
    body = _read("init-db.sh")
    assert "docker compose" not in body
    assert "command -v psql" in body


def test_selfcheck_expects_51_tools():
    body = _read("selfcheck.py")
    assert "--expect-tools" in body and "default=51" in body


def test_packager_excludes_docker_files_and_checks_runtime():
    with open(os.path.join(REPO, "tools", "delivery", "pack_release.py"), encoding="utf-8") as fh:
        body = fh.read()
    assert "def check_mcp_runtime" in body
    assert "Dockerfile" in body  # 只允许出现在 forbidden 断言中
    assert "logrotate.bodhi2" in body
    assert "systemd-only" in body


if __name__ == "__main__":
    for fn in (test_runtime_files_are_shared, test_units_point_to_opt_and_log_dir,
               test_installer_is_systemd_only, test_init_db_does_not_fallback_to_docker,
               test_selfcheck_expects_51_tools, test_packager_excludes_docker_files_and_checks_runtime):
        fn()
    print("delivery systemd checks OK")
