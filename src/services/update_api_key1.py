"""更新 LLM API Key 服务 — 通过网关 sceneCode 模式申请新密钥"""

from ..config import settings


async def update_api_key(extraction_service, scene_code: str = "") -> dict:
    """向 LLM 网关申请新的 API Key，并更新 settings 及客户端缓存"""
    import httpx
    code = scene_code or settings.scene_code
    base = (settings.api_key_base_url or settings.llm_base_url).rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = f"{base}/updateapikey.do"
    payload = {
        "TRAN_PROCESS": settings.api_key_method,
        "REQ_BODY": {
            "param": {"sceneCode": code}
        }
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    rep_body = data.get("REP_BODY", {})
    resulte = rep_body.get("resulte", {})
    if isinstance(resulte, dict):
        api_key = resulte.get("apiKey", "")
    else:
        api_key = str(resulte) if resulte else ""
    if api_key:
        settings.llm_api_key = api_key
        extraction_service.client = None  # 重置客户端缓存
        print(f"[Bodhi] 🔑 LLM API Key 已更新: {api_key[:8]}...", flush=True)
    return {"api_key": api_key, "raw_response": data}
