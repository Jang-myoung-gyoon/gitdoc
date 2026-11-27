"""Local JSON-lines interface for the native GitDoc window."""

import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import pypdfium2_raw
from pypdf.errors import PyPdfError
from pypdfium2 import PdfiumError

from browser_viewer import viewer_url, workspace_info
from delimited import aligned_rows, key_columns, lines as table_lines, sheet as table_sheet, table_rows
from desktop_link import document_path
from desktop.exporting import convert_format, export_formats, write_export
from editing import editable_office_blocks, read_pdf_notes
from extract import comparison_text
from formats import DELIMITED_EXTENSIONS, WORKBOOK_EXTENSIONS
from page_alignment import comparison_pages, footer_regions, reused_pages
from page_text import page_text
from preview import CACHE, PreviewError, page_dimensions, page_sizes, poppler_available, preview_path, preview_png, unembedded_fonts
from store import (
    VERSION_FIELDS, branch_history, branch_list, create_document, database, desktop_review_enabled, find_branch,
    find_document, find_version, first_parent_ids, initialize, merge_state, new_branch, search_documents, set_desktop_review, text_diff,
)
from uploads import load_local_file, prepare_upload
from workbook import read_workbook
from workflow import merge_revision, require, reset_branch, restore_revision, review_revision, save_document_edit, save_proposal


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
            "first_parents": first_parent_ids(db, active["head_id"]),
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


