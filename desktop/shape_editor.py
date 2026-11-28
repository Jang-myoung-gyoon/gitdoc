"""Edit shapes addressed by their source paragraph and inline order."""

import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass

from lxml import etree

from desktop.document_objects import (
    DRAWING, DRAWING_WORD, HANGUL, LEGACY_PATHS, VML, VML_OFFICE, WORD_SHAPE, WORD_TAG,
)
from desktop.hangul_format import property_signature
from editing import office_parts, open_package, parse_part, rewrite_package

WORD_PART = "word/document.xml"
WP = f"{{{DRAWING_WORD}}}"
A = f"{{{DRAWING}}}"
WPS = f"{{{WORD_SHAPE}}}"
V = f"{{{VML}}}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
VML_WRAP = "{urn:schemas-microsoft-com:office:word}wrap"
WORD_KINDS = {"rect": "rectangle", "ellipse": "ellipse", "triangle": "triangle", "rightArrow": "arrow"}
VML_KINDS = {"rect": "rectangle", "oval": "ellipse", "5": "triangle", "13": "arrow"}
HWP_UNITS_PER_MM = 7200 / 25.4
HWP_GEOMETRY = {
    "rect": ("pt0", "pt1", "pt2", "pt3"),
    "ellipse": ("center", "ax1", "ax2", "start1", "end1", "start2", "end2"),
    "polygon": ("pt",),
}


@dataclass
class Shape:
    kind: str
    paragraph: object
    width: float
    height: float
    drawing: tuple = ()
    vml: object = None
    hangul: object = None


def standalone_word_shape(paragraph, binary_word=False):
    runs = [child for child in paragraph if child.tag != WORD_TAG + "pPr"]
    props = paragraph.find(WORD_TAG + "pPr")
    if (len(runs) != 1 or runs[0].tag != WORD_TAG + "r"
            or props is not None and any(child.tag in {WORD_TAG + "sectPr", WORD_TAG + "pageBreakBefore",
                                                       WORD_TAG + "pPrChange", WORD_TAG + "keepNext"}
                                           for child in props)):
        return None
    items = [child for child in runs[0] if child.tag != WORD_TAG + "rPr"]
    if len(items) != 1:
        return None
    item = items[0]
    if item.tag == MC + "AlternateContent":
        if [child.tag for child in item] != [MC + "Choice", MC + "Fallback"] or any(len(child) != 1 for child in item):
            return None
        drawing, picture = item[0][0], item[1][0]
    else:
        drawing = item if item.tag == WORD_TAG + "drawing" else None
        picture = item if item.tag == WORD_TAG + "pict" else None
    modern = read_drawing(drawing) if drawing is not None else None
    legacy = read_vml(picture, binary_word and modern is not None) if picture is not None else None
    if (modern is None and legacy is None or drawing is not None and modern is None
            or picture is not None and legacy is None or modern and legacy and modern[0] != legacy[0]):
        return None
    kind, width, height = (modern or legacy)[:3]
    return Shape(kind, paragraph, width, height, modern[3] if modern else (), legacy[3] if legacy else None)


def read_drawing(drawing):
    if drawing.tag != WORD_TAG + "drawing" or len(drawing) != 1 or drawing[0].tag != WP + "inline":
        return None
    inline = drawing[0]
    graphic = inline.find(f"{A}graphic/{A}graphicData")
    if graphic is None or len(graphic) != 1 or graphic[0].tag != WPS + "wsp":
        return None
    shape = graphic[0]
    if shape.find(WPS + "txbx") is not None or any(
        node.get(key) in ("1", "true") for node in inline.iter()
        for key in ("noSelect", "noMove", "noResize", "noChangeAspect")
    ):
        return None
    transforms = shape.find(f"{WPS}spPr/{A}xfrm")
    geometry = shape.find(f"{WPS}spPr/{A}prstGeom")
    outer = inline.find(WP + "extent")
    inner = None if transforms is None else transforms.find(A + "ext")
    if any(node is None for node in (outer, inner, geometry)) or transforms.get("rot", "0") != "0":
        return None
    kind = WORD_KINDS.get(geometry.get("prst"))
    try:
        sizes = [int(outer.get(key)) / 36000 for key in ("cx", "cy")]
        if any(abs(int(inner.get(key)) - int(outer.get(key))) > 635 for key in ("cx", "cy")):
            return None
    except (TypeError, ValueError):
        return None
    return (kind, *sizes, (outer, inner)) if kind and all(s > 0 for s in sizes) else None


