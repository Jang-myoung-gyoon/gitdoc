import hmac
import io
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file, url_for
from pypdf.errors import PyPdfError
from werkzeug.exceptions import SecurityError

from browser_viewer import HOST, STORE_ID, WORKSPACE_PREFIX, browser_port, workspace_info
from delimited import aligned_rows, key_columns, lines as table_lines, sheet as table_sheet, table_rows
from editing import read_pdf_notes
from extract import comparison_text
from formats import DELIMITED_EXTENSIONS, WORKBOOK_EXTENSIONS
from page_alignment import comparison_pages, footer_regions
from page_text import page_text
from preview import PreviewError, page_dimensions, preview_path, preview_png, requires_poppler
from workbook import read_workbook
from store import (
    VERSION_FIELDS, branch_history, branch_list, database,
    detached_branch_document, find_branch, find_document, find_version, initialize, merge_state,
    search_documents, text_diff, view_branch,
)
from workflow import MissingEntity, review_revision

app = Flask(__name__)
app.jinja_env.globals.update(workbook_extensions=WORKBOOK_EXTENSIONS, workspace=workspace_info())
app.config["TRUSTED_HOSTS"] = ["localhost", "127.0.0.1"]
REVIEW_KEY = secrets.token_bytes(32)

flask_app = app.wsgi_app


def workspace_routes(environ, start_response):
    path = environ["PATH_INFO"]
    if path.startswith("/workspace/"):
        if path != WORKSPACE_PREFIX and not path.startswith(WORKSPACE_PREFIX + "/"):
            return Response("이 주소의 GitDoc 작업 공간을 찾을 수 없습니다.", status=404,
                            mimetype="text/plain", headers={"Cache-Control": "no-store"})(environ, start_response)
        environ["SCRIPT_NAME"] += WORKSPACE_PREFIX
        environ["PATH_INFO"] = path[len(WORKSPACE_PREFIX):] or "/"
    return flask_app(environ, start_response)


app.wsgi_app = workspace_routes


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' blob:; style-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
    )
    return response


def require(row):
    if row is None:
        abort(404)
    return row


def document(db, document_id):
    return require(find_document(db, document_id))


def version(db, document_id, version_id, fields=VERSION_FIELDS):
    return require(find_version(db, document_id, version_id, fields))


def branch(db, document_id, name):
    return require(find_branch(db, document_id, name))


@app.template_filter("without_view")
def without_view(params):
    params.pop("view", None)
    return params


@app.template_filter("local_time")
def local_time(value):
    utc = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return utc.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")


@app.get("/healthz")
def health():
    return jsonify(store=STORE_ID, web_approval=True, **workspace_info())


def comparison_history(db, active, original, updated):
    rows = {item["id"]: item for item in branch_history(db, active)}
    for item in (original, updated):
        rows[item["id"]] = item
    return sorted(rows.values(), key=lambda v: -v["number"])


@app.get("/")
def index():
    query = request.args.get("q", "").strip()[:150]
    with database(readonly=True) as db:
        documents = search_documents(db, query)
    return render_template("index.html", documents=documents, query=query)


def viewing_branch(db, document_id, opened):
    name = request.args.get("branch")
    active = view_branch(db, document_id, name, opened)
    if active is None and name and (detached := detached_branch_document(db, document_id, name)):
        abort(400, description=f"분리된 브랜치입니다. 새 문서 ID: {detached['document_id']}")
    return require(active)


@app.get("/documents/<int:document_id>")
def detail(document_id):
    with database(readonly=True) as db:
        doc = document(db, document_id)
        open_id = request.args.get("version", type=int)
        if open_id is None and "version" in request.args:
            abort(400, description="문서 버전 ID를 확인하세요.")
        opened = version(db, document_id, open_id) if open_id is not None else None
        active = viewing_branch(db, document_id, opened)
        branches = branch_list(db, document_id)
        versions = branch_history(db, active)
        current = version(db, document_id, active["head_id"])
    return render_template("detail.html", doc=doc, versions=versions, current=current,
                           opened=opened or current, active=active, branches=branches)


@app.get("/documents/<int:document_id>/branches/compare")
def compare_branches(document_id):
    with database(readonly=True) as db:
        doc = document(db, document_id)
        target = branch(db, document_id, request.args.get("target", "main"))
        source = branch(db, document_id, request.args.get("source"))
        common, _ = merge_state(db, target, source)
        fields = VERSION_FIELDS + ", extracted"
        a = version(db, document_id, target["head_id"], fields)
        b = version(db, document_id, source["head_id"], fields)
        ancestor = version(db, document_id, common["id"], fields)
        branches = branch_list(db, document_id)
        versions = comparison_history(db, target, a, b)
    changes = [
        (f"{target['name']} → {source['name']}", a, b),
        (f"공통 조상 → {target['name']}", ancestor, a),
        (f"공통 조상 → {source['name']}", ancestor, b),
    ]
    panels = [(title, before, after, *text_diff(comparison_text(before), comparison_text(after)))
              for title, before, after in changes]
    return render_template("branches.html", doc=doc, target=target, source=source,
                           ancestor=ancestor, a=a, b=b, panels=panels,
                           current=a, branches=branches, versions=versions)


