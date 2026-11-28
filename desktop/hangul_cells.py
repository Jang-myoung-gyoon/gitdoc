"""Resolve editable Hangul table cells against native document coordinates."""

import json
from collections import Counter
from copy import deepcopy

from lxml import etree

from desktop.document_objects import HANGUL
from desktop.hangul_format import HEADER, clone_properties, paragraph_update, property_nodes
from editing import open_package, parse_part, rewrite_package, valid_text

HWP_UNITS_PER_MM = 7200 / 25.4
UNSAFE_LAYOUT = "셀 정렬은 보호되지 않은 일반 텍스트 셀에서만 사용할 수 있습니다."
UNSAFE_SIZE = "셀 크기는 병합되지 않은 일반 표에서만 바꿀 수 있습니다."
PARTIAL_SPAN = "정렬하려면 범위에 걸친 병합 셀 전체를 포함하세요."
LAYOUT_MISMATCH = "한글 표 속성과 주변 내용을 확인할 수 없어 변경을 적용하지 않았습니다."
CELL_LAYOUT_COMMANDS = {
    "horizontal": ("left", "center", "right"),
    "vertical": ("TOP", "CENTER", "BOTTOM"),
}
MAX_SUBDIVISION = 6
UNSAFE_SUBDIVISION = "행·열로 나누기는 병합·보호되지 않은 일반 표에서만 사용할 수 있습니다."
SUBDIVISION_MISMATCH = "나눈 셀이나 주변 셀의 구조와 서식을 확인할 수 없어 변경을 적용하지 않았습니다."
STALE_CELL = "편집할 표 셀을 다시 선택하세요."
UNSAFE_SPAN = "병합·나누기는 보호되지 않은 일반 셀에서만 사용할 수 있습니다."
SPAN_STYLES = "선택한 셀의 테두리·여백·정렬 또는 머리글이 달라 서식을 보존한 채 합칠 수 없습니다."
SPAN_MISMATCH = "셀 내용이나 서식을 확인할 수 없어 병합·나누기를 취소했습니다."

UNSAFE_GRID = "병합되거나 보호된 셀, 또는 복잡한 표 구조에서는 줄·칸을 편집할 수 없습니다."
GRID_MISMATCH = "줄·칸 편집 결과의 셀 내용과 서식을 확인할 수 없어 변경을 취소했습니다."
GRID_OPERATIONS = {
    "row_above": ("insert-row", 0, False, None),
    "row_below": ("insert-row", 0, False, "--below"),
    "col_left": ("insert-col", 1, False, None),
    "col_right": ("insert-col", 1, False, "--right"),
    "row_delete": ("delete-row", 0, True, None),
    "col_delete": ("delete-col", 1, True, None),
}


def source_table(root, table):
    paragraphs = root.findall(HANGUL + "p")
    index = table["paragraph"]
    if not 0 <= index < len(paragraphs):
        return
    candidates = paragraphs[index].findall(f"{HANGUL}run/{HANGUL}tbl")
    if len(candidates) != 1:
        return
    native = candidates[0]
    if (native.get("lock", "0") != "0" or native.get("rowCnt") != str(table["rows"])
            or native.get("colCnt") != str(table["cols"])):
        return
    return native


def source_cells(native, table):
    nodes = {}
    duplicates = set()
    for cell in native.findall(f"{HANGUL}tr/{HANGUL}tc"):
        address = cell.find(HANGUL + "cellAddr")
        if address is None:
            continue
        key = address.get("rowAddr"), address.get("colAddr")
        if key in nodes:
            duplicates.add(key)
        nodes[key] = cell

    for cell in table["cells"]:
        key = cell["row"], cell["col"]
        coordinates = tuple(map(str, key))
        node = nodes.get(coordinates)
        if (node is None or coordinates in duplicates or node.get("protect", "0") != "0"
                or cell["rowSpan"] != 1 or cell["colSpan"] != 1):
            continue
        span = node.find(HANGUL + "cellSpan")
        sublists = node.findall(HANGUL + "subList")
        if (span is None or span.get("rowSpan") != "1" or span.get("colSpan") != "1"
                or len(sublists) != 1 or len(sublists[0]) != 1):
            continue
        paragraph = sublists[0][0]
        if paragraph.tag != HANGUL + "p" or any(
                child.tag not in (HANGUL + "run", HANGUL + "linesegarray") for child in paragraph):
            continue
        runs = paragraph.findall(HANGUL + "run")
        if len(runs) != 1 or len(runs[0]) != 1:
            continue
        text = runs[0][0]
        if (text.tag == HANGUL + "t" and not len(text)
                and (text.text or "") == cell["text"]):
            yield key, text, paragraph


def section_part(section):
    return f"Contents/section{section}.xml"


