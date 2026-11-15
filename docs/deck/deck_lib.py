"""Tiny layout DSL + renderers for the Bodhi2 deck.

Design canvas: 1600 x 900 (16:9). PPTX: 13.333 x 7.5 in.
All text is emitted as real PowerPoint text boxes (editable); graphics are shapes.
A Pillow preview is rendered from the same spec so layout can be checked without Office.
"""

from __future__ import annotations

import pathlib
import re

CJK = re.compile(r"[\u3400-\u9fff]")

# ---------- palette ----------
INK = "101826"
MUTED = "5C6675"
FAINT = "8A93A0"
PAPER = "F5F2E9"
PAPER2 = "FBF9F3"
WHITE = "FFFFFF"
LINE = "DDD7C9"
VIOLET = "6D4AFF"
CYAN = "0E8F8C"
AMBER = "C2760A"
EMERALD = "0B7A5A"
ROSE = "C22B4E"
BLUE = "2457D6"
VIOLET_SOFT = "EFEBFF"
CYAN_SOFT = "E4F3F1"
AMBER_SOFT = "FBEFD9"
EMERALD_SOFT = "E3F1EC"
ROSE_SOFT = "FBE7EC"
BLUE_SOFT = "E7EEFB"
GRAY_SOFT = "F1EFE8"

SANS = "sans"
MONO = "mono"
FONT_SCALE = 1.2

# ---------- spec helpers ----------


def rect(x, y, w, h, fill=WHITE, line=LINE, radius=14, lw=1, dash=None):
    return {"type": "rect", "x": x, "y": y, "w": w, "h": h,
            "fill": fill, "line": line, "radius": radius, "lw": lw, "dash": dash}


def text(x, y, w, h, s="", size=20, color=INK, bold=False, font=SANS,
         align="left", valign="top", spacing=4, runs=None):
    return {"type": "text", "x": x, "y": y, "w": w, "h": h, "s": s,
            "size": size, "color": color, "bold": bold, "font": font,
            "align": align, "valign": valign, "spacing": spacing, "runs": runs}


def line(x1, y1, x2, y2, color=FAINT, width=2, dash=None, arrow=False):
    return {"type": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "color": color, "width": width, "dash": dash, "arrow": arrow}


def dot(x, y, r, fill=EMERALD):
    return {"type": "dot", "x": x, "y": y, "r": r, "fill": fill}


def image(path, x, y, w, h):
    return {"type": "image", "path": str(path), "x": x, "y": y, "w": w, "h": h}


def card(x, y, w, h, fill=WHITE, line=LINE, radius=14, lw=1):
    return rect(x, y, w, h, fill=fill, line=line, radius=radius, lw=lw)


def pill(x, y, w, h, s, size=18, fg=INK, bg=WHITE, border=None, bold=True):
    out = [rect(x, y, w, h, fill=bg, line=border or bg, radius=h / 2, lw=1)]
    out.append(text(x, y, w, h, s=s, size=size, color=fg, bold=bold,
                    align="center", valign="middle", spacing=0))
    return out


def tag(x, y, w, h, s, fg, bg, size=18):
    return pill(x, y, w, h, s, size=size, fg=fg, bg=bg, border=fg)


def label(x, y, w, s, size=20, color=INK, bold=False, font=SANS,
          align="left", valign="top", h=30):
    return text(x, y, w, h, s=s, size=size, color=color, bold=bold, font=font,
                align=align, valign=valign)


def bullets(x, y, w, items, size=20, color=INK, gap=8, prefix="·"):
    out = []
    for i, item in enumerate(items):
        out.append(text(x, y + i * (size + gap), w, size + 8, s=f"{prefix} {item}",
                        size=size, color=color, spacing=0))
    return out


def flatten(items):
    out = []
    for item in items:
        if isinstance(item, list):
            out.extend(flatten(item))
        elif isinstance(item, dict):
            out.append(item)
    return out


# ---------- Pillow preview ----------

FONT_PATHS = {
    (SANS, False): r"C:\Windows\Fonts\msyh.ttc",
    (SANS, True): r"C:\Windows\Fonts\msyhbd.ttc",
    (MONO, False): r"C:\Windows\Fonts\consola.ttf",
    (MONO, True): r"C:\Windows\Fonts\consolab.ttf",
}


def _font(font_name, size, bold):
    from PIL import ImageFont
    path = FONT_PATHS.get((font_name, bold), FONT_PATHS[(SANS, bold)])
    return ImageFont.truetype(path, max(8, int(size)))


