"""Native presentation editor operations on a private working copy of the PPTX."""

import hashlib
import io
import math
import re
import tempfile
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.util import Pt

from desktop.editor_fonts import use_rendered_fonts
from editing import SLIDE, format_block, open_package, replace_run_span, valid_text, XML_SPACE
from page_text import embedded_text
from preview import (CACHE, PreviewError, convert, page_dimensions, preview_cache_path, preview_path,
                     preview_png, publish_preview)
from page_alignment import reuse_fingerprints
from uploads import MAX_FILE

ALIGN = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
         "right": PP_ALIGN.RIGHT, "justify": PP_ALIGN.JUSTIFY}
SHAPES = {"rectangle": MSO_SHAPE.RECTANGLE, "ellipse": MSO_SHAPE.OVAL,
          "triangle": MSO_SHAPE.ISOSCELES_TRIANGLE, "arrow": MSO_SHAPE.RIGHT_ARROW}
EMU = 12700
MAX_HISTORY = 128 * 1024 * 1024


def save(presentation):
    output = io.BytesIO()
    presentation.save(output)
    data = output.getvalue()
    if len(data) > MAX_FILE:
        raise ValueError("프레젠테이션이 최대 파일 크기를 초과했습니다.")
    return data


def signature(slide):
    return hashlib.sha256(slide.part.blob).digest()


def frame_at(shape, target):
    if "cell" in target:
        row, col = target["cell"]
        cell = shape.table.cell(row, col)
        if cell.is_spanned:
            raise ValueError("병합된 셀의 시작 위치를 선택하세요.")
        return cell.text_frame
    if shape.has_text_frame:
        return shape.text_frame
    raise ValueError("편집 가능한 텍스트를 선택하세요.")


def resize_paragraphs(frame, values):
    # Keep the original runs and their properties for unchanged text spans.
    for number, value in enumerate(values):
        if number >= len(frame.paragraphs):
            new = frame.add_paragraph()
            old_properties = frame.paragraphs[number - 1]._p.pPr
            if old_properties is not None:
                new._p.insert(0, deepcopy(old_properties))
        paragraph = frame.paragraphs[number]
        previous = paragraph.text
        if previous == value:
            continue
        runs = [node for node in paragraph._p.iter(qn("a:t")) if node.text]
        if not runs or previous != "".join(node.text for node in runs):
            paragraph.text = value
            continue
        offsets = [0]
        for node in runs:
            offsets.append(offsets[-1] + len(node.text))
        if max(len(previous), len(value)) <= 4000:
            opcodes = SequenceMatcher(None, previous, value, autojunk=False).get_opcodes()
        else:
            left = 0
            while left < min(len(previous), len(value)) and previous[left] == value[left]:
                left += 1
            right = 0
            while right < min(len(previous), len(value)) - left and previous[-1-right] == value[-1-right]:
                right += 1
            opcodes = [("replace", left, len(previous)-right, left, len(value)-right)]
        for operation, start, end, first, last in reversed(opcodes):
            if operation != "equal":
                replace_run_span(runs, offsets, start, end, value[first:last])
        for node in runs:
            if node.text and node.text.strip() != node.text:
                node.set(XML_SPACE, "preserve")
    for paragraph in frame.paragraphs[len(values):]:
        frame._txBody.remove(paragraph._p)


def split_format_runs(paragraph, start, end):
    """Split only runs intersecting a selected character range."""
    if start == end:
        return list(paragraph._p.iter(qn("a:t")))
    index = 0
    selected = []
    for element in list(paragraph._p):
        if element.tag == qn("a:br"):
            index += 1
            continue
        content = element.find(qn("a:t"))
        if content is None:
            continue
        value = content.text or ""
        first, last = max(0, start - index), min(len(value), end - index)
        index += len(value)
        if first >= last or element.tag != qn("a:r"):
            continue
        for a, b in ((0, first), (first, last), (last, len(value))):
            if a == b:
                continue
            clone = deepcopy(element)
            item = clone.find(qn("a:t"))
            item.text = value[a:b]
            if item.text.strip() != item.text:
                item.set(XML_SPACE, "preserve")
            element.addprevious(clone)
            if a == first and b == last:
                selected.append(item)
        paragraph._p.remove(element)
    return selected