def cell_catalog(data, tables):
    catalog = {}
    with open_package(data) as archive:
        if any(name.lower().startswith("meta-inf/") and "signature" in name.lower()
               for name in archive.namelist()):
            return catalog
        sections = {}
        addresses = [(table["section"], table["paragraph"]) for table in tables]
        counts = Counter(addresses)
        for table, address in zip(tables, addresses):
            if counts[address] != 1:
                continue
            section, *_ = address
            if section not in sections:
                sections[section] = parse_part(archive, section_part(section))
            native = source_table(sections[section], table)
            if native is not None:
                supported = {coordinate: element.text or ""
                             for coordinate, element, _ in source_cells(native, table)}
                grid = editable_grid(native, table, supported)
                catalog[(*address, table["control"])] = (
                    supported, grid, splittable_cells(native, table), layout_cells(native, table),
                    grid and native.get("cellSpacing", "0") == "0",
                )
    return catalog



def layout_paragraphs(cell):
    if not ordinary_cell(cell):
        return []
    sublist = cell.find(HANGUL + "subList")
    if sublist.get("textDirection", "HORIZONTAL") != "HORIZONTAL":
        return []
    for p in sublist:
        if (p.tag != HANGUL + "p" or any(
                child.tag not in (HANGUL + "run", HANGUL + "linesegarray") for child in p)
                or any(text.tag != HANGUL + "t" or len(text) for run in p.findall(HANGUL + "run") for text in run)):
            return []
    return list(sublist)


def layout_cells(native, table):
    if not ordinary_table(native, table):
        return {}
    cells = grid_cells(native)
    if len(cells) != len(table["cells"]):
        return {}
    result = {}
    for item in table["cells"]:
        row, col, rows, cols = (item[key] for key in ("row", "col", "rowSpan", "colSpan"))
        cell = cells.get((row, col))
        if cell is None or not layout_paragraphs(cell):
            continue
        span = cell.find(HANGUL + "cellSpan")
        if (span.get("rowSpan") != str(rows) or span.get("colSpan") != str(cols)
                or not (0 <= row < table["rows"] and 0 <= col < table["cols"]
                        and 1 <= rows <= table["rows"]-row and 1 <= cols <= table["cols"]-col)):
            continue
        result[row, col] = (rows, cols)
    return result


def layout_range(table, coordinate, end):
    anchors = {(c["row"], c["col"]): (c["rowSpan"], c["colSpan"]) for c in table["cells"]}
    if coordinate not in anchors or end not in anchors:
        raise ValueError(STALE_CELL)
    start = tuple(map(min, zip(coordinate, end)))
    stop = tuple(max(a + x, b + y) for a, b, x, y in zip(coordinate, end, anchors[coordinate], anchors[end]))
    if (stop[0] - start[0]) * (stop[1] - start[1]) > 1000:
        raise ValueError("한 번에 최대 1,000개 셀까지 편집할 수 있습니다.")
    selected = []
    covered = set()
    for (row, col), (rows, cols) in anchors.items():
        if row >= stop[0] or col >= stop[1] or row+rows <= start[0] or col+cols <= start[1]:
            continue
        if not (start[0] <= row and start[1] <= col and row+rows <= stop[0] and col+cols <= stop[1]):
            raise ValueError(PARTIAL_SPAN)
        selected.append((row, col))
        for r in range(row, row+rows):
            for c in range(col, col+cols):
                if (r, c) in covered:
                    raise ValueError(UNSAFE_LAYOUT)
                covered.add((r, c))
    if len(covered) != (stop[0]-start[0]) * (stop[1]-start[1]):
        raise ValueError(UNSAFE_LAYOUT)
    return selected


def resize_cells(native, table, cells, supported, selected, axis, value):
    if (native.get("cellSpacing", "0") != "0"
            or not editable_grid(native, table, supported)
            or any(span != (1, 1) for span in supported.values())):
        raise ValueError(UNSAFE_SIZE)
    dimension = 0 if axis == "height" else 1
    first, last = ("top", "bottom") if dimension == 0 else ("left", "right")
    targets = {coordinate[dimension] for coordinate in selected}
    units = round(value * HWP_UNITS_PER_MM)
    size = native.find(HANGUL + "sz")
    dimensions = {}
    affected = []
    changed_cells = []
    try:
        for coordinate, cell in cells.items():
            node = cell.find(HANGUL + "cellSz")
            length = int(node.get(axis))
            strip = coordinate[dimension]
            if length <= 0 or dimensions.setdefault(strip, length) != length:
                raise ValueError(UNSAFE_SIZE)
            if strip in targets:
                margin = cell.find(HANGUL + "cellMargin")
                if cell.get("hasMargin", "0") != "1" or margin is None:
                    margin = native.find(HANGUL + "inMargin")
                if margin is not None and units < sum(int(margin.get(key, "0")) for key in (first, last)) + round(HWP_UNITS_PER_MM):
                    raise ValueError("셀 크기는 안쪽 여백보다 커야 합니다.")
                if length != units:
                    node.set(axis, str(units))
                    affected.extend(layout_paragraphs(cell))
                    changed_cells.append(coordinate)
        total = int(size.get(axis))
        delta = sum(units-dimensions[strip] for strip in targets)
        if total <= 0 or total+delta <= 0 or (axis == "width" and total != sum(dimensions.values())):
            raise ValueError(UNSAFE_SIZE)
        if delta:
            size.set(axis, str(total+delta))
    except TypeError as error:
        raise ValueError(UNSAFE_SIZE) from error
    commands = []
    if changed_cells:
        if axis == "width":
            widths = [units if index in targets else dimensions[index] for index in range(table["cols"])]
            commands.append(("set-column-widths", ["--widths", ",".join(map(str, widths))]))
        else:
            commands = [cell_properties_command(row, col, {"height": units}) for row, col in changed_cells]
    return affected, commands