def read_vml(picture, inline_choice=False):
    if picture.tag != WORD_TAG + "pict":
        return None
    nodes = [node for node in picture if node.tag != V + "shapetype"]
    if len(nodes) != 1 or nodes[0].tag not in (V + "rect", V + "oval", V + "shape"):
        return None
    node = nodes[0]
    if any(node.find(f".//{V}{tag}") is not None for tag in ("textbox", "imagedata")):
        return None
    kind = VML_KINDS.get(node.tag.removeprefix(V))
    if node.tag == V + "shape":
        reference = node.get("type", "").lstrip("#")
        types = [shape for shape in picture.findall(V + "shapetype") if shape.get("id") == reference]
        if len(types) == 1:
            kind = VML_KINDS.get(types[0].get(f"{{{VML_OFFICE}}}spt"))
        elif not types:
            kind = VML_KINDS.get(reference.removeprefix("_x0000_t"))
    style = node.get("style", "")
    if re.search(r"(?:^|;)\s*rotation\s*:", style):
        return None
    if absolute_vml(style) and not inline_choice:
        return None
    lengths = [re.findall(rf"(?:^|;)\s*{key}\s*:\s*([0-9.]+)pt(?=;|$)", style) for key in ("width", "height")]
    if not kind or any(len(length) != 1 for length in lengths):
        return None
    try:
        sizes = [float(length[0]) * 25.4 / 72 for length in lengths]
    except ValueError:
        return None
    return (kind, *sizes, node) if all(size > 0 for size in sizes) else None


def absolute_vml(style):
    return re.search(r"(?:^|;)\s*position\s*:\s*absolute(?:;|$)", style)


def standalone_hangul_shape(paragraph):
    if any(paragraph.get(flag, "0") != "0" for flag in ("pageBreak", "columnBreak", "merged")):
        return None
    runs = [child for child in paragraph if child.tag != HANGUL + "linesegarray"]
    if len(runs) != 1 or runs[0].tag != HANGUL + "run" or len(runs[0]) != 1:
        return None
    return hangul_shape(paragraph, runs[0][0])


def hangul_shape(paragraph, shape):
    if not isinstance(shape.tag, str):
        return None
    tag = shape.tag.removeprefix(HANGUL)
    if tag not in HWP_GEOMETRY or shape.get("lock", "0") != "0" or shape.get("groupLevel", "0") != "0":
        return None
    pos, size = shape.find(HANGUL + "pos"), shape.find(HANGUL + "sz")
    if (pos is None or size is None or pos.get("treatAsChar") != "1"
            or size.get("protect", "0") != "0"
            or any(size.get(key) != "ABSOLUTE" for key in ("heightRelTo", "widthRelTo"))
            or shape.find(HANGUL + "drawText") is not None):
        return None
    try:
        width, height = (int(size.get(key)) / HWP_UNITS_PER_MM for key in ("width", "height"))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    kind = {"rect": "rectangle", "ellipse": "ellipse", "polygon": "polygon"}[tag]
    return Shape(kind, paragraph, width, height, hangul=shape)


INLINE_PLACEMENT = "이 도형은 같은 본문 문단의 일반 글자 사이에서만 이동할 수 있습니다."


def inline_hangul_nodes(paragraph):
    return [node for run in paragraph.findall(HANGUL + "run") for node in run]


