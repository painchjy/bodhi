"""ke-core · 结构化表格读取（**零第三方依赖**）：xlsx / csv / tsv。

为什么不用 openpyxl/pandas
--------------------------
本仓库与 MCP 服务的口径是「零第三方依赖」（只用标准库 + `psql`），而部署机上实测
`openpyxl` / `pandas` / `lxml` / `pandoc` **全都没有**。xlsx 本质是一个 zip：

    xl/workbook.xml              —— sheet 名与 sheetId（顺序即物理 sheetN.xml 的顺序）
    xl/sharedStrings.xml         —— 共享字符串表（cell t="s" 的 v 是它的下标）
    xl/worksheets/sheetN.xml     —— 单元格：<c r="B3" t="s"><v>12</v></c> / t="inlineStr" 用 <is><t>
    xl/_rels/workbook.xml.rels   —— sheetId → 文件名的映射（有些工具导出的编号会错位）

所以用标准库 `zipfile` + `xml.etree` 就能**确定性地**读出来（本模块只做"读"，不做公式计算：
公式取缓存值；日期取原始序列号——批量建模要的是"单元格里那串字"，不做类型猜测）。

能力
----
- `read_xlsx(path, sheet=None, max_rows=0)` → `{"sheets": [{"name", "rows": [[str, ...]]}]}`
- `read_csv(path, delimiter=None)`        → 同上（自动嗅探 `,` / `;` / `\t`，UTF-8/GBK 兜底）
- `probe(path, sample=3)`                 → 只回**结构**：sheet 名、列名、行数、抽样几行、疑似主键
- `write_xlsx(path, sheet_name, rows)`    → 写一个最小 xlsx（内联字符串）——用于**造测试数据**

CLI
---
    python3 tools/ke-core/ke_sheet.py probe docs/cases/主子系统清单.xlsx [--sample 3] [--sheet 主系统]
    python3 tools/ke-core/ke_sheet.py rows  <file> [--sheet S] [--limit 20]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import pathlib
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"

CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _col_index(ref: str) -> int:
    """`"BC12"` → 列下标（A=0）。"""
    hit = CELL_RE.match((ref or "").strip().upper())
    if not hit:
        return -1
    letters = hit.group(1)
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _text(elem) -> str:
    return "".join(elem.itertext()) if elem is not None else ""


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    out = []
    for si in ET.fromstring(raw).findall(NS_MAIN + "si"):
        out.append(_text(si))
    return out


def _sheet_files(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """→ [(sheet 名, zip 内路径)]，按工作簿里的物理顺序。"""
    names = {}
    try:
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        for sh in workbook.iter(NS_MAIN + "sheet"):
            rid = sh.get(NS_REL + "id") or ""
            names[rid] = sh.get("name") or ""
    except KeyError:
        pass
    rels = {}
    try:
        for rel in ET.fromstring(zf.read("xl/_rels/workbook.xml.rels")).iter(NS_PKG + "Relationship"):
            target = (rel.get("Target") or "").lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            rels[rel.get("Id") or ""] = target
    except KeyError:
        pass
    ordered = []
    for rid, name in names.items():
        path = rels.get(rid)
        if path and path in zf.namelist():
            ordered.append((name, path))
    if not ordered:                      # 兜底：直接扫 worksheets/*.xml
        ordered = [(pathlib.PurePosixPath(p).stem, p)
                   for p in sorted(n for n in zf.namelist()
                                   if n.startswith("xl/worksheets/") and n.endswith(".xml"))]
    return ordered


def read_xlsx(path: str | pathlib.Path, sheet: str = "", max_rows: int = 0) -> dict:
    """读 xlsx → `{"sheets": [{"name", "rows"}]}`（rows 为二维字符串数组，缺格补空串）。"""
    path = pathlib.Path(path)
    out = []
    with zipfile.ZipFile(path) as zf:
        shared = _shared_strings(zf)
        for name, member in _sheet_files(zf):
            if sheet and name != sheet:
                continue
            rows: list[list[str]] = []
            root = ET.fromstring(zf.read(member))
            for row in root.iter(NS_MAIN + "row"):
                values: list[str] = []
                for cell in row.findall(NS_MAIN + "c"):
                    idx = _col_index(cell.get("r") or "")
                    kind = cell.get("t") or ""
                    if kind == "inlineStr":
                        text = _text(cell.find(NS_MAIN + "is"))
                    elif kind == "s":
                        v = cell.find(NS_MAIN + "v")
                        pos = int(_text(v) or 0)
                        text = shared[pos] if 0 <= pos < len(shared) else ""
                    else:
                        text = _text(cell.find(NS_MAIN + "v"))
                    if idx < 0:
                        idx = len(values)
                    while len(values) < idx:
                        values.append("")
                    if len(values) == idx:
                        values.append(text)
                    else:
                        values[idx] = text
                rows.append(values)
                if max_rows and len(rows) >= max_rows:
                    break
            out.append({"name": name, "rows": rows})
    return {"file": str(path), "sheets": out}


def read_csv(path: str | pathlib.Path, delimiter: str = "", max_rows: int = 0) -> dict:
    """读 csv/tsv（编码 UTF-8 → GBK 兜底；分隔符自动嗅探）。"""
    path = pathlib.Path(path)
    raw = path.read_bytes()
    text = ""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        text = raw.decode("utf-8", "replace")
    if not delimiter:
        head = text.splitlines()[0] if text.splitlines() else ""
        delimiter = max((",", ";", "\t"), key=head.count)
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        rows.append([(c or "").strip() for c in row])
        if max_rows and len(rows) >= max_rows:
            break
    return {"file": str(path), "sheets": [{"name": path.stem, "rows": rows}]}


def read_any(path: str | pathlib.Path, sheet: str = "", max_rows: int = 0) -> dict:
    """按扩展名分派（.xlsx/.xlsm → xlsx；其它按 csv）。"""
    suffix = pathlib.Path(path).suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return read_xlsx(path, sheet=sheet, max_rows=max_rows)
    if suffix in (".xls", ".doc", ".docx"):
        raise ValueError("不支持 %s（老式二进制/文档格式）→ 请另存为 .xlsx / .csv / .md" % suffix)
    return read_csv(path, max_rows=max_rows)


def probe(path: str | pathlib.Path, sheet: str = "", sample: int = 3) -> dict:
    """只读**结构**：sheet / 表头 / 行数 / 抽样 / 疑似主键列（供智能体与用户对齐映射）。"""
    data = read_any(path, sheet=sheet)
    out = []
    for item in data["sheets"]:
        rows = item["rows"]
        header = rows[0] if rows else []
        body = [r for r in rows[1:] if any((c or "").strip() for c in r)]
        widths = [max((len((r[i] if i < len(r) else "") or "") for r in body), default=0)
                  for i in range(len(header))]
        unique = []
        for i, col in enumerate(header):
            vals = [((r[i] if i < len(r) else "") or "").strip() for r in body]
            vals = [v for v in vals if v]
            if vals and len(set(vals)) == len(vals):
                unique.append(col)
        out.append({
            "name": item["name"], "header": header, "rows": len(body),
            "sample": [r[: len(header)] for r in body[:sample]],
            "empty_columns": [header[i] for i, w in enumerate(widths) if w == 0],
            "duplicate_headers": sorted({h for h in header if header.count(h) > 1}),
            "prefixes": sorted({h.split("系统编号")[0].split("英文名称")[0].split("功能简介")[0]
                                for h in header if h.endswith(("系统编号", "英文名称", "功能简介"))
                                and h not in ("系统编号", "英文名称", "功能简介")}),
            "unique_columns": unique,
        })
    return {"file": str(path), "sheets": out}



# ---------------------------------------------------------------------------
# 最小 xlsx 写出（**只用于造测试数据**）：单 sheet、内联字符串、无样式
# ---------------------------------------------------------------------------
def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _col_name(idx: int) -> str:
    name = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        name = chr(ord("A") + rem) + name
    return name


def write_xlsx(path: str | pathlib.Path, rows: list[list[str]], sheet_name: str = "Sheet1") -> dict:
    """写一个最小可用 xlsx（内联字符串）；返回 {file, sheets}。"""
    path = pathlib.Path(path)
    body = []
    for ri, row in enumerate(rows, start=1):
        cells = "".join(
            '<c r="%s%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
            % (_col_name(ci), ri, _xml_escape(str(val))) for ci, val in enumerate(row))
        body.append('<row r="%d">%s</row>' % (ri, cells))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData>%s</sheetData></worksheet>' % "".join(body))
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>'
                % _xml_escape(sheet_name))
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
                     'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
                     'officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
                     'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                    'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return {"file": str(path), "sheets": [{"name": sheet_name, "rows": len(rows)}]}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
    ap = argparse.ArgumentParser(description="结构化表格读取（零依赖）：probe / rows")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_probe = sub.add_parser("probe", help="只读结构：sheet/表头/行数/抽样/疑似主键")
    p_probe.add_argument("file")
    p_probe.add_argument("--sheet", default="")
    p_probe.add_argument("--sample", type=int, default=3)
    p_rows = sub.add_parser("rows", help="打印前 N 行")
    p_rows.add_argument("file")
    p_rows.add_argument("--sheet", default="")
    p_rows.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    if args.cmd == "probe":
        print(json.dumps(probe(args.file, args.sheet, args.sample), ensure_ascii=False, indent=2))
        return 0
    data = read_any(args.file, sheet=args.sheet, max_rows=args.limit)
    for item in data["sheets"]:
        print("### sheet=%s（前 %d 行）" % (item["name"], len(item["rows"])))
        for row in item["rows"]:
            print("  | " + " | ".join(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())