def cell_properties_command(row, col, props):
    return "set-cell-props", ["--row", str(row), "--col", str(col), "--props", json.dumps(props)]


def format_cells(data, table, coordinate, end, change, value):
    part = section_part(table["section"])
    with open_package(data) as archive:
        root = parse_part(archive, part)
        native = source_table(root, table)
        if native is None or not ordinary_table(native, table):
            raise ValueError(UNSAFE_LAYOUT)
        selected = layout_range(table, coordinate, end)
        supported = layout_cells(native, table)
        if any(key not in supported for key in selected):
            raise ValueError(UNSAFE_LAYOUT)
        cells = grid_cells(native)
        parts = {part: root}
        commands = []
        if change in ("width", "height"):
            changed, commands = resize_cells(native, table, cells, supported, selected, change, value)
        elif change == "horizontal":
            targets = [(row, col, index, p, p.get("paraPrIDRef"))
                       for row, col in selected for index, p in enumerate(layout_paragraphs(cells[row, col]))]
            paragraphs = [p for _, _, _, p, _ in targets]
            header = parse_part(archive, HEADER)
            group, nodes = property_nodes(header, "para")
            clone_properties(group, nodes, paragraphs, "paraPrIDRef", lambda node: paragraph_update(node, "align", value))
            changed = []
            for row, col, index, p, original in targets:
                if p.get("paraPrIDRef") != original:
                    changed.append(p)
                    commands.append(("apply-para-format-in-cell", [
                        "--row", str(row), "--col", str(col), "--cell-para", str(index),
                        "--props", json.dumps({"alignment": value}),
                    ]))
            if changed:
                parts[HEADER] = header
        else:
            changed = []
            for key in selected:
                cell = cells[key]
                sublist = cell.find(HANGUL + "subList")
                if sublist.get("vertAlign") != value:
                    sublist.set("vertAlign", value)
                    changed.extend(layout_paragraphs(cell))
                    commands.append(cell_properties_command(*key, {"verticalAlign": CELL_LAYOUT_COMMANDS["vertical"].index(value)}))
        if not changed:
            return data, []
        for paragraph in changed:
            if (cache := paragraph.find(HANGUL + "linesegarray")) is not None:
                paragraph.remove(cache)
        return rewrite_package(archive, parts), commands


def alignment_references(original, expected, saved):
    _, initial = property_nodes(original, "para")
    expected_group, desired = property_nodes(expected, "para")
    actual_group, actual = property_nodes(saved, "para")
    if any(key not in actual or xml_signature(node) != xml_signature(actual[key]) for key, node in initial.items()):
        raise ValueError(LAYOUT_MISMATCH)

    def appearance(node):
        signature = xml_signature(node)
        attributes = signature[1]
        attributes.pop("id")
        return signature

    styles = [(key, appearance(node)) for key, node in desired.items()]
    refs = {}
    for key, node in actual.items():
        if key in initial:
            continue
        signature = appearance(node)
        match = next((ident for ident, style in styles if style == signature), None)
        if match is None:
            raise ValueError(LAYOUT_MISMATCH)
        refs[key] = match
    # rhwp can append a duplicate paragraph style for each cell. Compare its effect by reference.
    for key in refs:
        actual_group.remove(actual[key])
    for key, node in desired.items():
        if key not in initial:
            actual_group.append(deepcopy(node))
    actual_group.set("itemCnt", expected_group.get("itemCnt"))
    return refs


