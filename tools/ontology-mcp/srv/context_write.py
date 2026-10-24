"""上下文写入口（批 3b 迁自 `server.py`，**实现逐字未改**）

三件事都是**薄封装**：守门（写权限 / ticket / `acknowledge_risks` / 领域库零写入）全在 `ke_context` 里，
本模块只做参数归整 ✓。MCP 工具（`srv/handlers/context_.py`）与 REST 路由（`srv/http.py`）共用同一份
实现 —— 不依赖 `bind()` 注入的名字，避免 S9 那种"运行期 NameError"。
"""
from __future__ import annotations

import ke_context


def context_concept_apply(slug: str, ticket: str = "", acknowledge_risks: list | None = None,
                          definition: str = "", standard_name: str = "", actor: str = "") -> dict:
    """**写**：把同名组落成「企业共享概念模型」里的概念页（`ke_context.concept_apply` 的入口）。

    守门在 ke_context 里：写权限（`ke_db.assert_can_write`，调用者租户来自 `X-Bodhi-Tenant` 头）
    + ticket（影响面指纹）+ `acknowledge_risks` 完全一致；领域库**零写入**。
    """
    return ke_context.concept_apply(str(slug or ""), str(ticket or ""),
                                    [str(x) for x in (acknowledge_risks or [])],
                                    definition=str(definition or ""),
                                    standard_name=str(standard_name or ""),
                                    actor=str(actor or ""))


def context_concept_rollback(ticket: str) -> dict:
    """**写**：回滚概念页 apply（幂等）+ 刷新缓存。"""
    return ke_context.concept_rollback(str(ticket or ""))





def context_authority_apply(action: str, slug: str, master: str = "", kb: str = "",
                            ticket: str = "", acknowledge_risks: list | None = None,
                            actor: str = "", apply: bool = True) -> dict:
    """**写**：`decide` 写概念库（登记权威）；`pull` 写副本库（从权威复制）；
    `detach` 解除副本绑定（摘登记 + 改自述；`apply=false` 只预览）。
    """
    act = str(action or "").strip().lower()
    ack = [str(x) for x in (acknowledge_risks or [])]
    if act == "decide":
        return ke_context.authority_decide(str(slug or ""), str(master or ""), str(ticket or ""), ack,
                                           actor=str(actor or ""))
    if act == "pull":
        return ke_context.authority_pull(str(kb or ""), str(slug or ""), str(ticket or ""), ack,
                                         actor=str(actor or ""), apply=True)
    if act == "detach":
        return ke_context.authority_detach(str(kb or ""), str(slug or ""), str(ticket or ""), ack,
                                           actor=str(actor or ""), apply=bool(apply))
    return {"error": "action 只能是 decide / pull / detach"}
