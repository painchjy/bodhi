"""多媒体切片（A1-7）：`image_extract`（文档内嵌图，只读）。

迁移来源：`server.py::_call_tool_impl` 的 `if name == "image_extract"` 分支（**参数解析逐字照搬**）✓
实现：`srv/images.py` —— 取 `resource://` 引用 → 解析宿主机文件 → base64 data URL（多模态模型可读）
"""
from __future__ import annotations

from srv.handlers import handle
from srv.images import image_extract


@handle("image_extract")
def image_extract_tool(args: dict) -> dict:
    return image_extract(str(args.get("kb_id", "")), str(args.get("knowledge_id", "")),
                         int(args.get("figure_no", 0) or 0))