def verify_cell_layout(source, expected, actual, table, change):
    part = section_part(table["section"])
    with open_package(source) as original, open_package(expected) as before, open_package(actual) as after:
        filenames = {name for name in before.namelist() if name.startswith(("Contents/", "BinData/"))}
        if filenames != {name for name in after.namelist() if name.startswith(("Contents/", "BinData/"))}:
            raise ValueError(LAYOUT_MISMATCH)
        if any(before.read(name) != after.read(name) for name in filenames - {part, HEADER}):
            raise ValueError(LAYOUT_MISMATCH)

        styles, actual_styles = (parse_part(archive, HEADER) for archive in (before, after))
        refs = alignment_references(parse_part(original, HEADER), styles, actual_styles) if change == "horizontal" else {}
        if xml_signature(styles) != xml_signature(actual_styles):
            raise ValueError(LAYOUT_MISMATCH)

        roots = [parse_part(archive, part) for archive in (before, after)]
        for root in roots:
            native = source_table(root, table)
            if native is None:
                raise ValueError(LAYOUT_MISMATCH)
            # The writer can refresh cached line metrics within the table.
            for cache in list(native.iter(HANGUL + "linesegarray")):
                cache.getparent().remove(cache)
            if change == "height":
                # rhwp recomputes the frame height from rendered row minima.
                size = native.find(HANGUL + "sz")
                height = size.attrib.pop("height", "") if size is not None else ""
                if not height.isascii() or not height.isdecimal() or int(height) <= 0:
                    raise ValueError(LAYOUT_MISMATCH)
        for paragraph in roots[1].iter(HANGUL + "p"):
            key = paragraph.get("paraPrIDRef")
            if key in refs:
                paragraph.set("paraPrIDRef", refs[key])
        if xml_signature(roots[0]) != xml_signature(roots[1]):
            raise ValueError(LAYOUT_MISMATCH)


def replace_cell(data, table, coordinate, value):
    part = section_part(table["section"])
    with open_package(data) as archive:
        root = parse_part(archive, part)
        native = source_table(root, table)
        for key, text, paragraph in source_cells(native, table) if native is not None else ():
            if key == coordinate:
                text.text = value
                if (cache := paragraph.find(HANGUL + "linesegarray")) is not None:
                    paragraph.remove(cache)
                return rewrite_package(archive, {part: root})
    raise ValueError(STALE_CELL)


def cell_value(value):
    if not isinstance(value, str):
        raise ValueError(STALE_CELL)
    return valid_text(value, 20000)


def verify_cell(expected, actual, table, coordinate):
    # Verify the binary writer preserved native section structure, styles and assets.
    part = section_part(table["section"])
    with open_package(expected) as before, open_package(actual) as after:
        def parts(archive):
            return {name for name in archive.namelist() if name.startswith(("Contents/", "BinData/"))}

        before_parts = parts(before)
        message = "한글 표 셀을 저장한 뒤 원본 구조를 확인할 수 없습니다."
        if before_parts != parts(after) or any(before.read(name) != after.read(name)
                                              for name in before_parts - {part}):
            raise ValueError(message)
        desired, saved = parse_part(before, part), parse_part(after, part)
        paragraphs = saved.findall(HANGUL + "p")
        native = paragraphs[table["paragraph"]].find(f"{HANGUL}run/{HANGUL}tbl")
        if native is None:
            raise ValueError(message)
        for node in native.findall(f"{HANGUL}tr/{HANGUL}tc"):
            address = node.find(HANGUL + "cellAddr")
            if address is not None and (address.get("rowAddr"), address.get("colAddr")) == tuple(map(str, coordinate)):
                for paragraph in node.findall(f"{HANGUL}subList/{HANGUL}p"):
                    if (cache := paragraph.find(HANGUL + "linesegarray")) is not None:
                        paragraph.remove(cache)
        if etree.tostring(desired, method="c14n") != etree.tostring(saved, method="c14n"):
            raise ValueError(message)


def ordinary_table(native, table):
    size = native.find(HANGUL + "sz")
    rows = native.findall(HANGUL + "tr")
    return (size is not None and size.get("protect", "0") == "0"
            and all(size.get(axis + "RelTo") == "ABSOLUTE" for axis in ("width", "height"))
            and all(node.tag in {HANGUL + kind for kind in ("sz", "pos", "outMargin", "inMargin", "tr")}
                    for node in native)
            and len(rows) == table["rows"] and all(not row.attrib for row in rows))


def ordinary_cell(cell):
    sublist = cell.find(HANGUL + "subList")
    parts = Counter(node.tag for node in cell)
    required = {HANGUL + name for name in ("subList", "cellAddr", "cellSpan", "cellSz")}
    return (cell.tag == HANGUL + "tc" and not cell.get("name") and cell.get("protect", "0") == "0"
            and sublist is not None and not sublist.get("id")
            and all(sublist.get(flag, "0") == "0" for flag in (
                "hasTextRef", "hasNumRef", "linkListIDRef", "linkListNextIDRef"))
            and required <= parts.keys() <= required | {HANGUL + "cellMargin"}
            and all(count == 1 for count in parts.values()))


def editable_grid(native, table, supported):
    if not ordinary_table(native, table):
        return False
    height, width = table["rows"], table["cols"]
    if (len(supported) != height * width or len(supported) != len(table["cells"])
            or any(not (0 <= row < height and 0 <= col < width) for row, col in supported)):
        return False
    return all(len(row) == width and all(ordinary_cell(cell) for cell in row)
               for row in native.findall(HANGUL + "tr"))


