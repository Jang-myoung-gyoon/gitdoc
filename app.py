import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from pypdf.errors import PyPdfError
from werkzeug.exceptions import SecurityError

from browser_viewer import HOST, STORE_ID, browser_port
from delimited import aligned_rows, key_columns, lines as table_lines, sheet as table_sheet, table_rows
from editing import MODES, read_pdf_notes
from extract import comparison_text
from formats import DELIMITED_EXTENSIONS, EXTENSIONS, WORKBOOK_EXTENSIONS
from page_alignment import comparison_pages
from preview import PreviewError, page_dimensions, preview_path, preview_png
from workbook import read_workbook
from uploads import MAX_FILE, MAX_FILE_MIB, prepare_upload
from workflow import MissingEntity, merge_revision, restore_revision, review_revision, save_document_edit, save_proposal
from store import (
    VERSION_FIELDS, branch_history, branch_list, create_document, database,
    find_branch, find_document, find_version, initialize, merge_state, new_branch,
    search_documents, text_diff,
)

app = Flask(__name__)
app.jinja_env.globals.update(extensions=EXTENSIONS, workbook_extensions=WORKBOOK_EXTENSIONS,
                            max_file_mib=MAX_FILE_MIB)
app.config.update(SECRET_KEY=os.urandom(32), MAX_CONTENT_LENGTH=MAX_FILE + 256 * 1024,
                  TRUSTED_HOSTS=["localhost", "127.0.0.1"], SESSION_COOKIE_SAMESITE="Strict")
@app.before_request
def restrict_cross_site_writes():
    if request.method != "POST":
        return
    origin = request.headers.get("Origin")
    if request.headers.get("Sec-Fetch-Site") == "cross-site" or (
        origin and origin != request.host_url.rstrip("/")
    ):
        abort(403)


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


def read_upload():
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        raise ValueError("파일을 선택하세요.")
    return prepare_upload(uploaded.filename, uploaded.stream.read(MAX_FILE + 1))


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
    return jsonify(store=STORE_ID)


def comparison_history(db, active, original, updated):
    rows = {item["id"]: item for item in branch_history(db, active)}
    for item in (original, updated):
        rows[item["id"]] = item
    return sorted(rows.values(), key=lambda v: -v["number"])


@app.get("/")
def index():
    query = request.args.get("q", "").strip()[:150]
    with database() as db:
        documents = search_documents(db, query)
    return render_template("index.html", documents=documents, query=query)


@app.post("/documents")
def create():
    try:
        title = request.form.get("title", "").strip()
        upload = read_upload()
        with database(write=True) as db:
            document_id = create_document(db, title, upload)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("index"), 303)
    flash("문서를 등록했습니다.")
    return redirect(url_for("detail", document_id=document_id), 303)


@app.get("/documents/<int:document_id>")
def detail(document_id):
    with database() as db:
        doc = document(db, document_id)
        open_id = request.args.get("version", type=int)
        if open_id is None and "version" in request.args:
            abort(400, description="문서 버전 ID를 확인하세요.")
        opened = version(db, document_id, open_id) if open_id is not None else None
        default_branch = opened["branch_name"] if opened else "main"
        active = branch(db, document_id, request.args.get("branch", default_branch))
        branches = branch_list(db, document_id)
        versions = branch_history(db, active)
        current = version(db, document_id, active["head_id"])
    return render_template("detail.html", doc=doc, versions=versions, current=current,
                           opened=opened or current, active=active, branches=branches,
                           edit_mode=MODES.get(current["extension"]))


@app.post("/documents/<int:document_id>/branches")
def create_branch(document_id):
    source = request.form.get("branch", "main")
    name = request.form.get("name", "").strip().lower()
    try:
        with database(write=True) as db:
            document(db, document_id)
            branch(db, document_id, source)
            new_branch(db, document_id, name, request.form.get("start", type=int))
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("detail", document_id=document_id, branch=source), 303)
    flash(f"{name} 브랜치를 만들었습니다.")
    return redirect(url_for("detail", document_id=document_id, branch=name), 303)


@app.get("/documents/<int:document_id>/branches/compare")
def compare_branches(document_id):
    with database() as db:
        doc = document(db, document_id)
        target = branch(db, document_id, request.args.get("target", "main"))
        source = branch(db, document_id, request.args.get("source"))
        common, state = merge_state(db, target, source)
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
                           ancestor=ancestor, a=a, b=b, state=state, panels=panels,
                           current=a, branches=branches, versions=versions)