def inline_hangul_shapes(paragraph):
    if any(paragraph.get(flag, "0") != "0" for flag in ("pageBreak", "columnBreak", "merged")):
        return
    if any(child.tag not in (HANGUL + "run", HANGUL + "linesegarray") for child in paragraph):
        return
    nodes = inline_hangul_nodes(paragraph)
    if not any(node.tag == HANGUL + "t" and node.text for node in nodes):
        return
    geometry = (node for node in nodes if isinstance(node.tag, str) and node.tag.removeprefix(HANGUL) in HWP_GEOMETRY)
    for index, node in enumerate(geometry):
        if shape := hangul_shape(paragraph, node):
            yield index, shape


def inline_neighbor(shape, direction):
    nodes = inline_hangul_nodes(shape.paragraph)
    index = nodes.index(shape.hangul) + direction
    while 0 <= index < len(nodes):
        node = nodes[index]
        if node.tag != HANGUL + "t" or len(node):
            return None
        if text := node.text:
            # Keep combining marks, Jamo sequences, emoji and other clusters intact.
            if all((unicodedata.category(char)[0] in "LNP" or char == " ")
                   and not (0x1100 <= ord(char) <= 0x11ff or 0xa960 <= ord(char) <= 0xa97f
                            or 0xd7b0 <= ord(char) <= 0xd7ff) for char in text):
                return node
            return None
        index += direction
    return None


def remove_line_segments(paragraph):
    if (segments := paragraph.find(HANGUL + "linesegarray")) is not None:
        paragraph.remove(segments)


def isolate_run_node(node):
    run = node.getparent()
    index = run.index(node)
    for children, insert in ((run[:index], run.addprevious), (run[index+1:], run.addnext)):
        if children:
            fragment = run.makeelement(run.tag, attrib=dict(run.attrib))
            fragment.extend(children)
            insert(fragment)
    return run


def edit_inline_hangul(shape, operation):
    paragraph, element = shape.paragraph, shape.hangul
    run = element.getparent()
    if operation == "delete_shape":
        run.remove(element)
        if not len(run):
            paragraph.remove(run)
    else:
        before = operation == "shape_up"
        other = inline_neighbor(shape, -1 if before else 1)
        if other is None:
            raise ValueError(INLINE_PLACEMENT)
        own = isolate_run_node(element)
        adjacent = isolate_run_node(other)
        split = len(other.text) - 1 if before else 1
        first, last = other.text[:split], other.text[split:]
        if first and last:
            tail = deepcopy(adjacent)
            tail[0].text = last
            other.text = first
            adjacent.addnext(tail)
            adjacent.addnext(own)
        elif before:
            adjacent.addprevious(own)
        else:
            adjacent.addnext(own)
    remove_line_segments(paragraph)


def can_resize(shape):
    if shape.hangul is None:
        return True
    node = shape.hangul
    flip = node.find(HANGUL + "flip")
    rotation = node.find(HANGUL + "rotationInfo")
    matrices = node.find(HANGUL + "renderingInfo")
    if flip is None or rotation is None or matrices is None:
        return False
    if any(flip.get(key) != "0" for key in ("horizontal", "vertical")) or rotation.get("angle") != "0":
        return False
    size = node.find(HANGUL + "sz")
    for tag in ("orgSz", "curSz"):
        other = node.find(HANGUL + tag)
        if other is None or any(other.get(key) != size.get(key) for key in ("width", "height")):
            return False
    point_tags = HWP_GEOMETRY[node.tag.removeprefix(HANGUL)]
    points = [child for child in node if child.tag.rsplit("}", 1)[-1] in point_tags]
    if len(points) < 3 or len(point_tags) > 1 and len(points) != len(point_tags):
        return False
    try:
        for center in ("centerX", "centerY"):
            int(rotation.get(center))
        for point in points:
            for axis in ("x", "y"):
                int(point.get(axis))
        return all([float(matrix.get(f"e{i}")) for i in range(1, 7)] == [1, 0, 0, 0, 1, 0]
                   for matrix in matrices)
    except (TypeError, ValueError):
        return False