def render_png(slide: dict, path: str | pathlib.Path) -> None:
    from PIL import Image, ImageDraw

    W, H = 1600, 900
    img = Image.new("RGB", (W, H), "#" + slide.get("bg", PAPER))
    draw = ImageDraw.Draw(img)

    for el in flatten(slide["elements"]):
        t = el["type"]
        if t == "rect":
            box = [el["x"], el["y"], el["x"] + el["w"], el["y"] + el["h"]]
            fill = "#" + el["fill"] if el.get("fill") else None
            outline = "#" + el["line"] if el.get("line") else None
            if el.get("radius", 0):
                draw.rounded_rectangle(box, radius=el["radius"], fill=fill,
                                       outline=outline, width=max(1, int(el.get("lw", 1))))
            else:
                draw.rectangle(box, fill=fill, outline=outline, width=max(1, int(el.get("lw", 1))))
        elif t == "line":
            color = "#" + el["color"]
            draw.line([el["x1"], el["y1"], el["x2"], el["y2"]], fill=color,
                      width=max(1, int(el.get("width", 2))))
            if el.get("arrow"):
                _arrow_head(draw, el["x1"], el["y1"], el["x2"], el["y2"], color)
        elif t == "dot":
            r = el["r"]
            draw.ellipse([el["x"] - r, el["y"] - r, el["x"] + r, el["y"] + r],
                         fill="#" + el["fill"])
        elif t == "image":
            from PIL import Image
            im = Image.open(el["path"]).convert("RGBA").resize(
                (max(1, int(el["w"])), max(1, int(el["h"]))), Image.LANCZOS)
            img.paste(im, (int(el["x"]), int(el["y"])), im)
        elif t == "text":
            _draw_text(draw, el)

    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def _arrow_head(draw, x1, y1, x2, y2, color, size=12):
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    for delta in (2.6, -2.6):
        x = x2 + size * math.cos(ang + delta)
        y = y2 + size * math.sin(ang + delta)
        draw.line([x2, y2, x, y], fill=color, width=2)


def _draw_text(draw, el):
    align, valign = el.get("align", "left"), el.get("valign", "top")
    base_size = el.get("size", 20)
    size = base_size * FONT_SCALE
    color = "#" + el.get("color", INK)
    spacing = el.get("spacing", 4)

    if el.get("runs"):
        total_w = 0
        measured = []
        for run in el["runs"]:
            fn = run.get("font", el.get("font", SANS))
            if fn == MONO and CJK.search(run["s"]):
                fn = SANS
            f = _font(fn, run.get("size", base_size) * FONT_SCALE,
                      run.get("bold", el.get("bold", False)))
            w = draw.textlength(run["s"], font=f)
            measured.append((run, f, w))
            total_w += w
        x = el["x"] + (el["w"] - total_w) / 2 if align == "center" else (
            el["x"] + el["w"] - total_w if align == "right" else el["x"])
        y = el["y"] + (el["h"] - size * 1.2) / 2 if valign == "middle" else el["y"]
        for run, f, w in measured:
            draw.text((x, y), run["s"], font=f, fill="#" + run.get("color", el.get("color", INK)),
                      anchor="lt")
            x += w
        return

    font_name = el.get("font", SANS)
    if font_name == MONO and CJK.search(el.get("s", "")):
        font_name = SANS
    font = _font(font_name, size, el.get("bold", False))
    s = el.get("s", "")
    bbox = draw.multiline_textbbox((0, 0), s, font=font, spacing=spacing)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = el["x"] + (el["w"] - tw) / 2 if align == "center" else (
        el["x"] + el["w"] - tw if align == "right" else el["x"])
    y = el["y"] + (el["h"] - th) / 2 if valign == "middle" else (
        el["y"] + el["h"] - th if valign == "bottom" else el["y"])
    draw.multiline_text((x - bbox[0], y - bbox[1]), s, font=font, fill=color, spacing=spacing)


# ---------- PowerPoint renderer ----------

def _pptx_font_name(name, s):
    if name == MONO and CJK.search(s):
        return "Microsoft YaHei"
    return "Consolas" if name == MONO else "Microsoft YaHei"