def format_text(frame, tool, value, selection):
    if tool in ("bold", "italic", "underline"):
        attr = {"bold":"b","italic":"i","underline":"u"}[tool]
        active = any(node.get(attr) in ("1", "true", "sng") for p in frame.paragraphs
                     for node in p._p.iter(qn("a:rPr")))
        value = not active if value is None else bool(value)
    start, end = sorted(selection or [0, 0])
    index = 0
    for paragraph in frame.paragraphs:
        length = len(paragraph.text)
        first, last = start-index, end-index
        index += length + 1
        if start != end and (last <= 0 or first >= length):
            continue
        if tool == "align":
            paragraph.alignment = ALIGN[value]
        elif tool == "spacing":
            paragraph.line_spacing = {"1": 1.0, "1.5": 1.5, "2": 2.0}[value]
        elif tool == "indent":
            paragraph.level = min(8, max(0, paragraph.level + int(value)))
        elif tool in ("bullet", "number"):
            props = paragraph._p.get_or_add_pPr()
            choice = qn("a:buChar" if tool == "bullet" else "a:buAutoNum")
            already = props.find(choice) is not None
            for name in ("a:buChar", "a:buAutoNum", "a:buBlip", "a:buNone"):
                if (old := props.find(qn(name))) is not None:
                    props.remove(old)
            node = OxmlElement("a:buNone" if already else "a:buChar" if tool == "bullet" else "a:buAutoNum")
            if not already:
                node.set("char" if tool == "bullet" else "type", "•" if tool == "bullet" else "arabicPeriod")
            boundary = next((item for item in props if item.tag in {qn("a:tabLst"), qn("a:defRPr"), qn("a:extLst")}), None)
            if boundary is None:
                props.append(node)
            else:
                boundary.addprevious(node)
        else:
            runs = split_format_runs(paragraph, first, last)
            if tool == "highlight":
                for run in runs:
                    props = run.getparent().get_or_add_rPr()
                    if (old := props.find(qn("a:highlight"))) is not None:
                        props.remove(old)
                    highlight = OxmlElement("a:highlight")
                    color = OxmlElement("a:srgbClr")
                    color.set("val", value)
                    highlight.append(color)
                    boundary = next((item for item in props if item.tag in {
                        qn("a:latin"), qn("a:ea"), qn("a:cs"), qn("a:sym"),
                        qn("a:hlinkClick"), qn("a:hlinkMouseOver"), qn("a:rtl"), qn("a:extLst")
                    }), None)
                    if boundary is None:
                        props.append(highlight)
                    else:
                        boundary.addprevious(highlight)
            else:
                format_block(f"{{{SLIDE}}}", paragraph._p, runs, {tool: value})


def text_properties(frame, width, height, scale):
    sx, sy = scale
    paragraphs = frame.paragraphs
    return {"text": frame.text, "inset": [frame.margin_left/width*sx, frame.margin_top/height*sy],
            "size": next((run.font.size.pt for p in paragraphs for run in p.runs if run.font.size is not None), 18),
            "align": [next((name for name, value in ALIGN.items() if p.alignment == value), "left")
                      for p in paragraphs]}


