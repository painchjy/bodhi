"""产物：各模块的「轻量版」提示词（`artifacts/prompts/<模块>_light.md`）。

为什么单独一份产物
------------------
抽取提示词**不注入完整 TTL**（体积大、语法噪音多），而用人工撰写的轻量版 md：

    ontology/BMM轻量版.md      ontology/EA轻量版.md

本发射器只做两件事：把真源**搬到产物目录**（给 fork / 提示词构建脚本一个稳定路径）、
在文件头标注真源。这样 "哪些模块有轻量版" 可以在产物里枚举
（`ontology_index.json` 的 `light_available`），而无需让消费者猜文件名。

未登记轻量版的模块（如三个扩展模块）直接跳过，**不产出空文件**——避免 fork 侧把空提示词当成有效内容。
"""

from __future__ import annotations

from pathlib import Path

from ontology_compiler.config import ARTIFACTS_DIR
from ontology_compiler.emitters._common import write_text


class LightPromptEmitter:
    name = "light_prompts"

    def emit(self, bundle, out_dir: Path | None = None) -> list[str]:
        root = Path(out_dir) if out_dir is not None else Path(ARTIFACTS_DIR)
        written: list[str] = []
        for key in bundle.module_order():
            spec = bundle.modules[key]
            if spec.light_file is None:
                continue
            if not spec.light_ready():
                raise FileNotFoundError(
                    "模块 %s 登记了轻量版但文件不存在：%s" % (key, spec.rel_light())
                )
            body = Path(spec.light_file).read_text(encoding="utf-8")
            header = (
                "<!-- 由 tools/ontology-compiler 从真源复制 —— 请勿手改。 -->\n"
                "<!-- 真源：%s -->\n"
                "<!-- 重新生成：python tools/ontology-compiler/compile.py compile -->\n\n"
                % spec.rel_light()
            )
            target = root / "prompts" / ("%s_light.md" % key)
            written.append(write_text(target, header + body))
        return written