def render_pptx(slides: list[dict], path: str | pathlib.Path) -> None:
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.dml import MSO_LINE_DASH_STYLE

    PX = 13.333 / 1600.0

    def rgb(h):
        return RGBColor.from_string(h)

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    for spec in slides:
        slide = prs.slides.add_slide(blank)
        bg = slide.background.fill
        bg.solid()
        bg.fore_color.rgb = rgb(spec.get("bg", PAPER))

        for el in flatten(spec["elements"]):
            t = el["type"]
            if t == "rect":
                shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if el.get("radius", 0) else MSO_SHAPE.RECTANGLE
                shp = slide.shapes.add_shape(shape_type, Inches(el["x"] * PX), Inches(el["y"] * PX),
                                             Inches(el["w"] * PX), Inches(el["h"] * PX))
                if el.get("radius", 0):
                    try:
                        shp.adjustments[0] = min(0.5, el["radius"] / max(1, min(el["w"], el["h"])))
                    except Exception:
                        pass
                if el.get("fill"):
                    shp.fill.solid()
                    shp.fill.fore_color.rgb = rgb(el["fill"])
                else:
                    shp.fill.background()
                if el.get("line"):
                    shp.line.color.rgb = rgb(el["line"])
                    shp.line.width = Pt(max(0.5, el.get("lw", 1) * 0.6))
                    if el.get("dash"):
                        shp.line.dash_style = MSO_LINE_DASH_STYLE.DASH
                else:
                    shp.line.fill.background()
                shp.shadow.inherit = False
            elif t == "line":
                from pptx.enum.shapes import MSO_CONNECTOR
                conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,
                                                  Inches(el["x1"] * PX), Inches(el["y1"] * PX),
                                                  Inches(el["x2"] * PX), Inches(el["y2"] * PX))
                conn.line.color.rgb = rgb(el["color"])
                conn.line.width = Pt(max(0.5, el.get("width", 2) * 0.6))
                if el.get("dash"):
                    conn.line.dash_style = MSO_LINE_DASH_STYLE.DASH
                if el.get("arrow"):
                    _pptx_arrow_head(slide, el, PX)
            elif t == "dot":
                r = el["r"]
                shp = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches((el["x"] - r) * PX),
                                             Inches((el["y"] - r) * PX),
                                             Inches(2 * r * PX), Inches(2 * r * PX))
                shp.fill.solid()
                shp.fill.fore_color.rgb = rgb(el["fill"])
                shp.line.fill.background()
                shp.shadow.inherit = False
            elif t == "image":
                slide.shapes.add_picture(el["path"], Inches(el["x"] * PX), Inches(el["y"] * PX),
                                         Inches(el["w"] * PX), Inches(el["h"] * PX))
            elif t == "text":
                box = slide.shapes.add_textbox(Inches(el["x"] * PX), Inches(el["y"] * PX),
                                               Inches(el["w"] * PX), Inches(el["h"] * PX))
                tf = box.text_frame
                tf.word_wrap = True
                tf.margin_left = 0
                tf.margin_right = 0
                tf.margin_top = 0
                tf.margin_bottom = 0
                tf.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE,
                                      "bottom": MSO_ANCHOR.BOTTOM}.get(el.get("valign"), MSO_ANCHOR.TOP)
                align = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
                         "right": PP_ALIGN.RIGHT}.get(el.get("align"), PP_ALIGN.LEFT)

                if el.get("runs"):
                    p = tf.paragraphs[0]
                    p.alignment = align
                    p.space_after = Pt(0)
                    p.space_before = Pt(0)
                    for run in el["runs"]:
                        r = p.add_run()
                        r.text = run["s"]
                        r.font.size = Pt(run.get("size", el.get("size", 20)) * 0.6 * FONT_SCALE)
                        r.font.bold = run.get("bold", el.get("bold", False))
                        r.font.name = _pptx_font_name(run.get("font", el.get("font", SANS)), run["s"])
                        r.font.color.rgb = rgb(run.get("color", el.get("color", INK)))
                else:
                    lines = str(el.get("s", "")).split("\n")
                    for i, ln in enumerate(lines):
                        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                        p.alignment = align
                        p.space_after = Pt(0)
                        p.space_before = Pt(0)
                        p.line_spacing = 1.0 + el.get("spacing", 4) / 100.0
                        r = p.add_run()
                        r.text = ln
                        r.font.size = Pt(el.get("size", 20) * 0.6 * FONT_SCALE)
                        r.font.bold = el.get("bold", False)
                        r.font.name = _pptx_font_name(el.get("font", SANS), ln)
                        r.font.color.rgb = rgb(el.get("color", INK))

    prs.core_properties.title = "Bodhi2 · 知识工程系统架构"
    prs.core_properties.subject = "智能体 · 技能/工具 · 领域知识 · 本体"
    prs.core_properties.author = "Bodhi2"
    prs.core_properties.comments = "Native shapes + editable text; 6 pages."
    prs.save(str(path))


def _pptx_arrow_head(slide, el, PX):
    import math
    from pptx.util import Inches
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.dml.color import RGBColor
    ang = math.atan2(el["y2"] - el["y1"], el["x2"] - el["x1"])
    size = 10
    x, y = el["x2"], el["y2"]
    tri = slide.shapes.add_shape(MSO_SHAPE.ISOSCELES_TRIANGLE,
                                 Inches((x - size) * PX), Inches((y - size) * PX),
                                 Inches(2 * size * PX), Inches(2 * size * PX))
    tri.rotation = math.degrees(ang) + 90
    tri.fill.solid()
    tri.fill.fore_color.rgb = RGBColor.from_string(el["color"])
    tri.line.fill.background()
    tri.shadow.inherit = False