def xml_signature(node, *, skip=frozenset(), synthetic_ids=False):
    attrs = dict(node.attrib)
    if synthetic_ids and node.tag == HANGUL + "p":
        # rhwp exports sequential paragraph IDs from binary HWP on every export.
        attrs.pop("id", None)
    text = node.text if node.tag == HANGUL + "t" or (node.text or "").strip() else None
    parent = node.getparent()
    tail = node.tail if (node.tail or "").strip() or (parent is not None and parent.tag == HANGUL + "t") else None
    return (node.tag, attrs, text or None, tail or None,
            tuple(xml_signature(child, skip=skip, synthetic_ids=synthetic_ids)
                  for child in node if child.tag not in skip))


def grid_cells(table):
    cells = {}
    for cell in table.findall(f"{HANGUL}tr/{HANGUL}tc"):
        addresses = cell.findall(HANGUL + "cellAddr")
        if len(addresses) != 1:
            return {}
        address = tuple(addresses[0].get(key, "") for key in ("rowAddr", "colAddr"))
        if any(not value.isascii() or not value.isdigit() or len(value) > 5 for value in address):
            return {}
        coordinate = tuple(map(int, address))
        if coordinate in cells:
            return {}
        cells[coordinate] = cell
    return cells


def verify_grid(source, staged, before, after, operation, coordinate, binary):
    _, axis, deleting, direction = GRID_OPERATIONS[operation]
    pivot = coordinate[axis] + bool(direction)
    part = section_part(before["section"])
    sizes = ("rows", "cols")
    count_attr = ("rowCnt", "colCnt")[axis]
    extent_attr = ("height", "width")[axis]
    expected = [before[key] + ((-1 if deleting else 1) if index == axis else 0)
                for index, key in enumerate(sizes)]
    if [after[key] for key in sizes] != expected:
        raise ValueError(GRID_MISMATCH)

    with open_package(source) as original, open_package(staged) as candidate:
        root, changed = parse_part(original, part), parse_part(candidate, part)
        table, replacement = source_table(root, before), source_table(changed, after)
        if table is None or replacement is None:
            raise ValueError(GRID_MISMATCH)
        old_cells, new_cells = grid_cells(table), grid_cells(replacement)
        # Existing native cell properties must agree, including character/paragraph styles,
        # border, margin, direction and size. Only coordinates and cached layout may differ.
        skip = {HANGUL + "cellAddr", HANGUL + "linesegarray"}
        remaining = set(new_cells)
        surviving = []
        previous_size = table.find(HANGUL + "sz")
        new_size = replacement.find(HANGUL + "sz")
        if previous_size is None or new_size is None:
            raise ValueError(GRID_MISMATCH)
        previous_size.set(extent_attr, new_size.get(extent_attr) or "")
        table.set(count_attr, replacement.get(count_attr))
        if xml_signature(table, skip={HANGUL + "tr"}) != xml_signature(replacement, skip={HANGUL + "tr"}):
            raise ValueError(GRID_MISMATCH)

        for cell_coordinate, old_cell in old_cells.items():
            pos = cell_coordinate[axis]
            if deleting and pos == pivot:
                continue
            shifted = list(cell_coordinate)
            shifted[axis] += ((-1 if deleting else 1) if pos >= pivot else 0)
            new_coordinate = tuple(shifted)
            new_cell = new_cells.get(new_coordinate)
            if (new_coordinate not in remaining
                    or xml_signature(old_cell, skip=skip, synthetic_ids=True)
                    != xml_signature(new_cell, skip=skip, synthetic_ids=True)):
                raise ValueError(GRID_MISMATCH)
            remaining.remove(new_coordinate)
            surviving.append((old_cell, new_cell))
        if deleting and remaining:
            raise ValueError(GRID_MISMATCH)
        if not deleting and (len(remaining) != expected[1-axis]
                             or any(key[axis] != pivot for key in remaining)):
            raise ValueError(GRID_MISMATCH)
        if not editable_grid(replacement, after, {
                key: node.text or "" for key, node, _ in source_cells(replacement, after)
        }) or any(item["text"] for item in after["cells"] if (item["row"], item["col"]) in remaining):
            raise ValueError(GRID_MISMATCH)

        if binary:
            table.getparent().remove(table)
            replacement.getparent().remove(replacement)
            if xml_signature(root, synthetic_ids=True) != xml_signature(changed, synthetic_ids=True):
                raise ValueError(GRID_MISMATCH)
            return None

        header = "Contents/header.xml"
        if remaining and xml_signature(parse_part(original, header)) != xml_signature(parse_part(candidate, header)):
            raise ValueError(GRID_MISMATCH)
        # Install only the table. Use the original HWPX cells (and paragraph IDs) for
        # all surviving entries, and assign fresh IDs only to newly created cells.
        preserve_ids(original, root, part, [new_cells[key] for key in sorted(remaining)])
        preserve_cells(surviving)
        replacement.tail = table.tail
        table.getparent().replace(table, replacement)
        return rewrite_package(original, {part: root})