def movable_neighbor(paragraph, neighbor, hangul, binary_word=False):
    if neighbor is None or neighbor.tag != paragraph.tag:
        return False
    if hangul:
        if standalone_hangul_shape(neighbor) is not None:
            return True
        return (all(neighbor.get(flag, "0") == "0" for flag in ("pageBreak", "columnBreak", "merged"))
                and all(node.tag in (HANGUL + "run", HANGUL + "linesegarray") for node in neighbor)
                and all(node.tag == HANGUL + "t" and not len(node)
                        for run in neighbor.findall(HANGUL + "run") for node in run))
    if standalone_word_shape(neighbor, binary_word) is not None:
        return True
    return (all(node.tag in (WORD_TAG + "pPr", WORD_TAG + "r") for node in neighbor)
            and not any(neighbor.find(f".//{WORD_TAG}{tag}") is not None
                        for tag in ("sectPr", "pageBreakBefore", "pPrChange", "keepNext"))
            and all(node.tag in (WORD_TAG + "t", WORD_TAG + "rPr")
                    for run in neighbor.findall(WORD_TAG + "r") for node in run))


def shapes_in(root, hangul, binary_word=False):
    parent = root if hangul else root.find(WORD_TAG + "body")
    if parent is None:
        return
    for index, paragraph in enumerate(parent.findall((HANGUL if hangul else WORD_TAG) + "p")):
        shape = standalone_hangul_shape(paragraph) if hangul else standalone_word_shape(paragraph, binary_word)
        if shape is not None:
            yield (index,), shape
        elif hangul:
            for position, shape in inline_hangul_shapes(paragraph):
                yield (index, position), shape


def shape_catalog(data, *, hangul, binary_word=False):
    with open_package(data) as archive:
        if hangul and any("signature" in name.lower() for name in archive.namelist()
                          if name.lower().startswith("meta-inf/")):
            return []
        parts = [(0, WORD_PART)]
        if hangul:
            parts = sorted((int(match[1]), name) for name in archive.namelist()
                           if (match := re.fullmatch(r"Contents/section(\d+)\.xml", name)))
        return [
            {"address": [section, *index], "kind": shape.kind, "size": [round(shape.width), round(shape.height)],
             "move_up": shape_can_move(shape, index, -1, hangul, binary_word),
             "move_down": shape_can_move(shape, index, 1, hangul, binary_word),
             "resize": can_resize(shape),
             **({"character": inline_character(shape)} if len(index) == 2 else {})}
            for section, part in parts
            for index, shape in shapes_in(parse_part(archive, part), hangul, binary_word)
        ]


def inline_character(shape):
    offset = 0
    for node in inline_hangul_nodes(shape.paragraph):
        if node is shape.hangul:
            return offset
        if node.tag == HANGUL + "t":
            offset += len("".join(node.itertext()))
    raise ValueError(INLINE_PLACEMENT)


def verify_inline_hwp(expected, reopened, section):
    part = f"Contents/section{section}.xml"
    with open_package(expected) as before, open_package(reopened) as after:
        if property_signature(parse_part(before, part)) != property_signature(parse_part(after, part)):
            raise ValueError("HWP에 저장한 도형과 주변 본문을 확인할 수 없어 변경을 적용하지 않았습니다.")


def shape_can_move(shape, index, direction, hangul, binary_word):
    if len(index) == 2:
        return inline_neighbor(shape, direction) is not None
    neighbor = shape.paragraph.getprevious() if direction < 0 else shape.paragraph.getnext()
    return movable_neighbor(shape.paragraph, neighbor, hangul, binary_word)


def apply_shape(data, address, operation, size, *, hangul, binary_word=False):
    section, *indices = address
    index = tuple(indices)
    part = f"Contents/section{section}.xml" if hangul else WORD_PART
    with open_package(data) as archive:
        if not hangul:
            office_parts(archive, "docx")
        root = parse_part(archive, part)
        shape = next((shape for position, shape in shapes_in(root, hangul, binary_word) if position == index), None)
        if shape is None:
            raise ValueError("편집할 인라인 도형을 다시 선택하세요.")
        paragraph = shape.paragraph
        if len(index) == 2 and operation in ("delete_shape", "shape_up", "shape_down"):
            edit_inline_hangul(shape, operation)
        elif operation == "delete_shape":
            paragraph.getparent().remove(paragraph)
        elif operation in ("shape_up", "shape_down"):
            neighbor = paragraph.getprevious() if operation == "shape_up" else paragraph.getnext()
            if not movable_neighbor(paragraph, neighbor, hangul, binary_word):
                raise ValueError("문단 경계를 넘어 도형을 이동할 수 없습니다.")
            if operation == "shape_up":
                neighbor.addprevious(paragraph)
            else:
                neighbor.addnext(paragraph)
        else:
            if not can_resize(shape):
                raise ValueError("이 도형의 크기 조절을 지원하지 않습니다.")
            if hangul:
                resize_hangul(shape.hangul, size)
            else:
                resize_word(shape, size)
        return rewrite_package(archive, {part: root})