@app.post("/documents/<int:document_id>/branches/merge")
def merge_branches(document_id):
    target_name = request.form.get("target", "main")
    source_name = request.form.get("source")
    destination = url_for("compare_branches", document_id=document_id,
                          target=target_name, source=source_name)
    try:
        reason = request.form.get("reason", "").strip()
        if not 1 <= len(reason) <= 500:
            raise ValueError("병합 사유를 1~500자로 입력하세요.")
        uploaded = request.files.get("file")
        resolution = read_upload() if uploaded and uploaded.filename else None
        merge_revision(document_id, target_name, source_name, request.form.get("target_head", type=int),
                       request.form.get("source_head", type=int), reason, resolution)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(destination, 303)
    flash(f"{source_name} 브랜치를 {target_name}에 병합했습니다.")
    return redirect(url_for("detail", document_id=document_id, branch=target_name), 303)


def review_draft(document_id, name, version_id):
    flash("수정안을 검토 대기로 등록했습니다.")
    return redirect(url_for("compare", document_id=document_id, target=version_id, branch=name), 303)


@app.route("/documents/<int:document_id>/edit", methods=("GET", "POST"))
def edit_document(document_id):
    name = request.args.get("branch", "main")
    submitted = request.method == "POST"
    with database() as db:
        doc = document(db, document_id)
        active = branch(db, document_id, name)
        base = request.form.get("base", type=int) if submitted else active["head_id"]
        item = version(db, document_id, base)
        current = item if base == active["head_id"] else version(db, document_id, active["head_id"])
        branches = branch_list(db, document_id)
        versions = branch_history(db, active)
    mode = MODES.get(item["extension"])
    if not mode:
        abort(400, description="이 형식의 내장 편집을 지원하지 않습니다.")
    params = request.form if submitted else request.args
    context = dict(doc=doc, active=active, item=item, mode=mode, fields=params,
                   current=current, branches=branches, versions=versions)
    if not submitted:
        return render_template("edit.html", **context)
    if base != active["head_id"]:
        return render_template("edit.html", **context,
                               error="확정본이 변경되었습니다. 최신 확정본에서 다시 편집하세요."), 409
    try:
        reason = request.form.get("reason", "").strip()
        if not 1 <= len(reason) <= 500:
            raise ValueError("변경 사유를 1~500자로 입력하세요.")
        uploaded = request.files.get("file")
        source = read_upload() if uploaded and uploaded.filename else None
        version_id = save_document_edit(document_id, name, base, request.form, reason, source=source)
    except ValueError as error:
        return render_template("edit.html", **context, error=str(error)), 422
    return review_draft(document_id, name, version_id)


@app.post("/documents/<int:document_id>/versions")
def propose(document_id):
    name = request.form.get("branch", "main")
    destination = url_for("detail", document_id=document_id, branch=name)
    try:
        reason = request.form.get("reason", "").strip()
        if not 1 <= len(reason) <= 500:
            raise ValueError("변경 사유를 1~500자로 입력하세요.")
        upload = read_upload()
        version_id = save_proposal(document_id, name, request.form.get("base", type=int), upload, reason)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(destination, 303)
    return review_draft(document_id, name, version_id)


@app.get("/documents/<int:document_id>/compare")
def compare(document_id):
    with database() as db:
        doc = document(db, document_id)
        target = request.args.get("target", type=int)
        if target is None:
            abort(400, description="비교할 버전을 선택하세요.")
        updated = version(db, document_id, target, VERSION_FIELDS + ", extracted")
        active = branch(db, document_id, request.args.get("branch", updated["branch_name"]))
        review_branch = branch(db, document_id, updated["branch_name"])
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
    return render_template("compare.html", doc=doc, original=original, updated=updated,
                           active=active, review_branch=review_branch, changes=changes, truncated=truncated,
                           current=current, branches=branches, versions=versions)


@app.post("/documents/<int:document_id>/versions/<int:version_id>/review")
def review(document_id, version_id):
    action = request.form.get("action")
    note = request.form.get("note", "").strip()
    if action not in ("approve", "reject") or len(note) > 500:
        abort(400, description="승인 또는 반려와 함께 500자 이하의 의견을 입력하세요.")
    with database() as db:
        draft = version(db, document_id, version_id)
    try:
        review_revision(document_id, version_id, action, request.form.get("base", type=int), note)
        flash("수정안을 승인했습니다." if action == "approve" else "수정안을 반려했습니다.")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("detail", document_id=document_id, branch=draft["branch_name"]), 303)