def preserve_ids(original, root, part, new_cells):
    if not new_cells:
        return
    used = (int(ident) for part_name in original.namelist()
            if part_name.startswith("Contents/section") and part_name.endswith(".xml")
            for paragraph in (root if part_name == part else parse_part(original, part_name)).iter(HANGUL + "p")
            if (ident := paragraph.get("id", "")).isascii() and ident.isdigit())
    next_id = max(used, default=0) + 1
    for cell in new_cells:
        paragraph = cell.find(f"{HANGUL}subList/{HANGUL}p")
        if next_id > 0xffffffff:
            raise ValueError(GRID_MISMATCH)
        paragraph.set("id", str(next_id))
        next_id += 1


def preserve_cells(surviving, *, update_spans=False):
    for old_cell, new_cell in surviving:
        fields = ("cellAddr", "cellSpan") if update_spans else ("cellAddr",)
        for field in fields:
            old_cell.find(HANGUL + field).attrib.update(new_cell.find(HANGUL + field).attrib)
        new_cell.getparent().replace(new_cell, old_cell)


def plain_paragraphs(cell):
    if not ordinary_cell(cell):
        return []
    paragraphs = cell.find(HANGUL + "subList")
    for paragraph in paragraphs:
        runs = paragraph.findall(HANGUL + "run")
        if (paragraph.tag != HANGUL + "p" or len(runs) != 1 or len(runs[0]) != 1
                or any(child.tag not in (HANGUL + "run", HANGUL + "linesegarray") for child in paragraph)
                or runs[0][0].tag != HANGUL + "t" or len(runs[0][0])):
            return []
    return list(paragraphs)


def splittable_cells(native, table):
    if not ordinary_table(native, table):
        return {}
    spans = [(item["row"], item["col"], item["rowSpan"], item["colSpan"])
             for item in table["cells"] if item["rowSpan"] > 1 or item["colSpan"] > 1]
    if not spans:
        return {}
    cells = grid_cells(native)
    result = {}
    for row, col, rows, cols in spans:
        if not (1 <= rows <= table["rows"]-row and 1 <= cols <= table["cols"]-col):
            continue
        cell = cells.get((row, col))
        if cell is None or not plain_paragraphs(cell):
            continue
        span = cell.find(HANGUL + "cellSpan")
        if span is not None and span.get("rowSpan") == str(rows) and span.get("colSpan") == str(cols):
            result[row, col] = [rows, cols]
    return result


def cell_appearance(cell):
    sublist = cell.find(HANGUL + "subList")
    flags = dict(cell.attrib)
    flags.pop("dirty", None)
    return flags, dict(sublist.attrib), tuple(
        xml_signature(node) for node in cell if node.tag not in {
            HANGUL + name for name in ("cellAddr", "cellSpan", "cellSz", "subList")
        }
    )


def paragraph_signatures(paragraphs):
    return [xml_signature(p, skip={HANGUL + "linesegarray"}, synthetic_ids=True) for p in paragraphs]


def paragraph_nonempty(paragraph):
    return bool(paragraph.find(f"{HANGUL}run/{HANGUL}t").text)


