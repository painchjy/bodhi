"""异步任务表（批 3b 迁自 `server.py`，**实现逐字未改**）

⚠️ 现状：**没有任何工具再提交任务**
- 整篇异步抽取：2026-09-22 退役（改 `domain_modeling` 技能分批交互）
- 总览页刷新：2026-10-24 退役（`service_overview` 下线 ⇒ 走 CLI `refresh_design.py --overview`）

⇒ 任务表恒空，`job_status` 保留只为**兼容旧提示词/脚本**（回执照实说「没有这个任务」）。
将来若要再加异步工具，请复用本模块的 `JOBS` / `JOBS_LOCK`（别各自建 dict ✗）。
"""
from __future__ import annotations

import threading

# ---------------------------------------------------------------------------
# 异步任务层（2026-09-19 实测的必要性）
#   app 侧 MCP 客户端的超时是**硬编码 60 秒且无配置项**（app env / 上游 compose 里都没有
#   MCP 超时开关），而单次抽取的 LLM 调用就要 ~57s → 同步调用必然 context deadline exceeded。
#   因此工具改为「立即受理 + 后台执行」，用 job_status 轮询结果。
# ---------------------------------------------------------------------------
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


DETAIL_KEYS = ("created", "merged", "violations", "unmatched")


def _job_brief(result: dict) -> dict:
    """给智能体看的任务简报：**既要计数也要明细**。

    2026-09-19 用户实测：旧实现把列表一律转成 len()，于是智能体只看到
    `unmatched=7` / `pending=5` 却拿不到名称与理由，无法汇报也无法人工跟进。
    现在同时给出 `*_count` 与 `*`（明细，最多 20 条，控制体积）。
    """
    out: dict = {}
    for k in ("status", "started_at", "finished_at", "doc_title", "elements",
              "relationships", "folders_synced", "error", "resolved_note"):
        if k in result:
            out[k] = result[k]
    for k in DETAIL_KEYS:
        items = result.get(k)
        if isinstance(items, list):
            out[k + "_count"] = len(items)
            out[k] = items[:20]
    return out


def job_status(job_id: str = "") -> dict:
    """查询**异步任务**的状态/结果（当前唯一用户：`service_overview` 刷新）。

    不给 job_id 则列出最近的任务。

    沿革：本工具原名 `job_status`，只服务于「整篇异步抽取」；该抽取已于 2026-09-22
    退役（改为 `domain_modeling` 技能分批交互），任务池保留给总览页刷新，故改名去歧义。
    """
    with JOBS_LOCK:
        if job_id:
            job = JOBS.get(job_id)
            if not job:
                return {"job_id": job_id, "status": "unknown",
                        "note": "没有这个任务（可能服务重启过；重启会清空内存中的任务表）"}
            return {"job_id": job_id, "args": None, **_job_brief(job)}
        return {"count": len(JOBS),
                "jobs": {k: {"status": v.get("status"), "started_at": v.get("started_at"),
                             "finished_at": v.get("finished_at")}
                         for k, v in list(JOBS.items())}}