@app.post("/documents/<int:document_id>/versions/<int:version_id>/restore")
def restore(document_id, version_id):
    reason = request.form.get("reason", "").strip()
    name = request.form.get("branch", "main")
    destination = url_for("detail", document_id=document_id, branch=name)
    try:
        restore_revision(document_id, name, version_id, request.form.get("base", type=int), reason)
        with database() as db:
            source = version(db, document_id, version_id)
        flash(f"{source['version_label']} 파일을 새 확정본으로 복원했습니다.")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(destination, 303)


@app.get("/documents/<int:document_id>/versions/<int:version_id>/file")
def download(document_id, version_id):
    with database() as db:
        item = version(db, document_id, version_id, "filename, extension, content")
    pdf = item["extension"] == "pdf" and request.args.get("preview") == "1"
    return send_file(io.BytesIO(item["content"]),
                     mimetype="application/pdf" if pdf else "application/octet-stream",
                     as_attachment=not pdf, download_name=item["filename"])


@app.get("/documents/<int:document_id>/versions/<int:version_id>/notes/<int:page>")
def pdf_notes(document_id, version_id, page):
    with database() as db:
        item = version(db, document_id, version_id, "extension, content")
    if item["extension"] != "pdf":
        abort(404)
    try:
        return jsonify(notes=read_pdf_notes(item["content"], page))
    except (PyPdfError, ValueError, OSError, RecursionError):
        return jsonify(notes=[])


def aligned_tables(db, document_id, base, target, row_key):
    records = [version(db, document_id, item, "extension, content") for item in (base, target)]
    if any(item["extension"] not in DELIMITED_EXTENSIONS for item in records):
        raise ValueError("행 기준 열은 CSV/TSV에서만 사용할 수 있습니다.")
    tables = [table_rows(item["extension"], item["content"]) for item in records]
    column = key_columns(*tables).get(row_key)
    if column is None:
        raise ValueError("두 파일에서 중복과 빈 값이 없는 공통 열 제목을 선택하세요.")
    return aligned_rows(*tables, column)


@app.get("/documents/<int:document_id>/versions/<int:version_id>/workbook")
def workbook(document_id, version_id):
    row_key = request.args.get("row_key")
    if row_key is not None:
        base, target = (request.args.get(name, type=int) for name in ("base", "target"))
        if base is None or target is None or version_id not in (base, target):
            abort(400, description="두 비교 버전을 지정하세요.")
        try:
            with database() as db:
                tables = aligned_tables(db, document_id, base, target, row_key)
        except ValueError as error:
            return jsonify(error=str(error)), 422
        return jsonify(sheets=[table_sheet(tables[0 if version_id == base else 1])])
    with database() as db:
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
    with database() as db:
        item = version(db, document_id, version_id, "filename, extension, sha256")

    def original_content():
        with database() as db:
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
    return jsonify(pairs=pairs, sources=sources)


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
            return Response(preview_png(path, page), mimetype="image/png")
    except PreviewError as error:
        if export_pdf:
            return render_template("error.html", message=str(error)), 422
        return jsonify(error=str(error)), 422
    return jsonify(
        pages=count,
        dimensions=dimensions,
        sheet_widths=[width for width, _ in dimensions] if item["extension"] in WORKBOOK_EXTENSIONS else [],
        pdf=url_for("preview_pdf", document_id=document_id, version_id=version_id),
    )


@app.errorhandler(MissingEntity)
def missing_entity(error):
    return render_template("error.html", message=str(error)), 404


@app.errorhandler(SecurityError)
def untrusted_host(error):
    return "허용되지 않는 주소입니다.", error.code


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def show_error(error):
    messages = {403: "이 요청을 허용할 수 없습니다.", 404: "문서나 페이지를 찾을 수 없습니다.",
                413: f"파일이 허용 크기인 {MAX_FILE_MIB}MiB를 초과했습니다."}
    return render_template("error.html", message=messages.get(error.code, error.description)), error.code


if __name__ == "__main__":
    initialize()
    app.run(host=HOST, port=browser_port())
