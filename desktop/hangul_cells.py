"""Resolve editable Hangul table cells against native document coordinates."""

from collections import Counter

from lxml import etree

from desktop.document_objects import HANGUL
from editing import open_package, parse_part, rewrite_package, valid_text

STALE_CELL = "편집할 표 셀을 다시 선택하세요."


def source_cells(root, table):
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
            catalog[(*address, table["control"])] = {
                coordinate: element.text or ""
                for coordinate, element, _ in source_cells(sections[section], table)
            }
    return catalog


def replace_cell(data, table, coordinate, value):
    part = section_part(table["section"])
    with open_package(data) as archive:
        root = parse_part(archive, part)
        for key, text, paragraph in source_cells(root, table):
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