@app.get("/documents/<int:document_id>/compare")
def compare(document_id):
    with database(readonly=True) as db:
        doc = document(db, document_id)
        target = request.args.get("target", type=int)
        if target is None:
            abort(400, description="비교할 버전을 선택하세요.")
        updated = version(db, document_id, target, VERSION_FIELDS + ", extracted")
        active = viewing_branch(db, document_id, updated)
        base = request.args.get("base", type=int) or updated["base_id"] or active["head_id"]
        original = version(db, document_id, base, VERSION_FIELDS + ", extracted")
        current = original if base == active["head_id"] else (
            updated if target == active["head_id"] else version(db, document_id, active["head_id"])
        )
        branches = branch_list(db, document_id)
        versions = comparison_history(db, active, original, updated)
        left, right = comparison_text(original), comparison_text(updated)
        row_key = request.args.get("row_key")
        if row_key is not None:
            try:
                left, right = ("\n".join(table_lines(table)) for table in
                               aligned_tables(db, document_id, base, target, row_key))
            except ValueError as error:
                abort(400, description=str(error))
    changes, truncated = text_diff(left, right)
    can_approve = (updated["status"] == "pending" and updated["branch_name"] == active["name"] and
                   original["id"] == updated["base_id"] == active["head_id"])
    token = approval_token(document_id, target, base) if can_approve else None
    return render_template("compare.html", doc=doc, original=original, updated=updated,
                           active=active, changes=changes, truncated=truncated,
                           current=current, branches=branches, versions=versions, approval=token)


def approval_token(document_id, version_id, base):
    scope = f"{STORE_ID}:{document_id}:{version_id}:{base}".encode()
    return hmac.digest(REVIEW_KEY, scope, "sha256").hex()


@app.post("/documents/<int:document_id>/versions/<int:version_id>/approve")
def approve(document_id, version_id):
    base = request.form.get("base", type=int)
    token = request.form.get("token", "")
    origin = request.headers.get("Origin")
    if (base is None or len(token) != 64 or not token.isascii() or
            (origin is not None and origin != request.host_url.rstrip("/")) or
            not hmac.compare_digest(token, approval_token(document_id, version_id, base))):
        abort(400, description="비교 화면에서 다시 확정하세요.")
    with database(readonly=True) as db:
        name = version(db, document_id, version_id)["branch_name"]
    try:
        review_revision(document_id, version_id, "approve", base, request.form.get("note", ""))
    except MissingEntity:
        abort(404)
    except ValueError as error:
        return render_template("error.html", message=str(error)), 409
    return redirect(url_for("compare", document_id=document_id, branch=name, base=base, target=version_id), code=303)


@app.get("/documents/<int:document_id>/versions/<int:version_id>/file")
def download(document_id, version_id):
    with database(readonly=True) as db:
        item = version(db, document_id, version_id, "filename, extension, content")
    pdf = item["extension"] == "pdf" and request.args.get("preview") == "1"
    return send_file(io.BytesIO(item["content"]),
                     mimetype="application/pdf" if pdf else "application/octet-stream",
                     as_attachment=not pdf, download_name=item["filename"])


@app.get("/documents/<int:document_id>/versions/<int:version_id>/notes/<int:page>")
def pdf_notes(document_id, version_id, page):
    with database(readonly=True) as db:
        item = version(db, document_id, version_id, "extension, content")
    if item["extension"] != "pdf":
        abort(404)
    try:
        return jsonify(notes=read_pdf_notes(item["content"], page))
    except (PyPdfError, ValueError, OSError, RecursionError):
        return jsonify(notes=[])


def aligned_tables(db, document_id, base, target, row_key, *, include_source=False):
    records = [version(db, document_id, item, "extension, content") for item in (base, target)]
    if any(item["extension"] not in DELIMITED_EXTENSIONS for item in records):
        raise ValueError("행 기준 열은 CSV/TSV에서만 사용할 수 있습니다.")
    tables = [table_rows(item["extension"], item["content"]) for item in records]
    column = key_columns(*tables).get(row_key)
    if column is None:
        raise ValueError("두 파일에서 중복과 빈 값이 없는 공통 열 제목을 선택하세요.")
    aligned = aligned_rows(*tables, column)
    if not include_source:
        return aligned
    source_rows = []
    for table, rows in zip(tables, aligned):
        by_key = {row[column]: index for index, row in enumerate(table[1:], 2) if any(row)}
        source_rows.append([1, *(by_key.get(row[column]) if row else None for row in rows[1:])])
    return aligned, source_rows


