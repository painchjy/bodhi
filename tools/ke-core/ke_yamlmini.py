"""ke-core · 最小 YAML 子集解析（**零依赖**兜底）。

为什么需要
----------
`skills/<id>/SKILL.md` 的 front-matter 是 YAML，`server.load_skills()` 原本用 PyYAML 解析。
但本项目的交付口径是**只用 Python 标准库**（容器镜像不装 pip 包也能跑），而干净镜像里没有 PyYAML
→ `skills()` 直接报 `No module named 'yaml'`（2026-09-21 在最小容器里实测到）。

本模块只实现 SKILL.md front-matter **实际用到**的那个子集：

    key: 标量值（可含 `:`/`[]`/引号/中文）
    key: [a, b, c]          流式列表
    key:
      k1: v1                一级块映射
      k2: v2
    # 整行注释 / 空行忽略

调用方（server.py）优先用 PyYAML，取不到才回退到这里 —— 两者对上述输入结果一致。
"""

from __future__ import annotations

import re

_SCALAR_LIST = re.compile(r"^\[(.*)\]$")


def _scalar(raw: str):
    """标量取值：去注释、去成对引号、识别流式列表与布尔/数字。"""
    text = raw.strip()
    if text.startswith("#"):
        return None
    # 行尾注释：只认「空格+#」且不在引号内的简单情形（front-matter 里很少出现）
    if " #" in text and not text.lstrip().startswith(('"', "'")):
        text = text.split(" #", 1)[0].rstrip()
    if not text:
        return ""
    m = _SCALAR_LIST.match(text)
    if m:
        inner = m.group(1).strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def safe_load(text: str) -> dict:
    """解析 front-matter（无 `---` 包裹）为 dict；解析不了的行走标量，绝不抛。"""
    out: dict = {}
    current: str | None = None
    for line in (text or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if stripped.startswith("- "):                     # 块列表项
            if current and isinstance(out.get(current), list):
                out[current].append(_scalar(stripped[2:]))
            continue
        if ":" not in stripped:
            continue
        key, _, raw = stripped.partition(":")
        key = key.strip()
        if not key:
            continue
        if indent == 0:
            value = _scalar(raw)
            if value == "":                               # `key:` 后面是嵌套块
                out[key] = {}
                current = key
            else:
                out[key] = value
                current = None
        elif current and isinstance(out.get(current), dict):   # 一级嵌套映射
            out[current][key] = _scalar(raw)
        elif current and isinstance(out.get(current), list):
            continue
    return out


def load_skill_front_matter(path) -> dict:
    """便捷入口：直接读一个 SKILL.md 文件，返回其 front-matter dict（无 front-matter 返回 {}）。"""
    import pathlib  # noqa: PLC0415

    text = pathlib.Path(path).read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    return safe_load(parts[1])
