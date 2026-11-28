"""Apply Hangul paragraph and character formatting with copy-on-write properties."""

import re
from copy import deepcopy

from lxml import etree

from desktop.document_objects import HANGUL
from editing import open_package, parse_part, rewrite_package

HEADER = "Contents/header.xml"
HH = "{http://www.hancom.co.kr/hwpml/2011/head}"
ALIGNMENTS = {"left": "LEFT", "center": "CENTER", "right": "RIGHT", "justify": "JUSTIFY"}
CHARACTER = {"bold", "italic", "underline", "size", "color", "font"}
PARAGRAPH = {"align", "spacing"}
OPERATIONS = CHARACTER | PARAGRAPH
UNSUPPORTED = "선택한 한글 문단에 이 서식을 적용할 수 없습니다."


def plain_runs(paragraph):
    # Keep section, list and anchor controls in their original runs.
    runs = paragraph.findall(HANGUL + "run")
    texts = []
    for run in runs:
        nodes = run.findall(HANGUL + "t")
        if any(len(node) for node in nodes) or (nodes and len(nodes) != len(run)):
            return []
        if any((node.text or "").strip() for node in nodes):
            texts.append(run)
    return texts


def font_map(header):
    by_face = {}
    groups = header.findall(f"{HH}refList/{HH}fontfaces/{HH}fontface")
    languages = {group.get("lang", "").lower() for group in groups}
    for group in groups:
        lang = group.get("lang", "").lower()
        for font in group.findall(HH + "font"):
            face, ident = font.get("face"), font.get("id")
            if face and ident:
                by_face.setdefault(face, {})[lang] = ident
    return {face: ids for face, ids in by_face.items() if ids.keys() == languages}


def formatting_catalog(data):
    with open_package(data) as archive:
        if any("signature" in name.lower() for name in archive.namelist()
               if name.lower().startswith("meta-inf/")):
            return set(), []
        paragraphs = set()
        for path in archive.namelist():
            if match := re.fullmatch(r"Contents/section(\d+)\.xml", path):
                section = int(match[1])
                root = parse_part(archive, path)
                paragraphs.update((section, index) for index, para in enumerate(root.findall(HANGUL + "p"))
                                  if plain_runs(para))
        return paragraphs, sorted(font_map(parse_part(archive, HEADER)))


def property_nodes(header, family):
    group = header.find(f"{HH}refList/{HH}{family}Properties")
    if group is None:
        raise ValueError(UNSUPPORTED)
    nodes = {node.get("id"): node for node in group.findall(HH + family + "Pr")}
    if len(nodes) != len(group) or any(ident is None or not ident.isascii() or not ident.isdigit()
                                      for ident in nodes):
        raise ValueError(UNSUPPORTED)
    return group, nodes


def property_signature(node):
    return (node.tag, dict(node.attrib), node.text, tuple(property_signature(child) for child in node))


def clone_properties(group, nodes, targets, attribute, update):
    changed = False
    for target in targets:
        ident = target.get(attribute)
        if ident not in nodes:
            raise ValueError(UNSUPPORTED)
        original = nodes[ident]
        modified = deepcopy(original)
        update(modified)
        if property_signature(modified) == property_signature(original):
            continue
        for existing, node in nodes.items():
            modified.set("id", existing)
            if property_signature(modified) == property_signature(node):
                ident = existing
                break
        else:
            ident = str(max(map(int, nodes)) + 1)
            modified.set("id", ident)
            group.append(modified)
            nodes[ident] = modified
            group.set("itemCnt", str(len(group)))
        target.set(attribute, ident)
        changed = True
    return changed


def paragraph_update(node, operation, value):
    if operation == "align" and isinstance(value, str) and value in ALIGNMENTS:
        align = node.find(HH + "align")
        if align is not None:
            align.set("horizontal", ALIGNMENTS[value])
            return
    if operation == "spacing" and type(value) is int and 80 <= value <= 300:
        items = list(node.iter(HH + "lineSpacing"))
        if items:
            for item in items:
                item.set("type", "PERCENT")
                item.set("value", str(value))
                item.set("unit", "HWPUNIT")
            return
    raise ValueError(UNSUPPORTED)


