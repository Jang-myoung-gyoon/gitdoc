"""Insert native table and drawing elements in paged document formats."""

from lxml import etree

from editing import WORD, office_blocks, office_parts, open_package, parse_part, rewrite_package

WORD_TAG = f"{{{WORD}}}"
DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/main"
DRAWING_WORD = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
WORD_SHAPE = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
HANGUL = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
WORD_SHAPES = {"rectangle": "rect", "ellipse": "ellipse", "triangle": "triangle", "arrow": "rightArrow"}
HANGUL_SHAPES = {"rectangle": "rectangle", "ellipse": "ellipse", "triangle": "polygon", "arrow": "polygon"}
SHAPE_SIZE = (9200, 4200)


def insertion_value(operation, value):
    if operation == "shape" and isinstance(value, str) and value in WORD_SHAPES:
        return value
    if (operation == "table" and isinstance(value, list) and len(value) == 2
            and all(type(number) is int and 1 <= number <= 12 for number in value)):
        return value
    raise ValueError("도형 종류 또는 표의 행과 열(1~12)을 확인하세요.")


def word(parent, tag, **attributes):
    return etree.SubElement(parent, WORD_TAG + tag, {WORD_TAG + key: str(value) for key, value in attributes.items()})


def word_table(rows, columns):
    table = etree.Element(WORD_TAG + "tbl")
    properties = word(table, "tblPr")
    word(properties, "tblW", w=5000, type="pct")
    borders = word(properties, "tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        word(borders, side, val="single", sz=6, color="auto")
    grid = word(table, "tblGrid")
    width = 9000 // columns
    for _ in range(columns):
        word(grid, "gridCol", w=width)
    for _ in range(rows):
        row = word(table, "tr")
        for _ in range(columns):
            cell = word(row, "tc")
            word(word(cell, "tcPr"), "tcW", w=width, type="dxa")
            word(cell, "p")
    return table


def word_shape(kind, identifier, legacy):
    paragraph = etree.Element(WORD_TAG + "p")
    run = word(paragraph, "r")
    if legacy and kind == "ellipse":
        # LibreOffice's DOC filter loses the DrawingML ellipse path; its VML oval survives.
        picture = word(run, "pict")
        etree.SubElement(picture, "{urn:schemas-microsoft-com:vml}oval",
                         id=f"GitDocEllipse{identifier}", style="width:92pt;height:42pt",
                         fillcolor="#DCE8F8", strokecolor="#23456A")
        return paragraph
    drawing = word(run, "drawing")
    drawing.append(etree.fromstring(f'''<wp:inline xmlns:wp="{DRAWING_WORD}"
        xmlns:a="{DRAWING}" xmlns:wps="{WORD_SHAPE}">
      <wp:extent cx="1168400" cy="533400"/>
      <wp:docPr id="{identifier}" name="GitDoc {kind} {identifier}"/>
      <a:graphic><a:graphicData uri="{WORD_SHAPE}">
        <wps:wsp>
          <wps:cNvSpPr/>
          <wps:spPr>
            <a:xfrm><a:off x="0" y="0"/><a:ext cx="1168400" cy="533400"/></a:xfrm>
            <a:prstGeom prst="{WORD_SHAPES[kind]}"><a:avLst/></a:prstGeom>
            <a:solidFill><a:srgbClr val="DCE8F8"/></a:solidFill>
            <a:ln w="12700"><a:solidFill><a:srgbClr val="23456A"/></a:solidFill></a:ln>
          </wps:spPr>
          <wps:bodyPr/>
        </wps:wsp>
      </a:graphicData></a:graphic>
    </wp:inline>'''))
    return paragraph


def insert_word_object(data, operation, value, candidates, single_page, *, legacy=False):
    path = "word/document.xml"
    with open_package(data) as archive:
        _, paths = office_parts(archive, "docx")
        blocks = {
            identity: paragraph
            for identity, _, part, _, _, paragraph, _ in office_blocks(archive, "docx")
            if part == path and paragraph.getparent().tag == WORD_TAG + "body"
        }
        anchor = next((blocks[identity] for identity in candidates if identity in blocks), None)
        if anchor is None and not single_page:
            raise ValueError("이 페이지에서 삽입 위치를 확인할 수 없습니다. 본문 문단이 있는 페이지를 선택하세요.")
        root = parse_part(archive, path) if anchor is None else anchor.getroottree().getroot()
        body = root.find(WORD_TAG + "body")
        if body is None:
            raise ValueError("Word 본문을 찾을 수 없습니다.")
        if operation == "table":
            node = word_table(*value)
        else:
            used = {
                int(item.get("id"))
                for part in paths
                for doc in [root if part == path else parse_part(archive, part)]
                for item in doc.iter(f"{{{DRAWING_WORD}}}docPr")
                if (item.get("id") or "").isdigit()
            }
            vml_ids = {item.get("id") for item in root.iter("{urn:schemas-microsoft-com:vml}oval")}
            identifier = 1
            while identifier in used or f"GitDocEllipse{identifier}" in vml_ids:
                identifier += 1
            node = word_shape(value, identifier, legacy)
        if anchor is not None:
            anchor.addprevious(node)
        elif (settings := body.find(WORD_TAG + "sectPr")) is not None:
            settings.addprevious(node)
        else:
            body.append(node)
        return rewrite_package(archive, {path: root})


def hangul_polygons(root, paragraph):
    paragraphs = root.findall(HANGUL + "p")
    if not 0 <= paragraph < len(paragraphs):
        raise ValueError("한글 도형의 삽입 위치를 확인할 수 없습니다.")
    return paragraphs[paragraph].findall(f"{HANGUL}run/{HANGUL}polygon")


def arrow_polygon(before, after, section, paragraph):
    """Change only the native polygon introduced by the rhwp edit."""
    path = f"Contents/section{section}.xml"
    with open_package(before) as archive:
        existing = {node.get("instid") for node in hangul_polygons(parse_part(archive, path), paragraph)}
    with open_package(after) as archive:
        root = parse_part(archive, path)
        created = [node for node in hangul_polygons(root, paragraph) if node.get("instid") not in existing]
        if len(created) != 1:
            raise ValueError("한글 문서에서 새 화살표 도형을 식별할 수 없습니다.")
        polygon = created[0]
        points = polygon.findall(f"{{*}}pt")
        if len(points) != 3:
            raise ValueError("한글 도형의 꼭짓점을 확인할 수 없습니다.")
        index = polygon.index(points[0])
        tag = points[0].tag
        for point in points:
            polygon.remove(point)
        width, height = SHAPE_SIZE
        for x, y in ((0, height//4), (width*3//5, height//4), (width*3//5, 0),
                     (width, height//2), (width*3//5, height), (width*3//5, height*3//4), (0, height*3//4)):
            polygon.insert(index, etree.Element(tag, x=str(x), y=str(y)))
            index += 1
        return rewrite_package(archive, {path: root})
