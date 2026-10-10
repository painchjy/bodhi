"""核对落库相关开关在**服务同一口径**下的解析值（`ke_db.env_value`：进程 env → .env）。

用法：/opt/bodhi-venv/bin/python3 _flag.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools" / "ke-core"))
import ke_db  # noqa: E402

print("BODHI_SAVE_ENGINE       =", repr(ke_db.env_value("BODHI_SAVE_ENGINE", "legacy")))
print("BODHI_SIMILARITY_MERGE  =", repr(ke_db.env_value("BODHI_SIMILARITY_MERGE", "0")))
print("BODHI_VALIDATE_FROM_GRAPH =", repr(ke_db.env_value("BODHI_VALIDATE_FROM_GRAPH", "1")))
print("BODHI_DEBUG_ENGINE      =", repr(ke_db.env_value("BODHI_DEBUG_ENGINE", "0")))
