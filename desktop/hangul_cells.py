"""Resolve editable Hangul table cells against native document coordinates."""

from collections import Counter

from lxml import etree

from desktop.document_objects import HANGUL
from editing import open_package, parse_part, rewrite_package, valid_text

STALE_CELL = "편집할 표 셀을 다시 선택하세요."

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
                catalog[(*address, table["control"])] = supported, editable_grid(native, table, supported)
    return catalog


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


def editable_grid(native, table, supported):
    size = native.find(HANGUL + "sz")
    rows = native.findall(HANGUL + "tr")
    if (size is None or size.get("protect", "0") != "0"
            or any(size.get(axis + "RelTo") != "ABSOLUTE" for axis in ("width", "height"))
            or any(node.tag not in {HANGUL + kind for kind in ("sz", "pos", "outMargin", "inMargin", "tr")}
                   for node in native)
            or len(rows) != table["rows"]):
        return False
    height, width = table["rows"], table["cols"]
    if (len(supported) != height * width or len(supported) != len(table["cells"])
            or any(not (0 <= row < height and 0 <= col < width) for row, col in supported)):
        return False
    for row in rows:
        if row.attrib or len(row) != table["cols"]:
            return False
        for cell in row:
            sublist = cell.find(HANGUL + "subList")
            if (cell.tag != HANGUL + "tc" or cell.get("name") or sublist is None or sublist.get("id")
                    or any(sublist.get(flag, "0") != "0" for flag in (
                        "hasTextRef", "hasNumRef", "linkListIDRef", "linkListNextIDRef"))
                    or any(node.tag not in {HANGUL + kind for kind in (
                        "subList", "cellAddr", "cellSpan", "cellSz", "cellMargin")} for node in cell)):
                return False
    return True


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
    return {(int(address.get("rowAddr")), int(address.get("colAddr"))): cell
            for cell in table.findall(f"{HANGUL}tr/{HANGUL}tc")
            for address in cell.findall(HANGUL + "cellAddr")}


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
        if remaining:
            used = (int(ident) for part_name in original.namelist()
                    if part_name.startswith("Contents/section") and part_name.endswith(".xml")
                    for paragraph in (root if part_name == part else parse_part(original, part_name)).iter(HANGUL + "p")
                    if (ident := paragraph.get("id", "")).isascii() and ident.isdigit())
            next_id = max(used, default=0) + 1
            for key in sorted(remaining):
                paragraph = new_cells[key].find(f"{HANGUL}subList/{HANGUL}p")
                if next_id > 0xffffffff:
                    raise ValueError(GRID_MISMATCH)
                paragraph.set("id", str(next_id))
                next_id += 1
        for old_cell, new_cell in surviving:
            old_address = old_cell.find(HANGUL + "cellAddr")
            new_address = new_cell.find(HANGUL + "cellAddr")
            old_address.attrib.update(new_address.attrib)
            new_cell.getparent().replace(new_cell, old_cell)
        replacement.tail = table.tail
        table.getparent().replace(table, replacement)
        return rewrite_package(original, {part: root})
