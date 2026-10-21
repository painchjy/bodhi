"""相似度 + 页 id 派生（P2 切片 S1 ✓ 从 `server.py` 抽出，**实现逐字未改**）

只依赖标准库（`re/math/json/os/urllib/uuid`）⇒ 无环、可单测、可独立复用 ✓
"""
from __future__ import annotations

import json
import math
import os
import re
import uuid
from urllib import request as urlrequest


def normalize_name(text: str) -> str:
    return re.sub(r"[\s\u3000·、，,。.（）()【】\[\]:：;；\-—_/]+", "", (text or "").lower())


def _ngrams(text: str, n: int = 3) -> dict[str, int]:
    cleaned = re.sub(r"\s+", "", text or "")
    if len(cleaned) < n:
        return {cleaned: 1} if cleaned else {}
    grams: dict[str, int] = {}
    for i in range(len(cleaned) - n + 1):
        gram = cleaned[i:i + n]
        grams[gram] = grams.get(gram, 0) + 1
    return grams


def lexical_similarity(text_a: str, text_b: str) -> float:
    """字符 3-gram 余弦：中文不需要分词，短文本上表现稳定。"""
    a, b = _ngrams(text_a), _ngrams(text_b)
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[g] * b[g] for g in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """可选：调 OpenAI 兼容嵌入服务；未配置返回 None（调用方退回 lexical）。"""
    base = os.environ.get("BODHI_EMBED_BASE_URL", "").rstrip("/")
    key = os.environ.get("BODHI_EMBED_API_KEY", "")
    model = os.environ.get("BODHI_EMBED_MODEL", "")
    if not (base and model):
        return None
    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urlrequest.Request(base + "/embeddings", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urlrequest.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return [item["embedding"] for item in body["data"]]
    except Exception as exc:  # noqa: BLE001
        print("[mcp] 嵌入调用失败，退回 lexical：%s" % exc)
        return None


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def squash(text: str) -> str:
    """压掉所有空白（相似度/去重/标题归一的公共前置 ✓）"""
    return "".join((text or "").split())


def page_id_for(kb_id: str, slug: str) -> str:
    """**(知识库, slug) → 确定性页 id**。

    2026-09-24 修（用户实测）：旧实现只按 slug 派生 → **两个知识库里的同名页 id 相同** →
    `INSERT ... ON CONFLICT (id) DO UPDATE` 会把**别的库那一行**更新掉（回执却报成功、
    本库查不到该页）。现在把 kb 并入派生，跨库天然不冲突。
    迁移策略见 `_resolve_page_id`：本库已有同 slug 的页一律**沿用其现有 id**（老数据零迁移）。
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "bodhi-element:%s|%s" % (kb_id, slug)))