def verify_span(source, staged, before, after, start, end, splitting, binary):
    part = section_part(before["section"])
    area = [(row, col) for row in range(start[0], end[0] + 1) for col in range(start[1], end[1] + 1)]
    if splitting and any(
            (entry["row"], entry["col"]) != start
            and entry["row"] <= end[0] and entry["row"] + entry["rowSpan"] > start[0]
            and entry["col"] <= end[1] and entry["col"] + entry["colSpan"] > start[1]
            for entry in before["cells"]):
        raise ValueError(UNSAFE_SPAN)
    with open_package(source) as original, open_package(staged) as candidate:
        root, changed = parse_part(original, part), parse_part(candidate, part)
        table, replacement = source_table(root, before), source_table(changed, after)
        if (table is None or replacement is None or not ordinary_table(table, before)
                or not ordinary_table(replacement, after)):
            raise ValueError(SPAN_MISMATCH)
        # Combining or redistributing paragraphs can change the computed table height.
        height = replacement.find(HANGUL + "sz").get("height", "")
        if not height.isascii() or not height.isdigit() or int(height) <= 0:
            raise ValueError(SPAN_MISMATCH)
        table.find(HANGUL + "sz").set("height", height)
        if xml_signature(table, skip={HANGUL + "tr"}) != xml_signature(replacement, skip={HANGUL + "tr"}):
            raise ValueError(SPAN_MISMATCH)
        old, new = grid_cells(table), grid_cells(replacement)
        if start not in old or start not in new:
            raise ValueError(SPAN_MISMATCH)
        unaffected = old.keys() - set(area)
        expected = unaffected | (set(area) if splitting else {start})
        if new.keys() != expected or any(
                xml_signature(old[key], synthetic_ids=True) != xml_signature(new[key], synthetic_ids=True)
                for key in unaffected):
            raise ValueError(SPAN_MISMATCH)
        appearance = cell_appearance(old[start])
        selected = [old[start]] if splitting else [old[key] for key in area if key in old]
        if (not splitting and len(selected) != len(area)) or any(not plain_paragraphs(cell) for cell in selected):
            raise ValueError(UNSAFE_SPAN)
        if any(cell_appearance(cell) != appearance for cell in selected):
            raise ValueError(SPAN_STYLES)
        target_cells = [new[key] for key in area if key in new]
        if any(not plain_paragraphs(cell) or cell_appearance(cell) != appearance for cell in target_cells):
            raise ValueError(SPAN_MISMATCH)

        first = plain_paragraphs(old[start])
        expected_paragraphs = first + [
            paragraph for cell in selected[1:] for paragraph in plain_paragraphs(cell)
            if paragraph_nonempty(paragraph)
        ]
        blank_style = deepcopy(first[0])
        blank_style.find(f"{HANGUL}run/{HANGUL}t").text = None
        if not splitting and any(
                paragraph_signatures([paragraph]) != paragraph_signatures([blank_style])
                for cell in selected[1:] for paragraph in plain_paragraphs(cell)
                if not paragraph_nonempty(paragraph)):
            raise ValueError(SPAN_STYLES)
        candidate_paragraphs = plain_paragraphs(new[start])
        if paragraph_signatures(expected_paragraphs) != paragraph_signatures(candidate_paragraphs):
            raise ValueError(SPAN_MISMATCH)
        blanks = target_cells[1:]
        if any(len(plain_paragraphs(cell)) != 1 or paragraph_nonempty(plain_paragraphs(cell)[0])
               for cell in blanks):
            raise ValueError(SPAN_MISMATCH)
        by_coordinate = {(item["row"], item["col"]): (item["rowSpan"], item["colSpan"]) for item in after["cells"]}
        shape = (end[0]-start[0]+1, end[1]-start[1]+1)
        if any(by_coordinate.get(key) != (1, 1) for key in area if splitting) or (
                not splitting and by_coordinate.get(start) != shape):
            raise ValueError(SPAN_MISMATCH)
        if binary:
            table.getparent().remove(table)
            replacement.getparent().remove(replacement)
            if xml_signature(root, synthetic_ids=True) != xml_signature(changed, synthetic_ids=True):
                raise ValueError(SPAN_MISMATCH)
            return None

        header = "Contents/header.xml"
        if xml_signature(parse_part(original, header)) != xml_signature(parse_part(candidate, header)):
            raise ValueError(SPAN_MISMATCH)
        preserve_ids(original, root, part, blanks)
        for paragraph, generated in zip(expected_paragraphs, candidate_paragraphs):
            generated.getparent().replace(generated, paragraph)
        preserve_cells((old[key], new[key]) for key in sorted(unaffected))
        replacement.tail = table.tail
        table.getparent().replace(table, replacement)
        return rewrite_package(original, {part: root})


def subdivision_axes(coordinate, selected, counts):
    address = tuple(value + (count - 1 if value > pivot else 0)
                    for value, pivot, count in zip(coordinate, selected, counts))
    span = tuple(count if value == pivot else 1 for value, pivot, count in zip(coordinate, selected, counts))
    return address, span


def subdivision_children(start, counts):
    return {(row, col) for row in range(start[0], start[0]+counts[0])
            for col in range(start[1], start[1]+counts[1])}