def view(doc, ids, mode="auto", row_key=None):
    records = versions(doc, ids, text=True)
    before, selected = records[0], records[-1]
    left, right = comparison_text(before), comparison_text(selected)
    result = {"versions": [row["version_label"] for row in records], "filename": selected["filename"],
              "save_as": export_formats(selected["extension"]),
              "workbook": selected["extension"] in WORKBOOK_EXTENSIONS}
    if mode == "auto" and selected["extension"] in DELIMITED_EXTENSIONS:
        try:
            tables = [table_rows(row["extension"], content(doc, row["id"])) for row in records]
        except ValueError as error:
            return dict(result, pages=0, faults=[str(error)])
        columns = key_columns(*tables) if len(tables) == 2 else {}
        if row_key in columns:
            tables = aligned_rows(*tables, columns[row_key])
            left, right = ("\n".join(table_lines(table)) for table in tables)
        result.update(books=[[table_sheet(table)] for table in tables], pages=0, delimited=True,
                      row_keys=list(columns), row_key=row_key if row_key in columns else None)
    diff, truncated = text_diff(left, right, limit=450)
    if truncated:
        diff.append("… 일부 변경만 표시합니다.")
    result["diff"] = "\n".join(diff)
    if result.get("delimited"):
        return result
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
    pairs, sources = comparison_pages(doc, ids, paths, counts)
    return dict(result, pages=len(pairs), pairs=pairs, sources=sources, counts=counts, faults=faults, paths=paths, ratios=ratios,
                footers=footer_regions(paths, pairs), reused=reused_pages(paths, pairs),
                substitute_fonts=poppler_available() and any(path and unembedded_fonts(Path(path)) for path in paths),
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


@lru_cache(maxsize=1)
def font_raster_folder():
    return tempfile.TemporaryDirectory(prefix="gitdoc-font-raster-")


def font_raster(path, number):
    source = Path(path).resolve()
    if source.parent != CACHE.resolve() or source.suffix != ".pdf":
        raise ValueError("미리보기 파일 경로를 확인하세요.")
    output = Path(font_raster_folder().name) / "page.png"
    output.write_bytes(preview_png(source, number))
    return str(output)


def preview_text(paths, pages, number):
    return [page_text(Path(path), page) if path and page else [] for path, page in zip(paths, pages)]


def register(file, title):
    upload = load_local_file(file)
    with database(write=True) as db:
        return create_document(db, title.strip(), upload)


def create_branch(doc, name, start):
    with database(write=True) as db:
        new_branch(db, doc, name.strip().lower(), start)
    return name.strip().lower()


def review(doc, version, action, base, note=""):
    review_revision(doc, version, action, base, note, desktop=True)
    return version


def print_preview(doc, version):
    record = versions(doc, [version])[0]
    path = page_path(record)
    page_sizes(path)
    return {"path": str(path), "title": record["filename"],
            "pdfium_directory": str(Path(pypdfium2_raw.__file__).resolve().parent)}


def open_browser_view(doc=None, ids=(), branch=None, page=None, change=None, sheet=None,
                      cell=None, row_key=None, pages=False, open_tab=True):
    selection = {"base": ids[0], "target": ids[1], "row_key": row_key} if len(ids) == 2 else {
        "version": next(iter(ids), None)}
    path = document_path(doc, branch=branch, page=page, change=change, sheet=sheet, cell=cell,
                         view="pages" if pages else None, **selection)
    return {"browser_url": viewer_url(path, open_tab=open_tab), "opened": open_tab}


def save_file(doc, version, path, format, overwrite=True):
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
    write_export(path, data, overwrite=overwrite)
    return format


def merge(doc, target, source, base, head, reason, file=None):
    return merge_revision(doc, target, source, base, head, reason, load_local_file(file) if file else None)


def submit(doc, branch, base, file, reason):
    version = save_proposal(doc, branch, base, load_local_file(file), reason, desktop=True)
    return saved_version(doc, version)


def edit(doc, branch, base, fields, reason, file=None):
    version = save_document_edit(doc, branch, base, fields, reason,
                                 source=load_local_file(file) if file else None, desktop=True)
    return saved_version(doc, version)


def saved_version(doc, version):
    with database(readonly=True) as db:
        return dict(item(db, doc, version, "id, status"))


def desktop_workspace():
    with database(readonly=True) as db:
        return dict(workspace_info(), desktop_review=desktop_review_enabled(db))


def edit_contents(doc, version):
    with database(readonly=True) as db:
        record = item(db, doc, version, "extension, content")
    return editable_office_blocks(record["extension"], record["content"])


def restore(doc, branch, version, base, reason):
    return restore_revision(doc, branch, version, base, reason)


def rewind(doc, branch, version, base):
    return reset_branch(doc, branch, version, base)


class CanvasSession:
    working = None

    def open(self, doc, branch, base, number):
        self.close()
        with database(readonly=True) as db:
            active = require(find_branch(db, doc, branch), "브랜치")
            if active["head_id"] != base:
                raise ValueError("새 확정본에서 다시 편집하세요.")
            original = item(db, doc, base, "filename, extension, sha256, content")
        extension = original["extension"]
        if extension == "pptx":
            from desktop.presentation import Deck
            working = Deck(original["content"], original["sha256"])
        elif extension in ("doc", "hwp"):
            from desktop.paged_editor import PagedEditor
            working = PagedEditor(extension, original["content"])
        else:
            raise ValueError("캔버스 편집을 지원하는 문서를 선택하세요.")
        try:
            result = working.page(number)
        except Exception:
            working.close()
            raise
        self.working = working
        self.destination = doc, branch, base
        self.filename = original["filename"]
        self.extension = extension
        return result

    def page(self, **params):
        return self.active().page(**params)

    def active(self):
        if self.working is None:
            raise ValueError("문서 편집을 다시 시작하세요.")
        return self.working

    def save(self, reason, number, text_edit=None):
        working = self.active()
        if text_edit is not None:
            working.page(number, text_edit=text_edit)
        if working.position == 0:
            raise ValueError("저장할 변경이 없습니다.")
        slide = self.extension == "pptx"
        upload = prepare_upload(self.filename, working.data if slide else working.final_data())
        if slide:
            try:
                working.prime_preview(upload[3])
            except (OSError, PreviewError, PyPdfError, PdfiumError, ValueError):
                # The normal preview path remains available when reuse is impossible.
                pass
        label = "슬라이드 편집" if slide else "본문 편집"
        version = save_proposal(*self.destination, upload, reason.strip() or label, desktop=True)
        result = saved_version(self.destination[0], version)
        self.close()
        return result

    def close(self):
        if self.working is not None:
            self.working.close()
            self.working = None


canvas_editor = CanvasSession()


OPERATIONS = {
    "workspace": desktop_workspace, "desktop_review": set_desktop_review, "library": library, "detail": detail, "branch_compare": branch_comparison,
    "view": view, "page_notes": page_notes, "page_text": preview_text, "register": register,
    "branch": create_branch, "submit": submit, "edit": edit, "edit_contents": edit_contents, "review": review,
    "restore": restore, "rewind": rewind, "save": save_file, "print": print_preview, "merge": merge,
    "browser": open_browser_view, "font_raster": font_raster,
    "deck_open": canvas_editor.open, "deck_page": canvas_editor.page,
    "deck_save": canvas_editor.save, "deck_close": canvas_editor.close,
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
