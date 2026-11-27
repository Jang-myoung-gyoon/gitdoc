"""Local JSON-lines interface for the native GitDoc window."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pypdfium2_raw

from desktop.exporting import convert_format, export_formats, write_export
from editing import read_pdf_notes
from formats import WORKBOOK_EXTENSIONS
from preview import page_dimensions, page_sizes, preview_path
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
    selected = records[-1]
    result = {"versions": [row["version_label"] for row in records], "diff": "\n".join(diff),
              "filename": selected["filename"], "save_as": export_formats(selected["extension"])}
    if mode == "auto" and all(row["extension"] in WORKBOOK_EXTENSIONS for row in records):
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
    counts, faults, paths, ratios = map(list, zip(*previews))
    return dict(result, pages=max(counts, default=0), counts=counts, faults=faults, paths=paths, ratios=ratios,
                pdfium_directory=str(Path(pypdfium2_raw.__file__).resolve().parent),
                pdf_notes=len(records) == 1 and records[0]["extension"] == "pdf")


def inspect_preview(record):
    try:
        path = page_path(record)
        dimensions = page_dimensions(path)
        ratios = [height / width if width > 0 and 0 < height / width < 100 else 1.4
                  for width, height in dimensions]
        return len(dimensions), "", str(path), ratios
    except Exception as error:
        return 0, str(error), None, []


def page_notes(doc, version, number):
    try:
        return read_pdf_notes(content(doc, version), number)
    except Exception:
        return []


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


def print_preview(doc, version):
    record = versions(doc, [version])[0]
    path = page_path(record)
    page_sizes(path)
    return {"path": str(path), "title": record["filename"],
            "pdfium_directory": str(Path(pypdfium2_raw.__file__).resolve().parent)}


def save_file(doc, version, path, format):
    record = versions(doc, [version])[0]
    source = record["extension"]
    if format not in export_formats(source) or Path(path).suffix.lower() != f".{format}":
        raise ValueError("저장 형식과 파일 확장자를 확인하세요.")
    if format == source:
        data = content(doc, version)
    elif format == "pdf":
        data = page_path(record).read_bytes()
    else:
        data = convert_format(source, format, content(doc, version))
    write_export(path, data)
    return format


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
    "view": view, "page_notes": page_notes, "register": register,
    "branch": create_branch, "submit": submit, "edit": edit, "review": review,
    "restore": restore, "save": save_file, "print": print_preview, "merge": merge,
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
