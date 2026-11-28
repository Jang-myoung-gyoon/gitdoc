"""Plan cell-only border and background edits with copy-on-write style definitions."""

import re
from copy import deepcopy
from dataclasses import dataclass

from lxml import etree

from desktop.hangul_cells import (
    grid_cells, layout_cells, layout_range, section_part, source_table, xml_signature,
)
from desktop.hangul_format import HEADER, HH
from editing import open_package, parse_part, rewrite_package

HC = "{http://www.hancom.co.kr/hwpml/2011/core}"
SIDES = ("left", "right", "top", "bottom")
LINE_STYLES = {"none": ("NONE", 0), "solid": ("SOLID", 1)}
LINE_WIDTHS = {"0.2": 3, "0.3": 5, "0.5": 7, "0.7": 9}
UNSAFE_PAINT = "보호되지 않은 일반 셀을 선택하세요. 병합 셀은 전체를 선택 범위에 포함해야 합니다."
PAINT_MISMATCH = "선택 범위의 새 서식과 주변 셀 보존을 확인할 수 없어 변경을 적용하지 않았습니다."


@dataclass
class PaintPlan:
    document: bytes
    references: dict
    new_styles: dict


def color_value(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError("색을 #RRGGBB 형식으로 지정하세요.")
    return value.upper()


def paint_request(change, value):
    if change == "background":
        if value is None:
            return {"fillType": "none"}
        return {"fillType": "solid", "fillColor": color_value(value), "patternColor": "#000000", "patternType": -1}
    if not isinstance(value, dict):
        raise ValueError("테두리 방향과 선 종류, 굵기, 색을 지정하세요.")
    side, style, width = (value.get(key) for key in ("side", "style", "width"))
    if (side not in (*SIDES, "all") or not isinstance(style, str) or style not in LINE_STYLES
            or not isinstance(width, str) or width not in LINE_WIDTHS):
        raise ValueError("테두리 방향과 선 종류, 굵기를 다시 선택하세요.")
    spec = {"type": LINE_STYLES[style][1], "width": LINE_WIDTHS[width], "color": color_value(value.get("color"))}
    if style == "none":
        spec = {"type": 0, "width": 0, "color": "#000000"}
    return {f"border{key.title()}": spec for key in (SIDES if side == "all" else (side,))}


def borders(header):
    group = header.find(f"{HH}refList/{HH}borderFills")
    if group is None:
        raise ValueError(UNSAFE_PAINT)
    definitions = {}
    for node in group:
        key = node.get("id", "")
        if node.tag != HH+"borderFill" or not re.fullmatch(r"[1-9][0-9]{0,4}", key) or key in definitions:
            raise ValueError(UNSAFE_PAINT)
        definitions[key] = node
    return group, definitions


def painted_definition(source, patch):
    result = deepcopy(source)
    if (result.get("centerLine", "NONE") != "NONE"
            or any(result.find(HH+direction) is not None
                   and result.find(HH+direction).get("type") != "NONE" for direction in ("slash", "backSlash"))):
        raise ValueError("대각선이나 특수 선이 있는 셀은 테두리와 배경을 편집할 수 없습니다.")
    if "fillType" in patch:
        fill = result.find(HC+"fillBrush")
        if fill is not None:
            children = list(fill)
            if (len(children) != 1 or children[0].tag != HC+"winBrush"
                    or children[0].get("hatchStyle") is not None
                    or children[0].get("alpha", "0") != "0"):
                raise ValueError("그림, 무늬, 그라데이션 또는 투명도가 있는 배경은 편집할 수 없습니다.")
            if patch["fillType"] == "none" and children[0].get("faceColor", "").lower() == "none":
                return result
            result.remove(fill)
        if patch["fillType"] == "solid":
            fill = etree.SubElement(result, HC+"fillBrush")
            etree.SubElement(fill, HC+"winBrush", faceColor=patch["fillColor"], hatchColor="#000000", alpha="0")
        return result

    width_labels = {index: f"{label} mm" for label, index in LINE_WIDTHS.items()}
    types = {index: kind for kind, index in LINE_STYLES.values()}
    for side in SIDES:
        spec = patch.get(f"border{side.title()}")
        if spec is None:
            continue
        entries = result.findall(HH+side+"Border")
        if len(entries) != 1:
            raise ValueError(UNSAFE_PAINT)
        line = entries[0]
        if spec["type"] or line.get("type") != "NONE":
            line.set("type", types[spec["type"]])
            line.set("width", width_labels.get(spec["width"], "0.1 mm"))
            line.set("color", spec["color"])
    return result


def paint_signature(node):
    signature = xml_signature(node)
    signature[1].pop("id", None)
    return signature


def plan_cell_paint(data, native, start, end, patch):
    part = section_part(native["section"])
    with open_package(data) as archive:
        root = parse_part(archive, part)
        table = source_table(root, native)
        if table is None:
            raise ValueError(UNSAFE_PAINT)
        selected = layout_range(native, start, end)
        supported = layout_cells(table, native)
        if any(key not in supported for key in selected):
            raise ValueError(UNSAFE_PAINT)
        header = parse_part(archive, HEADER)
        group, definitions = borders(header)
        cells = grid_cells(table)
        next_id = max(map(int, definitions), default=0)+1
        changed = False
        new_styles = {}
        styles_by_base = {}
        for key in selected:
            cell = cells[key]
            old_id = cell.get("borderFillIDRef")
            original = definitions.get(old_id)
            if original is None:
                raise ValueError(UNSAFE_PAINT)
            if old_id in styles_by_base:
                style_id = styles_by_base[old_id]
                cell.set("borderFillIDRef", style_id)
                changed |= style_id != old_id
                continue
            style = painted_definition(original, patch)
            wanted = paint_signature(style)
            if wanted == paint_signature(original):
                styles_by_base[old_id] = old_id
                continue
            style_id = next((ident for ident, node in definitions.items() if paint_signature(node) == wanted), None)
            if style_id is None:
                if next_id > 65535:
                    raise ValueError("문서의 셀 서식 개수가 한도에 도달했습니다.")
                style_id = str(next_id)
                next_id += 1
                style.set("id", style_id)
                definitions[style_id] = style
                group.append(style)
                new_styles[style_id] = (key, {"borderFillId": int(old_id), **patch})
            styles_by_base[old_id] = style_id
            cell.set("borderFillIDRef", style_id)
            changed = True
        if not changed:
            return None
        group.set("itemCnt", str(len(definitions)))
        refs = {key: cell.get("borderFillIDRef") for key, cell in cells.items()}
        return PaintPlan(rewrite_package(archive, {HEADER: header, part: root}), refs, new_styles)


def paint_references(data, native):
    with open_package(data) as archive:
        table = source_table(parse_part(archive, section_part(native["section"])), native)
        if table is None:
            raise ValueError(PAINT_MISMATCH)
        return {key: cell.get("borderFillIDRef") for key, cell in grid_cells(table).items()}


def verify_cell_paint(source, plan, actual, native, aliases):
    part = section_part(native["section"])
    with open_package(source) as original, open_package(plan.document) as intended, open_package(actual) as saved:
        relevant = {name for name in intended.namelist() if name.startswith(("Contents/", "BinData/"))}
        if relevant != {name for name in saved.namelist() if name.startswith(("Contents/", "BinData/"))}:
            raise ValueError(PAINT_MISMATCH)
        if any(intended.read(name) != saved.read(name) for name in relevant - {part, HEADER}):
            raise ValueError(PAINT_MISMATCH)
        expected_header, saved_header = (parse_part(archive, HEADER) for archive in (intended, saved))
        _, initial = borders(parse_part(original, HEADER))
        group, styles = borders(saved_header)
        _, wanted = borders(expected_header)
        if any(key not in styles or xml_signature(node) != xml_signature(styles[key]) for key, node in initial.items()):
            raise ValueError(PAINT_MISMATCH)
        for key, actual_id in aliases.items():
            if actual_id not in styles or paint_signature(wanted[key]) != paint_signature(styles[actual_id]):
                raise ValueError(PAINT_MISMATCH)

        # The writer may create unused neighbor definitions during staging. Compare the header after removing them.
        for key, node in styles.items():
            if key not in initial:
                group.remove(node)
        for key, node in wanted.items():
            if key not in initial:
                group.append(deepcopy(node))
        group.set("itemCnt", str(len(wanted)))
        if xml_signature(expected_header) != xml_signature(saved_header):
            raise ValueError(PAINT_MISMATCH)

        root = parse_part(saved, part)
        saved_table = source_table(root, native)
        if saved_table is None:
            raise ValueError(PAINT_MISMATCH)
        cells = grid_cells(saved_table)
        if cells.keys() != plan.references.keys():
            raise ValueError(PAINT_MISMATCH)
        for key, expected_id in plan.references.items():
            native_id = aliases.get(expected_id, expected_id)
            if cells[key].get("borderFillIDRef") != native_id:
                raise ValueError(PAINT_MISMATCH)
            cells[key].set("borderFillIDRef", expected_id)
        if xml_signature(parse_part(intended, part)) != xml_signature(root):
            raise ValueError(PAINT_MISMATCH)