def objects(shapes, width, height, offset=(0, 0), scale=(1, 1), parent=None):
    items = []
    for shape in shapes:
        if None in (shape.left, shape.top, shape.width, shape.height):
            continue
        sx, sy = scale
        x, y = offset[0] + shape.left*sx, offset[1] + shape.top*sy
        w, h = shape.width*sx, shape.height*sy
        box = [x/width, y/height, (x+w)/width, (y+h)/height]
        item = {"id": shape.shape_id, "parent": parent, "box": box, "name": shape.name,
                "group": shape.shape_type == MSO_SHAPE_TYPE.GROUP}
        items.append(item)
        if shape.has_text_frame:
            item.update(text_properties(shape.text_frame, width, height, scale))
        if shape.has_table:
            table = shape.table
            row_y = y
            for row, row_data in enumerate(table.rows):
                col_x = x
                for col, col_data in enumerate(table.columns):
                    cell = table.cell(row, col)
                    if not cell.is_spanned:
                        cell_width = sum(table.columns[i].width for i in range(col, col+cell.span_width))
                        cell_height = sum(table.rows[i].height for i in range(row, row+cell.span_height))
                        items.append(dict(item, cell=[row, col],
                                          box=[col_x/width, row_y/height,
                                               (col_x+cell_width*sx)/width, (row_y+cell_height*sy)/height],
                                          **text_properties(cell.text_frame, width, height, scale)))
                    col_x += col_data.width*sx
                row_y += row_data.height*sy
        if item["group"]:
            transform = shape._element.grpSpPr.xfrm
            if transform.chExt is not None and transform.chExt.cx and transform.chExt.cy:
                scales = w/transform.chExt.cx, h/transform.chExt.cy
                offsets = x-transform.chOff.x*scales[0], y-transform.chOff.y*scales[1]
                items.extend(objects(shape.shapes, width, height, offsets, scales, shape.shape_id))
    return items


def text_hits(items, pdf, page):
    # Match rendered text within its owning object. Ambiguous glyph matches
    # keep the full editable shape bounds instead of binding another object.
    lines = embedded_text(pdf, page)
    glyphs = [glyph for line in lines for glyph in line if not glyph[0].isspace()]
    letters = "".join(char for char, _ in glyphs)
    for item in items:
        word = "".join(char for char in item.get("text", "") if not char.isspace())
        if not word:
            continue
        x, y, right, bottom = item["box"]
        matches = []
        start = letters.find(word)
        while start >= 0:
            boxes = [box for _, box in glyphs[start:start+len(word)]]
            hit = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes)]
            mx, my = (hit[0]+hit[2])/2, (hit[1]+hit[3])/2
            if x-.015 <= mx <= right+.015 and y-.015 <= my <= bottom+.015:
                matches.append(hit)
            start = letters.find(word, start+len(word))
        if len(matches) == 1:
            item["ink"] = matches[0]


def find_shape(shapes, shape_id):
    for shape in shapes:
        if shape.shape_id == shape_id:
            return shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            child = find_shape(shape.shapes, shape_id)
            if child is not None:
                return child
    return None


def duplicate_slide(source, target):
    remap = {}
    for rel in source.part.rels.values():
        if rel.reltype in (RT.SLIDE_LAYOUT, RT.NOTES_SLIDE):
            continue
        value = rel.target_ref if rel.is_external else rel.target_part
        remap[rel.rId] = target.part.relate_to(value, rel.reltype, rel.is_external)
    clone = deepcopy(source._element)
    for el in clone.iter():
        for key, value in list(el.attrib.items()):
            if key.startswith("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}") and value in remap:
                el.set(key, remap[value])
    target.part._element = clone
    # The Slide lazy proxy has already been created by add_slide.
    target._element = clone
    if source.has_notes_slide:
        original = source.notes_slide.notes_text_frame
        target_notes = target.notes_slide.notes_text_frame
        if original is not None and target_notes is not None:
            target_notes.text = original.text


def move_layer(node, forward):
    sibling = node.getnext() if forward else node.getprevious()
    if sibling is None or sibling.tag in (qn("p:nvGrpSpPr"), qn("p:grpSpPr"), qn("p:extLst")):
        return
    if forward:
        sibling.addnext(node)
    else:
        sibling.addprevious(node)