def verify_subdivision(source, staged, before, after, start, counts, binary):
    part = section_part(before["section"])
    expected_size = tuple(before[key] + count - 1 for key, count in zip(("rows", "cols"), counts))
    if (after["rows"], after["cols"]) != expected_size:
        raise ValueError(SUBDIVISION_MISMATCH)
    with open_package(source) as original, open_package(staged) as candidate:
        root, changed = parse_part(original, part), parse_part(candidate, part)
        table, replacement = source_table(root, before), source_table(changed, after)
        if (table is None or replacement is None or not ordinary_table(replacement, after)
                or table.get("cellSpacing", "0") != "0"):
            raise ValueError(UNSAFE_SUBDIVISION)
        old, new = grid_cells(table), grid_cells(replacement)
        if not editable_grid(table, before, {key: text.text or "" for key, text, _ in source_cells(table, before)}):
            raise ValueError(UNSAFE_SUBDIVISION)

        original_frame, new_frame = (node.find(HANGUL + "sz") for node in (table, replacement))
        # Splitting can refresh the rendered frame height. Width and all other table settings stay fixed.
        height = new_frame.get("height", "")
        if not height.isascii() or not height.isdecimal() or int(height) <= 0:
            raise ValueError(SUBDIVISION_MISMATCH)
        original_frame.set("height", height)
        table.set("rowCnt", str(expected_size[0]))
        table.set("colCnt", str(expected_size[1]))
        if xml_signature(table, skip={HANGUL + "tr"}) != xml_signature(replacement, skip={HANGUL + "tr"}):
            raise ValueError(SUBDIVISION_MISMATCH)

        children = subdivision_children(start, counts)
        survivors = {}
        after_spans = {(cell["row"], cell["col"]): (cell["rowSpan"], cell["colSpan"]) for cell in after["cells"]}
        if len(after_spans) != len(after["cells"]) or after_spans.keys() != new.keys():
            raise ValueError(SUBDIVISION_MISMATCH)
        caches = {HANGUL + "linesegarray"}
        for coordinate, old_cell in old.items():
            if coordinate == start:
                continue
            address, span = subdivision_axes(coordinate, start, counts)
            actual = new.get(address)
            if actual is None or after_spans[address] != span:
                raise ValueError(SUBDIVISION_MISMATCH)
            expected = deepcopy(old_cell)
            expected.find(HANGUL + "cellAddr").attrib.update({"rowAddr": str(address[0]), "colAddr": str(address[1])})
            expected.find(HANGUL + "cellSpan").attrib.update({"rowSpan": str(span[0]), "colSpan": str(span[1])})
            if xml_signature(expected, skip=caches, synthetic_ids=True) != xml_signature(actual, skip=caches, synthetic_ids=True):
                raise ValueError(SUBDIVISION_MISMATCH)
            survivors[address] = (old_cell, actual)
        if new.keys() != survivors.keys() | children or any(after_spans[key] != (1, 1) for key in children):
            raise ValueError(SUBDIVISION_MISMATCH)

        source_cell = old[start]
        target_paragraphs = plain_paragraphs(source_cell)
        if len(target_paragraphs) != 1:
            raise ValueError(UNSAFE_SUBDIVISION)
        paragraph = target_paragraphs[0]
        blank = deepcopy(paragraph)
        blank.find(f"{HANGUL}run/{HANGUL}t").text = None
        appearance = cell_appearance(source_cell)
        for key in children:
            cell = new[key]
            paragraphs = plain_paragraphs(cell)
            if not paragraphs or cell_appearance(cell) != appearance:
                raise ValueError(SUBDIVISION_MISMATCH)
            if tuple(cell.find(HANGUL + "cellSpan").get(axis) for axis in ("rowSpan", "colSpan")) != ("1", "1"):
                raise ValueError(SUBDIVISION_MISMATCH)
            expected = paragraph if key == start else blank
            if paragraph_signatures(paragraphs) != paragraph_signatures([expected]):
                raise ValueError(SUBDIVISION_MISMATCH)
        verify_subdivision_sizes(table, source_cell, new, start, counts)

        if binary:
            table.getparent().remove(table)
            replacement.getparent().remove(replacement)
            if xml_signature(root, synthetic_ids=True) != xml_signature(changed, synthetic_ids=True):
                raise ValueError(SUBDIVISION_MISMATCH)
            return None

        if xml_signature(parse_part(original, HEADER)) != xml_signature(parse_part(candidate, HEADER)):
            raise ValueError(SUBDIVISION_MISMATCH)
        preserve_ids(original, root, part, [new[key] for key in sorted(children - {start})])
        first = plain_paragraphs(new[start])[0]
        if (identity := paragraph.get("id")) is not None:
            first.set("id", identity)
        else:
            first.attrib.pop("id", None)
        preserve_cells(survivors.values(), update_spans=True)
        replacement.tail = table.tail
        table.getparent().replace(table, replacement)
        return rewrite_package(original, {part: root})


def verify_subdivision_sizes(table, source_cell, cells, start, counts):
    size = source_cell.find(HANGUL + "cellSz")
    margin = source_cell.find(HANGUL + "cellMargin") if source_cell.get("hasMargin", "0") == "1" else None
    if margin is None:
        margin = table.find(HANGUL + "inMargin")
    for axis, dimension in enumerate(("height", "width")):
        strips = [set() for _ in range(counts[axis])]
        for coordinate in subdivision_children(start, counts):
            length = cells[coordinate].find(HANGUL + "cellSz").get(dimension, "")
            if not length.isascii() or not length.isdecimal() or int(length) <= 0:
                raise ValueError(SUBDIVISION_MISMATCH)
            strips[coordinate[axis]-start[axis]].add(int(length))
        if any(len(values) != 1 for values in strips):
            raise ValueError(SUBDIVISION_MISMATCH)
        lengths = [next(iter(values)) for values in strips]
        if sum(lengths) != int(size.get(dimension)) or max(lengths)-min(lengths) >= counts[axis]:
            raise ValueError(SUBDIVISION_MISMATCH)
        edges = ("top", "bottom") if axis == 0 else ("left", "right")
        padding = sum(int(margin.get(edge, "0")) for edge in edges) if margin is not None else 0
        minimum = max(round(2 * HWP_UNITS_PER_MM), padding + round(HWP_UNITS_PER_MM))
        if counts[axis] > 1 and min(lengths) < minimum:
            raise ValueError("나눈 셀에 2 mm 이상과 안쪽 여백을 확보하려면 원래 셀의 크기를 먼저 늘리세요.")