def character_update(node, operation, value, active, fonts):
    if operation in ("bold", "italic"):
        tag = HH + operation
        prop = node.find(tag)
        if active and prop is None:
            node.insert(6, etree.Element(tag))
        elif not active and prop is not None:
            node.remove(prop)
    elif operation == "underline":
        underline = node.find(HH + "underline")
        if underline is None:
            raise ValueError(UNSUPPORTED)
        underline.set("type", "BOTTOM" if active else "NONE")
    elif operation == "size" and type(value) is int and 7 <= value <= 72:
        node.set("height", str(value * 100))
    elif operation == "color" and isinstance(value, str) and re.fullmatch("[0-9A-Fa-f]{6}", value):
        node.set("textColor", "#" + value.upper())
    elif operation == "font" and isinstance(value, str) and value in fonts:
        ref = node.find(HH + "fontRef")
        if ref is None:
            raise ValueError(UNSUPPORTED)
        for key, ident in fonts[value].items():
            ref.set(key, ident)
    else:
        raise ValueError(UNSUPPORTED)


def selected_runs(paragraph, selection, original):
    runs = []
    for run in paragraph.findall(HANGUL + "run"):
        texts = run.findall(HANGUL + "t")
        if texts:
            if len(run) != 1 or len(texts[0]):
                raise ValueError(UNSUPPORTED)
            runs.append((run, texts[0].text or ""))
    if "".join(value for _, value in runs) != original:
        raise ValueError(UNSUPPORTED)

    start, end = selection
    offset = 0
    for run, value in runs:
        left, right = max(0, start-offset), min(len(value), end-offset)
        offset += len(value)
        if left >= right:
            continue
        if left:
            prefix = deepcopy(run)
            prefix[0].text = value[:left]
            run.addprevious(prefix)
        if right < len(value):
            suffix = deepcopy(run)
            suffix[0].text = value[right:]
            run.addnext(suffix)
        run[0].text = value[left:right]
        yield run


def format_paragraph(data, section, index, operation, value, *, selection=None, original=None):
    part = f"Contents/section{section}.xml"
    with open_package(data) as archive:
        header = parse_part(archive, HEADER)
        root = parse_part(archive, part)
        paragraphs = root.findall(HANGUL + "p")
        if not 0 <= index < len(paragraphs):
            raise ValueError(UNSUPPORTED)
        paragraph = paragraphs[index]
        runs = plain_runs(paragraph)
        if not runs:
            raise ValueError(UNSUPPORTED)
        if operation in PARAGRAPH:
            group, nodes = property_nodes(header, "para")
            changed = clone_properties(group, nodes, [paragraph], "paraPrIDRef",
                             lambda node: paragraph_update(node, operation, value))
        elif operation in CHARACTER:
            if selection is not None:
                runs = list(selected_runs(paragraph, selection, original))
                if not runs:
                    raise ValueError(UNSUPPORTED)
            group, nodes = property_nodes(header, "char")
            fonts = font_map(header) if operation == "font" else {}
            active = True
            if operation in ("bold", "italic", "underline"):
                properties = [nodes.get(run.get("charPrIDRef")) for run in runs]
                if any(prop is None for prop in properties):
                    raise ValueError(UNSUPPORTED)
                tags = [prop.find(HH + operation) for prop in properties]
                active = not all(tag is not None and (operation != "underline" or tag.get("type") != "NONE")
                                 for tag in tags)
            changed = clone_properties(group, nodes, runs, "charPrIDRef",
                             lambda node: character_update(node, operation, value, active, fonts))
        else:
            raise ValueError(UNSUPPORTED)
        if not changed:
            return data
        if (segments := paragraph.find(HANGUL + "linesegarray")) is not None:
            paragraph.remove(segments)
        return rewrite_package(archive, {HEADER: header, part: root})