def ungroup(shape):
    node = shape._element
    xfrm = node.grpSpPr.xfrm
    if shape.rotation or xfrm.get("flipH") in ("1", "true") or xfrm.get("flipV") in ("1", "true"):
        raise ValueError("회전되거나 뒤집힌 그룹은 그룹 해제를 지원하지 않습니다.")
    if not xfrm.chExt.cx or not xfrm.chExt.cy:
        raise ValueError("그룹의 크기를 확인하세요.")
    sx, sy = shape.width/xfrm.chExt.cx, shape.height/xfrm.chExt.cy
    for child in list(shape.shapes):
        child.left = round(shape.left+(child.left-xfrm.chOff.x)*sx)
        child.top = round(shape.top+(child.top-xfrm.chOff.y)*sy)
        child.width = round(child.width*sx)
        child.height = round(child.height*sy)
        node.addprevious(child._element)
    node.getparent().remove(node)


class Deck:
    def __init__(self, data, digest=None):
        with open_package(data):
            pass
        self.states = [data]
        self.position = 0
        self.directory = tempfile.TemporaryDirectory(prefix="gitdoc-slide-")
        self.pdfs = {}
        original = Presentation(io.BytesIO(data))
        self.original_ids = [slide.slide_id for slide in original.slides]
        self.original_signature = [signature(slide) for slide in original.slides]
        self.base_preview = None
        if digest:
            try:
                path = preview_path("pptx", digest, lambda: data)
                if len(page_dimensions(path)) == len(original.slides):
                    self.base_preview = path
            except (OSError, PreviewError):
                pass

    @property
    def data(self):
        return self.states[self.position]

    def close(self):
        self.directory.cleanup()

    def source_page(self, presentation, index, data=None):
        slides = presentation.slides
        slide = slides[index]
        unchanged = (self.base_preview is not None and
                     len(slides) == len(self.original_ids) and slide.slide_id == self.original_ids[index] and
                     slide._element.get("show") not in ("0", "false") and
                     signature(slide) == self.original_signature[index] and
                     [entry.id for entry in slides._sldIdLst] == self.original_ids)
        if unchanged:
            return self.base_preview, index + 1

        # The complete slide order stays available to Impress for numbering and
        # links. Only the requested output PDF page is rendered.
        data = self.data if data is None else data
        key = hashlib.sha256(data).digest(), index
        if key not in self.pdfs:
            with tempfile.TemporaryDirectory(dir=self.directory.name) as run:
                folder = Path(run)
                source, pdf = folder / "slide.pptx", folder / "slide.pdf"
                source.write_bytes(data)
                convert(source, pdf, "pptx", page_range=str(index+1), hidden_slides=True)
                if not pdf.is_file() or len(page_dimensions(pdf)) != 1:
                    raise ValueError("선택한 슬라이드의 미리보기를 만들 수 없습니다.")
                dst = Path(self.directory.name) / (key[0].hex() + f"-{index}.pdf")
                pdf.replace(dst)
            if len(self.pdfs) >= 32:
                self.pdfs.pop(next(iter(self.pdfs))).unlink(missing_ok=True)
            self.pdfs[key] = dst
        return self.pdfs[key], 1

    def render(self, presentation, index, data=None):
        slides = presentation.slides
        index = max(0, min(len(slides)-1, index))
        slide = slides[index]
        width, height = presentation.slide_width, presentation.slide_height
        items = objects(slide.shapes, width, height)
        pdf, page = self.source_page(presentation, index, data)
        png = Path(self.directory.name) / "slide.png"
        png.write_bytes(preview_png(pdf, page, prefer_pdfium=True))
        try:
            text_hits(items, pdf, page)
        except (OSError, ValueError, RuntimeError):
            pass
        fonts = use_rendered_fonts(items, pdf, page, Path(self.directory.name) / "fonts")
        return {"page": index+1, "count": len(slides), "width": width/EMU, "height": height/EMU,
                "objects": items, "image": str(png), "fonts": fonts}

    def prime_preview(self, digest):
        if self.base_preview is None:
            return
        deck = Presentation(io.BytesIO(self.data))
        slides = deck.slides
        if [item.slide_id for item in slides] != self.original_ids:
            return
        changed = [i for i, slide in enumerate(slides) if signature(slide) != self.original_signature[i]]
        if not changed or any(slide._element.get("show") in ("0", "false") for slide in slides):
            return
        known = PdfReader(self.base_preview)
        sources = {i: self.source_page(deck, i) for i in changed}
        prepared = {i: PdfReader(path).pages[number - 1] for i, (path, number) in sources.items()}
        with PdfWriter() as writer:
            for index, page in enumerate(known.pages):
                writer.add_page(prepared.get(index, page))
            with tempfile.TemporaryDirectory(dir=CACHE) as run:
                output = Path(run) / "preview.pdf"
                writer.write(output)
                if publish_preview("pptx", digest, output):
                    reuse_fingerprints(self.base_preview, preview_cache_path("pptx", digest), sources)

    def page(self, number, operation=None, text_edit=None, text_page=None, patch_text=False, **fields):
        deck = Presentation(io.BytesIO(self.data))
        index = number - 1
        if not 0 <= index < len(deck.slides):
            raise ValueError("슬라이드 번호를 확인하세요.")
        if text_edit is not None:
            target = text_edit["target"]
            edit_index = index if text_page is None else text_page - 1
            shape = find_shape(deck.slides[edit_index].shapes, target["id"])
            if shape is None:
                raise ValueError("편집할 개체를 찾을 수 없습니다.")
            value = text_edit["text"].replace("\t", " ").replace("\r", "")
            value = valid_text(value.replace("\v", "\n"), 20000, multiline=True)
            resize_paragraphs(frame_at(shape, target), value.split("\n"))
        if operation in ("undo", "redo"):
            position = self.position + (-1 if operation == "undo" else 1)
            if 0 <= position < len(self.states):
                result = self.render(Presentation(io.BytesIO(self.states[position])), index, self.states[position])
                self.position = position
            else:
                result = self.render(deck, index)
        else:
            selected = None
            if operation and operation != "page":
                index, selected = self.apply(deck, index, operation, **fields)
            changed = text_edit is not None or (operation and operation != "page")
            output = save(deck) if changed else None
            if patch_text and operation == "align" and selected is not None:
                shape = find_shape(deck.slides[index].shapes, selected["id"])
                result = {"patch": dict(selected, **text_properties(
                    frame_at(shape, selected), deck.slide_width, deck.slide_height, (1, 1)))}
            else:
                result = self.render(Presentation(io.BytesIO(output)) if output is not None else deck, index, output)
            if selected is not None:
                result["selected"] = selected
            if output is not None and output != self.data:
                del self.states[self.position+1:]
                self.states.append(output)
                if len(self.states) > 24 or sum(map(len, self.states)) > MAX_HISTORY:
                    while len(self.states) > 2 and (len(self.states) > 24 or sum(map(len, self.states)) > MAX_HISTORY):
                        del self.states[1]
                self.position = len(self.states)-1
        return dict(result, undo=self.position > 0, redo=self.position+1 < len(self.states),
                    dirty=self.position > 0)

    def apply(self, deck, index, operation, target=None, ids=(), value=None, selection=None):
        slide = deck.slides[index]
        shapes = slide.shapes
        if operation in ("new_slide", "duplicate_slide", "delete_slide"):
            entries = deck.slides._sldIdLst
            if operation == "delete_slide":
                if len(entries) <= 1:
                    raise ValueError("마지막 슬라이드는 삭제할 수 없습니다.")
                entry = entries[index]
                deck.part.drop_rel(entry.rId)
                entries.remove(entry)
                return min(index, len(entries)-1), None
            layout = slide.slide_layout if operation == "duplicate_slide" else min(
                slide.slide_layout.slide_master.slide_layouts, key=lambda item: len(item.placeholders))
            new = deck.slides.add_slide(layout)
            if operation == "duplicate_slide":
                duplicate_slide(slide, new)
            entries[index].addnext(entries[-1])
            return index+1, None

        if operation in ("textbox", "image", "shape", "table"):
            offset = .025*(len(shapes) % 6)
            x, y = int(deck.slide_width*(.11+offset)), int(deck.slide_height*(.12+offset))
            w, h = int(deck.slide_width*.36), int(deck.slide_height*.24)
            if operation == "textbox":
                shape = shapes.add_textbox(x, y, w, h)
                frame = shape.text_frame
                frame.text = "텍스트 입력"
                for run in frame.paragraphs[0].runs:
                    run.font.size = Pt(24)
            elif operation == "image":
                path = Path(value)
                if path.suffix.lower() not in (".png", ".jpg", ".jpeg") or path.stat().st_size > 15*1024*1024:
                    raise ValueError("15MB 이하 PNG 또는 JPEG 이미지를 선택하세요.")
                shape = shapes.add_picture(str(path), x, y, width=w)
                if shape.height > h*2:
                    shape.width = round(shape.width*h*2/shape.height)
                    shape.height = h*2
            elif operation == "shape":
                shape = shapes.add_shape(SHAPES[value], x, y, w//2, h)
                shape.fill.solid()
                shape.fill.fore_color.rgb = RGBColor(38, 104, 197)
            else:
                rows, cols = value
                if not 1 <= rows <= 12 or not 1 <= cols <= 12:
                    raise ValueError("행과 열은 각각 1~12까지 선택하세요.")
                shape = shapes.add_table(rows, cols, x, y, w, h)
            return index, {"id": shape.shape_id}

        if operation == "group":
            selected = [shape for shape in shapes if shape.shape_id in ids]
            if len(selected) < 2:
                raise ValueError("그룹으로 묶을 개체를 두 개 이상 선택하세요.")
            if any(shape.is_placeholder for shape in selected):
                raise ValueError("슬라이드 레이아웃의 자리 표시자는 그룹으로 묶을 수 없습니다.")
            group = shapes.add_group_shape(selected)
            return index, {"id": group.shape_id}

        shape = find_shape(shapes, target["id"]) if target else None
        if shape is None:
            raise ValueError("슬라이드 개체를 먼저 선택하세요.")
        if operation in ("forward", "backward"):
            move_layer(shape._element, operation == "forward")
        elif operation == "ungroup" and shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            ungroup(shape)
            return index, None
        elif operation == "delete_object":
            shape._element.getparent().remove(shape._element)
            return index, None
        elif operation == "align_object":
            choices = {"left": ("left", 0), "horizontal": ("left", (deck.slide_width-shape.width)//2),
                       "right": ("left", deck.slide_width-shape.width), "top": ("top", 0),
                       "vertical": ("top", (deck.slide_height-shape.height)//2),
                       "bottom": ("top", deck.slide_height-shape.height)}
            if shape._element.getparent() is not shapes._spTree:
                raise ValueError("슬라이드에 직접 배치된 개체를 선택하세요.")
            attribute, coordinate = choices[value]
            setattr(shape, attribute, coordinate)
        elif operation == "resize_object":
            dx, dy = value
            if any(not math.isfinite(v) or abs(v) > 2 for v in value):
                raise ValueError("개체 크기를 확인하세요.")
            shape.width = max(4*EMU, shape.width+round(dx*deck.slide_width))
            shape.height = max(4*EMU, shape.height+round(dy*deck.slide_height))
        elif operation == "move_object":
            dx, dy = value
            if any(not math.isfinite(v) or abs(v) > 2 for v in value):
                raise ValueError("개체 이동 위치를 확인하세요.")
            shape.left += round(dx*deck.slide_width)
            shape.top += round(dy*deck.slide_height)
        else:
            if operation in ("color", "highlight") and not re.fullmatch("[0-9A-Fa-f]{6}", value):
                raise ValueError("텍스트 색상을 확인하세요.")
            if operation == "size" and not 6 <= value <= 120:
                raise ValueError("글자 크기는 6~120pt까지 설정할 수 있습니다.")
            if operation == "font":
                value = valid_text(value, 100).strip()
                if not value:
                    raise ValueError("글꼴 이름을 입력하세요.")
            format_text(frame_at(shape, target), operation, value, selection)
        return index, target
