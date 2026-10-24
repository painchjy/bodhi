"""证据/结论可信度评分（**可计算、可解释、可回归** ✓）

设计（2026-10-17 用户口径：提取质量 → 结论可信度 → 问题可信度 → 到阈值就无法判定 ✓）

## 一、事实的三种状态（关键区分 ✓）
| status | 含义 | 对结论的作用 |
|---|---|---|
| `present` | 文档里**有**（逐字 + 可回访定位 ✓） | 支持"符合/不符合" ✓ |
| `absent_verified` | 文档里**确认没有**（且能回访到"没有"的事实 ✓，如"全文无设备清单"） | **支持 failed** ✓（文档缺陷 ✓ 证据充分 ✓） |
| `unavailable` | 受评文档**之外**的数据源也拿不到（外部清单/系统挡板 ✓） | **降分** ⇒ 可能 `insufficient` ✓ |

> **2026-10-17 补充口径（用户）✓**：文档里没有、但**其他知识库/系统能拿到** ⇒ 属**文档遗漏** ✓ ⇒
> 仍记 `absent_verified` ✓（**这是"问题"** ✓ ⇒ `failed` ✓），**不是** `unavailable` ✗。
> 只有"**任何地方都拿不到**"才记 `unavailable` ✓（这才允许 `insufficient` ✓）。

> 一句话：**"缺文档要素"是证据 ✓；"缺外部数据"是缺证据 ✗** —— 这两者混在一起就会出现
> "该判不符合的判成无法判定"或反之 ✗（回归已经踩到 ✓）。

## 二、打分（0–1，权重透明 ✓）
```
score = 0.45 × 覆盖分      # Σ w(status)/n；present=1.0, absent_verified=1.0, unavailable=0.2
      + 0.30 × 置信分      # mean(confidence × source_type)，仅 present 事实
      + 0.15 × 证据分      # 有逐字 quote 且 locator 可回访的比例
      + 0.10 × 核查分      # fact_check：supported=1.0, not_supported=0.0, insufficient=0.3
```
**闸门（threshold）**：
- `score ≥ 0.80` ⇒ `high` ✓
- `0.60 ≤ score < 0.80` ⇒ `medium` ✓
- `score < 0.60` ⇒ **`low` ⇒ 结论一律 `insufficient`（无法判定）** ✓
  —— 阈值可由调用方传 `threshold=` 覆盖 ✓（不同领域可不同 ✓）

## 三、输出
```python
{"score": 0.72, "level": "medium", "gate": "ok|insufficient",
 "parts": {"coverage":…, "confidence":…, "evidence":…, "check":…},
 "reasons": ["facts: deploy_fig=absent_verified → 支持 failed ✓", "confidence 均值 0.70（image 0.7）", …]}
```
`reasons` 必须能**逐条回访** ✓（每个扣分点写清是哪个事实/哪条核查 ✓）。
"""

# 置信度 × 来源类型（口径与 knowledgeSpec 的 kind/tools 对齐 ✓）
CONF = {"high": 1.0, "medium": 0.7, "low": 0.4}
SOURCE = {"doc_table": 1.0, "doc_text": 0.9, "graph": 0.95, "image": 0.7,
          "doc_image": 0.85,      # 2026-10-18（用户口径 ✓）：**知识来自文档中的图** ✓（不是"图片本体" ✗）
          "external_query": 0.6, "external_stub": 0.4, "unknown": 0.5}
# 2026-10-17：**别名兼容** ✓（实测智能体会传 `document` / `table` / 数值 confidence ✗）
SOURCE_ALIAS = {"document": "doc_text", "doc": "doc_text", "text": "doc_text",
                "table": "doc_table", "img": "image", "figure": "image",
                "stub": "external_stub", "external": "external_query", "kb": "graph"}
STATUS_W = {"present": 1.0, "absent_verified": 1.0, "unavailable": 0.2}
CHECK = {"supported": 1.0, "not_supported": 0.0, "insufficient": 0.3}

W = {"coverage": 0.45, "confidence": 0.30, "evidence": 0.15, "check": 0.10}
DEFAULT_THRESHOLD = 0.60