def prepare_legacy_shapes(data):
    """Keep the classic shape geometry when LibreOffice writes the binary DOC."""
    with open_package(data) as archive:
        root = parse_part(archive, WORD_PART)
        changed = False
        for _, shape in shapes_in(root, False, binary_word=True):
            parent = shape.vml.getparent() if shape.vml is not None else None
            fallback = parent.getparent() if parent is not None else None
            if fallback is None or fallback.tag != MC + "Fallback":
                continue
            alternate = fallback.getparent()
            picture = deepcopy(parent)
            legacy = next(node for node in picture if node.tag != V + "shapetype")
            style = legacy.get("style")
            if absolute_vml(style):
                # Older LibreOffice uses a floating VML fallback even when DrawingML is inline.
                positioning = ("position", "margin-", "mso-position-")
                legacy.set("style", ";".join(part for part in style.split(";")
                                             if not part.lstrip().startswith(positioning)))
                if (wrap := legacy.find(VML_WRAP)) is not None:
                    legacy.remove(wrap)
                if legacy.tag in (V + "rect", V + "oval") and re.search(r"-21474836[0-9]{2}", legacy.get("path", "")):
                    legacy.attrib.pop("path")
            if shape.kind in LEGACY_PATHS:
                reference = legacy.get("type", "").lstrip("#")
                if not any(node.get("id") == reference for node in picture.findall(V + "shapetype")):
                    kind, path = LEGACY_PATHS[shape.kind]
                    picture.insert(0, etree.Element(V + "shapetype", {
                        "id": reference, "coordsize": "21600,21600", "path": path,
                        f"{{{VML_OFFICE}}}spt": kind,
                    }))
                    legacy.set("type", f"#{reference}")
            alternate.getparent().replace(alternate, picture)
            changed = True
        return rewrite_package(archive, {WORD_PART: root}) if changed else data


def resize_word(shape, size):
    for extent in shape.drawing:
        for key, dimension in zip(("cx", "cy"), size):
            extent.set(key, str(dimension * 36000))
    if shape.vml is not None:
        style = shape.vml.get("style")
        for key, dimension in zip(("width", "height"), size):
            style = re.sub(rf"(^|;)\s*{key}\s*:[^;]+", rf"\g<1>{key}:{dimension * 72 / 25.4:.3f}pt", style)
        shape.vml.set("style", style)


def resize_hangul(node, size):
    dimensions = [round(value * HWP_UNITS_PER_MM) for value in size]
    geometry = node.find(HANGUL + "sz")
    previous = [int(geometry.get(key)) for key in ("width", "height")]
    rotation = node.find(HANGUL + "rotationInfo")
    for axis, dimension, old, name, center in zip(("x", "y"), dimensions, previous,
                                                  ("width", "height"), ("centerX", "centerY")):
        for child in (geometry, node.find(HANGUL + "orgSz"), node.find(HANGUL + "curSz")):
            child.set(name, str(dimension))
        rotation.set(center, str(round(int(rotation.get(center)) * dimension / old)))
        point_tags = HWP_GEOMETRY[node.tag.removeprefix(HANGUL)]
        for point in node:
            if point.tag.rsplit("}", 1)[-1] in point_tags:
                point.set(axis, str(round(int(point.get(axis)) * dimension / old)))
    remove_line_segments(node.getparent().getparent())
