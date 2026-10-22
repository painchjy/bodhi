"""MCP server 常量（P2 切片 S3 ✓ 从 `server.py` 抽出，**取值表达式逐字一致**）

只放**与领域无关的路径/阈值**；业务口径常量留在 `server.py`（S8 再收）。
"""
from __future__ import annotations

import os
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[3]      # tools/ontology-mcp/srv/ → 仓库根
DOMAIN_STATE_DIR = REPO / "state" / "domain_sessions"
EXPORT_DIR = REPO / "exports"        # 导出产物目录（kb_export 落这里；HTTP 上传/下载也从这里）

try:
    DEFAULT_ROUND_BUDGET = int(os.environ.get("BODHI_ROUND_BUDGET_TOKENS") or 8000)
except ValueError:
    DEFAULT_ROUND_BUDGET = 8000
try:
    CHARS_PER_TOKEN = float(os.environ.get("BODHI_CHARS_PER_TOKEN") or 1.6)
except ValueError:
    CHARS_PER_TOKEN = 1.6

# MCP protocol/server identity (P2 S9)
PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "bodhi-ontology-mcp"
SERVER_VERSION = "0.1.0"