def score_rule(facts: list | None = None, checks: list | None = None,
               threshold: float = DEFAULT_THRESHOLD) -> dict:
    """给**一条规则**的结论打分（facts = 该规则 required 的事实；checks = fact_check 结果）。"""
    facts = [f for f in (facts or []) if isinstance(f, dict)]
    checks = [c for c in (checks or []) if isinstance(c, dict)]
    reasons: list = []

    # ① 覆盖分：三种状态都给不同的权重 ✓
    if facts:
        cov = sum(STATUS_W.get(str(f.get("status") or "present"), 0.5) for f in facts) / len(facts)
        un = [str(f.get("id")) for f in facts if str(f.get("status")) == "unavailable"]
        av = [str(f.get("id")) for f in facts if str(f.get("status")) == "absent_verified"]
        if un:
            reasons.append("外部数据不可得：%s ⇒ 降覆盖分 ✓" % "、".join(un))
        if av:
            reasons.append("文档要素确认缺失：%s ⇒ 支持不符合 ✓（不是证据不足 ✗）" % "、".join(av))
    else:
        cov = 0.0
        reasons.append("规则未声明必需事实（facts 为空）⇒ 覆盖分 0 ✗")
    parts = {"coverage": cov}

    # ② 置信分：只对 present 事实加权（status 缺失视为 present ✓）
    pres = [f for f in facts if str(f.get("status") or "present") == "present"]
    if pres:
        vals = []
        for f in pres:
            _c = str(f.get("confidence") or "medium").strip().lower()
            if _c in CONF:
                c = CONF[_c]
            else:
                try:
                    c = max(0.0, min(1.0, float(_c)))     # 数值 confidence ✓（0.95 ⇒ 0.95 ✓）
                except Exception:  # noqa: BLE001
                    c = 0.7
            _s = str(f.get("source_type") or "unknown").strip().lower()
            _s = SOURCE_ALIAS.get(_s, _s)
            s = SOURCE.get(_s, 0.5)
            vals.append(c * s)
            if c * s < 0.7:
                reasons.append("低置信事实 %s（confidence=%s, source=%s）"
                               % (f.get("id"), f.get("confidence"), f.get("source_type")))
        parts["confidence"] = sum(vals) / len(vals)
    else:
        parts["confidence"] = 0.0
        reasons.append("没有 present 事实 ⇒ 置信分 0 ✗")

    # ③ 证据分：逐字 quote + 可回访 locator
    allf = facts or []
    if allf:
        ev = sum(1 for f in allf
                 if str(f.get("quote") or "").strip() and str(f.get("locator") or "").strip())
        parts["evidence"] = ev / len(allf)
        if ev < len(allf):
            reasons.append("%d/%d 条事实缺逐字证据或定位 ⇒ 证据分降到 %.2f"
                           % (len(allf) - ev, len(allf), parts["evidence"]))
    else:
        parts["evidence"] = 0.0

    # ④ 核查分（fact_check）
    if checks:
        cvals = [CHECK.get(str(c.get("verdict") or "insufficient"), 0.3) for c in checks]
        parts["check"] = sum(cvals) / len(cvals)
        bad = [str(c.get("question") or c.get("k") or "?") for c in checks
               if str(c.get("verdict")) == "not_supported"]
        if bad:
            reasons.append("核查反驳：%s ⇒ 核查分 %.2f" % ("、".join(bad), parts["check"]))
    else:
        parts["check"] = 0.6        # 未做核查 ⇒ 中性偏低（不奖励"没核查"✓）
        reasons.append("未做事实核查 ⇒ 核查分按 0.60（不奖励 ✓）")

    score = sum(W[k] * parts[k] for k in W)
    # 两条闸门**互斥** ✓（2026-10-17 修：之前硬闸门也会插一行"score<threshold"⇒ 自相矛盾 ✗）
    blocked = [str(f.get("id")) for f in facts
               if str(f.get("status")) == "unavailable" and bool(f.get("required", True))]
    if blocked:
        gate, level = "insufficient", "low"
        reasons.insert(0, "必需事实**外部不可得**：%s ⇒ 无法判定 ✓（拿不到就不得臆断 ✗；"
                          "分数 %.2f 仅作参考，不参与本闸门 ✓）" % ("、".join(blocked), score))
    elif score < threshold:
        gate, level = "insufficient", "low"
        reasons.insert(0, "score=%.2f **<** threshold=%.2f ⇒ 无法判定 ✓（证据不足 ✗ 不得写 failed ✗）"
                       % (score, threshold))
    else:
        gate = "ok"
        level = "high" if score >= 0.80 else "medium"
    return {"score": round(score, 3), "level": level, "gate": gate,
            "threshold": threshold, "parts": {k: round(v, 3) for k, v in parts.items()},
            "reasons": reasons}
