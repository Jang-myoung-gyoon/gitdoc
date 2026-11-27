"""Read sparse worksheet previews while keeping the original file untouched."""

import io
from zipfile import ZipFile

import xlrd

from extract import ZIP_MARKERS, ordered_parts, read_xml

MAX_CELLS = 25000


def xlsx_sheets(data):
    with ZipFile(io.BytesIO(data)) as package:
        names = package.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = ["".join(t.text or "" for t in node.findall(".//{*}t"))
                      for node in read_xml(package, "xl/sharedStrings.xml")]
        styles = []
        if "xl/styles.xml" in names:
            root = read_xml(package, "xl/styles.xml")
            formats = {node.get("numFmtId"): node.get("formatCode")
                       for node in root.findall("./{*}numFmts/{*}numFmt")}
            fonts = root.findall("./{*}fonts/{*}font")
            fills = root.findall("./{*}fills/{*}fill")
            borders = root.findall("./{*}borders/{*}border")
            for style in root.findall("./{*}cellXfs/{*}xf"):
                font = fonts[int(style.get("fontId", 0))] if fonts else None
                fill = fills[int(style.get("fillId", 0))] if fills else None
                border = borders[int(style.get("borderId", 0))] if borders else None
                number = style.get("numFmtId", "0")
                align = style.find("{*}alignment")
                styles.append({
                    "bold": font is not None and font.find("{*}b") is not None,
                    "italic": font is not None and font.find("{*}i") is not None,
                    "color": rgb(font.find("{*}color")) if font is not None else None,
                    "fill": rgb(fill.find("./{*}patternFill/{*}fgColor")) if fill is not None else None,
                    "align": align.get("horizontal") if align is not None else None,
                    "format": formats.get(number, number),
                    "signature": [signature(node) for node in (font, fill, border, align)],
                })
        for (name,), path in ordered_parts(package, ZIP_MARKERS["xlsx"], "sheet", ("name",)):
            root = read_xml(package, path)
            if root.tag.rsplit("}", 1)[-1] != "worksheet":
                continue
            merges = [node.get("ref") for node in root.findall("./{*}mergeCells/{*}mergeCell")]
            cells = {}
            for node in root.findall("./{*}sheetData/{*}row/{*}c"):
                address, kind = node.get("r"), node.get("t")
                value = node.findtext("{*}v")
                formula = node.findtext("{*}f", default=None)
                if formula is not None and value == "":
                    value = None
                if kind == "inlineStr":
                    value = "".join(t.text or "" for t in node.findall("./{*}is//{*}t"))
                elif kind == "s" and value is not None:
                    value = shared[int(value)]
                elif kind == "b":
                    value = "TRUE" if value == "1" else "FALSE"
                style_index = int(node.get("s", 0))
                if address and (value is not None or formula is not None or style_index):
                    value_kind = "number" if kind in (None, "n") and value is not None else "text"
                    cells[address] = [value, formula, styles[style_index] if styles else {}, value_kind]
                    if len(cells) > MAX_CELLS:
                        raise ValueError("시트 미리보기의 셀 한도를 초과했습니다.")
            yield {"name": name, "cells": cells, "merges": merges}


def rgb(node):
    value = node.get("rgb", "") if node is not None else ""
    return value[-6:] if len(value) in (6, 8) else None


def signature(node):
    if node is None:
        return None
    return (node.tag.rsplit("}", 1)[-1], sorted(node.attrib.items()),
            [signature(child) for child in node])


def xls_sheets(data):
    book = xlrd.open_workbook(file_contents=data, formatting_info=True, on_demand=True,
                              ragged_rows=True)
    try:
        styles = []
        for xf in book.xf_list:
            font = book.font_list[xf.font_index]
            foreground = book.colour_map.get(font.colour_index)
            background = book.colour_map.get(xf.background.pattern_colour_index)
            border = xf.border
            styles.append({
                "bold": bool(font.bold), "italic": bool(font.italic),
                "color": "%02x%02x%02x" % foreground if foreground else None,
                "fill": "%02x%02x%02x" % background if background and xf.background.fill_pattern else None,
                "align": {1: "left", 2: "center", 3: "right"}.get(xf.alignment.hor_align),
                "format": book.format_map[xf.format_key].format_str,
                "signature": [font.name, font.height, border.left_line_style, border.right_line_style,
                              border.top_line_style, border.bottom_line_style],
            })
        for name in book.sheet_names():
            sheet = book.sheet_by_name(name)
            cells = {}
            for row in range(sheet.nrows):
                for col, cell in enumerate(sheet.row(row)):
                    if cell.ctype == xlrd.XL_CELL_EMPTY:
                        continue
                    value = cell.value
                    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        value = "TRUE" if value else "FALSE"
                    elif cell.ctype == xlrd.XL_CELL_ERROR:
                        value = xlrd.error_text_from_code.get(value, str(value))
                    elif cell.ctype == xlrd.XL_CELL_DATE:
                        value = xlrd.xldate_as_datetime(value, book.datemode).isoformat(sep=" ")
                    cells[xlrd.cellname(row, col)] = [str(value), None, styles[cell.xf_index]]
                    if len(cells) > MAX_CELLS:
                        raise ValueError("시트 미리보기의 셀 한도를 초과했습니다.")
            merges = [f"{xlrd.cellname(r0, c0)}:{xlrd.cellname(r1 - 1, c1 - 1)}"
                      for r0, r1, c0, c1 in sheet.merged_cells]
            yield {"name": sheet.name, "cells": cells, "merges": merges}
    finally:
        book.release_resources()


def read_workbook(extension, data):
    source = xls_sheets(data) if extension == "xls" else xlsx_sheets(data)
    sheets = []
    budget = MAX_CELLS
    for sheet in source:
        budget -= len(sheet["cells"]) + len(sheet["merges"]) + 1
        if budget < 0:
            raise ValueError("통합 문서 미리보기의 셀 한도를 초과했습니다.")
        sheets.append(sheet)
    return sheets
