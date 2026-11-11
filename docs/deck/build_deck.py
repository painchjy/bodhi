"""Build the native, text-editable Bodhi2 PPTX (and PNG previews).

Usage:
    python docs/deck/build_deck.py              # PPTX + preview PNGs
    python docs/deck/build_deck.py --no-preview # only PPTX
    python docs/deck/build_deck.py --preview    # only PNG previews

The deck is defined in slides.py (1600x900 design units). The PPTX is
13.333 x 7.5 in: every text is a real PowerPoint text box, all graphics are
shapes (no full-slide images).
"""

from __future__ import annotations

import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from deck_lib import render_png, render_pptx  # noqa: E402
from slides import SLIDES  # noqa: E402

PPTX = HERE / "bodhi2-architecture.pptx"
PREVIEW = HERE / "preview"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-preview", action="store_true", help="只生成 PPTX")
    parser.add_argument("--preview", action="store_true", help="只生成 PNG 预览")
    args = parser.parse_args()

    if not args.preview:
        candidates = [
            PPTX,
            PPTX.with_name("bodhi2-architecture-v2.pptx"),
            PPTX.with_name("bodhi2-architecture-v3.pptx"),
            PPTX.with_name("bodhi2-architecture-v4.pptx"),
        ]
        target = None
        for cand in candidates:
            try:
                render_pptx(SLIDES, cand)
                target = cand
                break
            except PermissionError:
                continue
        if target is None:
            import time
            target = PPTX.with_name(f"bodhi2-architecture-{time.strftime('%Y%m%d-%H%M')}.pptx")
            render_pptx(SLIDES, target)
        if target != PPTX:
            print(f"[pptx] 原文件被占用，已另存为 {target}")
        info = HERE / "build_info.txt"
        info.write_text(f"slides={len(SLIDES)}\npptx={target.name}\nbytes={target.stat().st_size}\n", encoding="utf-8")
        print(f"[pptx] {target} ({target.stat().st_size // 1024} KB, {len(SLIDES)} slides)")

    if not args.no_preview:
        PREVIEW.mkdir(parents=True, exist_ok=True)
        for i, slide in enumerate(SLIDES, 1):
            out = PREVIEW / f"slide-{i}.png"
            render_png(slide, out)
            print(f"[preview] {i}/{len(SLIDES)} -> {out.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