@app.get("/documents/<int:document_id>/versions/<int:version_id>/workbook")
def workbook(document_id, version_id):
    row_key = request.args.get("row_key")
    if row_key is not None:
        base, target = (request.args.get(name, type=int) for name in ("base", "target"))
        if base is None or target is None or version_id not in (base, target):
            abort(400, description="두 비교 버전을 지정하세요.")
        try:
            with database(readonly=True) as db:
                tables, source_rows = aligned_tables(db, document_id, base, target, row_key, include_source=True)
        except ValueError as error:
            return jsonify(error=str(error)), 422
        side = 0 if version_id == base else 1
        return jsonify(sheets=[{**table_sheet(tables[side]), "source_rows": source_rows[side]}])
    with database(readonly=True) as db:
        item = version(db, document_id, version_id, "extension, content")
    if item["extension"] not in WORKBOOK_EXTENSIONS:
        abort(404)
    try:
        sheets = read_workbook(item["extension"], item["content"])
    except ValueError as error:
        return jsonify(error=str(error)), 422
    except Exception:
        return jsonify(error="시트를 읽을 수 없습니다. PDF 페이지 대조나 원본을 확인하세요."), 422
    return jsonify(sheets=sheets)


def document_preview(document_id, version_id):
    with database(readonly=True) as db:
        item = version(db, document_id, version_id, "filename, extension, sha256")

    def original_content():
        with database(readonly=True) as db:
            return version(db, document_id, version_id, "content")["content"]

    return item, preview_path(item["extension"], item["sha256"], original_content)


@app.get("/documents/<int:document_id>/page-alignment")
def page_alignment(document_id):
    ids = [request.args.get(key, type=int) for key in ("base", "target")]
    if not all(ids):
        abort(400)
    try:
        paths = [document_preview(document_id, number)[1] for number in ids]
        counts = [len(page_dimensions(path)) for path in paths]
    except PreviewError as error:
        return jsonify(error=str(error)), 422
    pairs, sources = comparison_pages(document_id, ids, paths, counts)
    return jsonify(pairs=pairs, sources=sources, footers=footer_regions(paths, pairs))


@app.get("/documents/<int:document_id>/versions/<int:version_id>/preview")
@app.get("/documents/<int:document_id>/versions/<int:version_id>/preview/<int:page>.png", endpoint="preview_page")
@app.get("/documents/<int:document_id>/versions/<int:version_id>/preview/export.pdf", endpoint="preview_pdf", defaults={"export_pdf": True})
def visual_preview(document_id, version_id, page=None, export_pdf=False):
    try:
        item, path = document_preview(document_id, version_id)
        dimensions = page_dimensions(path)
        count = len(dimensions)
        if export_pdf:
            filename = f"{Path(item['filename']).stem}.pdf"
            return send_file(path, mimetype="application/pdf", download_name=filename)
        if page is not None:
            if not 1 <= page <= count:
                abort(404)
            use_pdfium = request.args.get("renderer") != "poppler"
            return Response(preview_png(path, page, prefer_pdfium=use_pdfium), mimetype="image/png")
    except PreviewError as error:
        if export_pdf:
            return render_template("error.html", message=str(error)), 422
        return jsonify(error=str(error)), 422
    return jsonify(
        pages=count,
        dimensions=dimensions,
        requires_poppler=requires_poppler(path),
        sheet_widths=[width for width, _ in dimensions] if item["extension"] in WORKBOOK_EXTENSIONS else [],
        pdf=url_for("preview_pdf", document_id=document_id, version_id=version_id),
    )


@app.get("/documents/<int:document_id>/versions/<int:version_id>/preview/<int:page>/text")
def preview_text(document_id, version_id, page):
    try:
        _, path = document_preview(document_id, version_id)
        if not 1 <= page <= len(page_dimensions(path)):
            abort(404)
        return jsonify(lines=page_text(path, page))
    except PreviewError as error:
        return jsonify(error=str(error)), 422


@app.errorhandler(SecurityError)
def untrusted_host(error):
    return "허용되지 않는 주소입니다.", error.code


@app.errorhandler(400)
@app.errorhandler(404)
def show_error(error):
    message = "문서나 페이지를 찾을 수 없습니다." if error.code == 404 else error.description
    return render_template("error.html", message=message), error.code


if __name__ == "__main__":
    initialize()
    app.run(host=HOST, port=browser_port())
