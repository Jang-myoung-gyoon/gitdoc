"""Local JSON-lines interface for the native GitDoc window."""

import base64
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from functools import lru_cache
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageChops

from editing import read_pdf_notes
from preview import page_sizes, preview_path
from store import (
    VERSION_FIELDS, branch_history, branch_list, create_document, database, find_branch,
    find_document, find_version, initialize, merge_state, new_branch, search_documents, text_diff,
)
from uploads import load_local_file
from workbook import read_workbook
from workflow import merge_revision, require, restore_revision, review_revision, save_document_edit, save_proposal


def item(db, doc, version, fields=VERSION_FIELDS):
    return require(find_version(db, doc, version, fields), "버전")


def library(query=""):
    with database() as db:
        return [dict(row) for row in search_documents(db, query[:150])]


def detail(doc, branch=None, version=None):
    with database() as db:
        document = require(find_document(db, doc), "문서")
        if branch is None and version is not None:
            branch = item(db, doc, version)["branch_name"]
        active = require(find_branch(db, doc, branch or "main"), "브랜치")
        return {
            "title": document["title"], "branch": dict(active),
            "branches": [dict(row) for row in branch_list(db, doc)],
            "versions": [dict(row) for row in branch_history(db, active)],
        }


def branch_comparison(doc, target, source):
    with database() as db:
        left = require(find_branch(db, doc, target), "대상 브랜치")
        right = require(find_branch(db, doc, source), "가져올 브랜치")
        ancestor, status = merge_state(db, left, right)
        return {"base": left["head_id"], "target": right["head_id"], "state": status,
                "ancestor": ancestor["version_label"], "source": source}


def versions(doc, values, text=False):
    fields = VERSION_FIELDS + (", extracted" if text else "")
    with database() as db:
        return [dict(item(db, doc, value, fields)) for value in values]


def content(doc, version):
    with database() as db:
        return item(db, doc, version, "content")["content"]


def page_path(record):
    return preview_path(record["extension"], record["sha256"],
                        lambda: content(record["document_id"], record["id"]))


def view(doc, ids, mode="auto"):
    records = versions(doc, ids, text=True)
    diff, truncated = text_diff(records[0]["extracted"], records[-1]["extracted"], limit=450)
    if truncated:
        diff.append("… 일부 변경만 표시합니다.")
    result = {"versions": [row["version_label"] for row in records], "diff": "\n".join(diff)}
    if mode == "auto" and all(row["extension"] in ("xls", "xlsx") for row in records):
        try:
            books = [read_workbook(row["extension"], content(doc, row["id"])) for row in records]
        except Exception as error:
            raise ValueError("시트를 읽을 수 없습니다. 인쇄 레이아웃을 확인하세요.") from error
        return dict(result, books=books, pages=0)
    if len(records) == 2 and records[0]["sha256"] != records[1]["sha256"]:
        with ThreadPoolExecutor(max_workers=2) as workers:
            previews = list(workers.map(inspect_preview, records))
    else:
        previews = [inspect_preview(record) for record in records]
    counts, faults = map(list, zip(*previews))
    return dict(result, pages=max(counts, default=0), counts=counts, faults=faults)


def inspect_preview(record):
    try:
        return len(page_sizes(page_path(record))), ""
    except Exception as error:
        return 0, str(error)


@lru_cache(maxsize=16)
def pixels(path, number):
    if number > len(page_sizes(path)):
        return None
    with pdfium.PdfDocument(path) as document:
        with closing(document[number - 1]) as page:
            scale = min(4, 1350 / max(page.get_size()))
            with closing(page.render(scale=scale)) as bitmap:
                return bitmap.to_pil().convert("RGB").copy()


def regions(before, after):
    if before is None or after is None or before.size != after.size:
        return [[0, 0, 1, 1]] if before is not None or after is not None else []
    width, height = before.size
    tile = 28
    cols, rows = (width + tile - 1) // tile, (height + tile - 1) // tile
    red, green, blue = ImageChops.difference(before, after).split()
    mask = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(lambda value: 255 if value >= 28 else 0)
    pad = Image.new("L", (cols * tile, rows * tile))
    pad.paste(mask)
    points = {(x, y) for y in range(rows) for x in range(cols)
              if pad.crop((x*tile, y*tile, (x+1)*tile, (y+1)*tile)).getbbox()}
    result = []
    while points and len(result) < 160:
        x, y = min(points, key=lambda point: (point[1], point[0]))
        points.remove((x, y))
        pending = [(x, y)]
        left = right = x
        top = bottom = y
        for cx, cy in pending:
            left, right = min(left, cx), max(right, cx)
            top, bottom = min(top, cy), max(bottom, cy)
            for px in range(cx-1, cx+2):
                for py in range(cy-1, cy+2):
                    if (px, py) in points:
                        points.remove((px, py))
                        pending.append((px, py))
        result.append([left*tile/width, top*tile/height, min(1,(right+1)*tile/width),
                       min(1,(bottom+1)*tile/height)])
    return result


def page_images(records, number):
    images = []
    for record in records:
        try:
            images.append(pixels(page_path(record), number))
        except Exception:
            images.append(None)
    return images


def encoded_image(image):
    if image is None:
        return None
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=False)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def page(doc, ids, number, comparable=True):
    records = versions(doc, ids)
    images = page_images(records, number)
    notes = []
    if len(records) == 1 and records[0]["extension"] == "pdf":
        try:
            notes = read_pdf_notes(content(doc, ids[0]), number)
        except Exception:
            pass
    return {"images": [encoded_image(image) for image in images], "notes": notes,
            "regions": regions(*images) if len(images) == 2 and comparable else []}


def changes(doc, ids, counts):
    records = versions(doc, ids)
    if len(records) != 2 or not all(counts):
        return []
    found = []
    for index in range(1, max(counts) + 1):
        images = page_images(records, index)
        if all(image is not None or index > count for image, count in zip(images, counts)):
            for change in range(len(regions(*images))):
                found.append([index, change])
    return found


def register(file, title):
    upload = load_local_file(file)
    with database(write=True) as db:
        return create_document(db, title.strip(), upload)


def create_branch(doc, name, start):
    with database(write=True) as db:
        new_branch(db, doc, name.strip().lower(), start)
    return name.strip().lower()


def review(doc, version, action, base, note=""):
    review_revision(doc, version, action, base, note)
    return version


def save_file(doc, version, path):
    Path(path).write_bytes(content(doc, version))
    return path


def merge(doc, target, source, base, head, reason, file=None):
    return merge_revision(doc, target, source, base, head, reason, load_local_file(file) if file else None)


def submit(doc, branch, base, file, reason):
    return save_proposal(doc, branch, base, load_local_file(file), reason)


def edit(doc, branch, base, fields, reason):
    return save_document_edit(doc, branch, base, fields, reason)


def restore(doc, branch, version, base, reason):
    return restore_revision(doc, branch, version, base, reason)


OPERATIONS = {
    "library": library, "detail": detail, "branch_compare": branch_comparison,
    "view": view, "page": page, "changes": changes, "register": register,
    "branch": create_branch, "submit": submit, "edit": edit, "review": review,
    "restore": restore, "save": save_file, "merge": merge,
}


def main():
    initialize()
    for line in sys.stdin.buffer:
        try:
            command = json.loads(line)
            result = {"result": OPERATIONS[command["op"]](**command["params"])}
        except Exception as error:
            result = {"error": str(error) or type(error).__name__}
        sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
