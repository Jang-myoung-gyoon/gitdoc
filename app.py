import hashlib
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import BadZipFile

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.exceptions import SecurityError

from extract import extract_for_version, matches_format
from preview import PreviewError, page_sizes, preview_path, preview_png
from workbook import read_workbook
from store import (
    DATABASE, UPLOAD_FIELDS, VERSION_FIELDS, append_version, branch_history, branch_list, create_document, database,
    find_branch, find_document, find_version, initialize, merge_state, move_head, new_branch,
    search_documents, text_diff,
)

MAX_FILE = 30 * 1024 * 1024
STORE_ID = hashlib.sha256(os.fsencode(DATABASE)).hexdigest()
EXTENSIONS = ("hwp", "hwpx", "docx", "pdf", "ppt", "pptx", "xls", "xlsx")
app = Flask(__name__)
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
        "default-src 'self'; style-src 'self'; form-action 'self'; "
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


def prepare_upload(name, data):
    filename = name.replace("\\", "/").split("/")[-1]
    extension = Path(filename).suffix.lower().lstrip(".")
    if len(filename) > 255 or any(ord(char) < 32 for char in filename):
        raise ValueError("파일 이름을 확인하세요.")
    if extension not in EXTENSIONS:
        raise ValueError("지원 파일 형식을 확인하세요.")
    if not data or len(data) > MAX_FILE:
        raise ValueError("파일은 1바이트 이상, 30MB 이하여야 합니다.")
    try:
        valid = matches_format(extension, data)
    except (BadZipFile, OSError, ValueError):
        valid = False
    if not valid:
        raise ValueError("파일 내용이 확장자와 일치하지 않거나 파일이 손상되었습니다.")
    extracted, note = extract_for_version(extension, data)
    return (filename, extension, len(data), hashlib.sha256(data).hexdigest(), note, extracted, data)


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


def comparison_sidebar(db, document_id, active, original, updated):
    branches = branch_list(db, document_id)
    rows = {item["id"]: item for item in branch_history(db, active)}
    for item in (original, updated):
        rows[item["id"]] = item
    return {"branches": branches, "history": sorted(rows.values(), key=lambda v: -v["number"])}


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
        active = branch(db, document_id, request.args.get("branch", "main"))
        branches = branch_list(db, document_id)
        versions = branch_history(db, active)
        current = version(db, document_id, active["head_id"])
    return render_template("detail.html", doc=doc, versions=versions, current=current,
                           active=active, branches=branches)


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
        sidebar = comparison_sidebar(db, document_id, source, a, b)
    changes = [
        (f"{target['name']} → {source['name']}", a, b),
        (f"공통 조상 → {target['name']}", ancestor, a),
        (f"공통 조상 → {source['name']}", ancestor, b),
    ]
    panels = [(title, before, after, *text_diff(before["extracted"], after["extracted"]))
              for title, before, after in changes]
    return render_template("branches.html", doc=doc, target=target, source=source,
                           ancestor=ancestor, a=a, b=b, state=state, panels=panels, sidebar=sidebar)


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
        with database(write=True) as db:
            target = branch(db, document_id, target_name)
            source = branch(db, document_id, source_name)
            if (str(target["head_id"]) != request.form.get("target_head")
                or str(source["head_id"]) != request.form.get("source_head")):
                raise ValueError("브랜치가 변경되었습니다. 최신 버전을 다시 대조하세요.")
            _, state = merge_state(db, target, source)
            if state == "merged":
                raise ValueError("이 버전은 이미 대상 브랜치 이력에 포함되어 있습니다.")
            if state == "conflict":
                if not resolution:
                    raise ValueError("양쪽 변경을 반영한 병합 파일을 올려주세요.")
                if resolution[1] != version(db, document_id, target["head_id"])["extension"]:
                    raise ValueError("대상 브랜치와 같은 파일 형식을 사용하세요.")
                data = resolution
            else:
                origin = source if state == "source" else target
                data = version(db, document_id, origin["head_id"], UPLOAD_FIELDS)
            append_version(db, target, data, reason, merge_parent_id=source["head_id"])
    except ValueError as error:
        flash(str(error), "error")
        return redirect(destination, 303)
    flash(f"{source_name} 브랜치를 {target_name}에 병합했습니다.")
    return redirect(url_for("detail", document_id=document_id, branch=target_name), 303)


@app.post("/documents/<int:document_id>/versions")
def propose(document_id):
    name = request.form.get("branch", "main")
    destination = url_for("detail", document_id=document_id, branch=name)
    try:
        reason = request.form.get("reason", "").strip()
        if not 1 <= len(reason) <= 500:
            raise ValueError("변경 사유를 1~500자로 입력하세요.")
        upload = read_upload()
        with database(write=True) as db:
            active = branch(db, document_id, name)
            if str(active["head_id"]) != request.form.get("base"):
                raise ValueError("확정본이 변경되었습니다. 새 확정본을 기준으로 다시 제출하세요.")
            current = version(db, document_id, active["head_id"])
            if current["extension"] != upload[1]:
                raise ValueError("확정본과 같은 확장자의 파일을 선택하세요.")
            if current["sha256"] == upload[3]:
                raise ValueError("확정본과 같은 파일입니다.")
            version_id = append_version(db, active, upload, reason, "pending")
    except ValueError as error:
        flash(str(error), "error")
        return redirect(destination, 303)
    flash("수정안을 검토 대기로 등록했습니다.")
    return redirect(url_for("compare", document_id=document_id, target=version_id, branch=name), 303)


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
        sidebar = comparison_sidebar(db, document_id, active, original, updated)
    changes, truncated = text_diff(original["extracted"], updated["extracted"])
    return render_template("compare.html", doc=doc, original=original, updated=updated,
                           active=active, review_branch=review_branch, changes=changes, truncated=truncated,
                           sidebar=sidebar)


@app.post("/documents/<int:document_id>/versions/<int:version_id>/review")
def review(document_id, version_id):
    action = request.form.get("action")
    note = request.form.get("note", "").strip()
    if action not in ("approve", "reject") or len(note) > 500:
        abort(400, description="승인 또는 반려와 함께 500자 이하의 의견을 입력하세요.")
    with database(write=True) as db:
        draft = version(db, document_id, version_id)
        active = branch(db, document_id, draft["branch_name"])
        error = None
        if draft["status"] != "pending":
            error = "이미 검토가 끝난 버전입니다."
        elif action == "approve" and (draft["base_id"] != active["head_id"] or
                                        str(draft["base_id"]) != request.form.get("base")):
            error = "확정본이 변경되었습니다. 새 확정본을 기준으로 다시 제출하세요."
        if error:
            flash(error, "error")
        else:
            approved = action == "approve"
            db.execute("UPDATE versions SET status = ?, review_note = ?, reviewed_at = CURRENT_TIMESTAMP WHERE id = ?",
                       ("approved" if approved else "rejected", note, version_id))
            if approved:
                move_head(db, active, version_id)
            flash("수정안을 승인했습니다." if approved else "수정안을 반려했습니다.")
    return redirect(url_for("detail", document_id=document_id, branch=active["name"]), 303)


@app.post("/documents/<int:document_id>/versions/<int:version_id>/restore")
def restore(document_id, version_id):
    reason = request.form.get("reason", "").strip()
    name = request.form.get("branch", "main")
    destination = url_for("detail", document_id=document_id, branch=name)
    if not 1 <= len(reason) <= 500:
        flash("복원 사유를 1~500자로 입력하세요.", "error")
        return redirect(destination, 303)
    with database(write=True) as db:
        active = branch(db, document_id, name)
        source = version(db, document_id, version_id)
        if source["status"] != "approved" or version_id == active["head_id"] or (
            str(active["head_id"]) != request.form.get("base")
        ):
            flash("지금은 이 버전을 복원할 수 없습니다. 현재 확정본을 확인하세요.", "error")
            return redirect(destination, 303)
        upload = version(db, document_id, version_id, UPLOAD_FIELDS)
        append_version(db, active, upload, reason, restored_from=version_id)
    flash(f"v{source['number']} 파일을 새 확정본으로 복원했습니다.")
    return redirect(destination, 303)


@app.get("/documents/<int:document_id>/versions/<int:version_id>/file")
def download(document_id, version_id):
    with database() as db:
        item = version(db, document_id, version_id, "filename, extension, content")
    pdf = item["extension"] == "pdf" and request.args.get("preview") == "1"
    return send_file(io.BytesIO(item["content"]),
                     mimetype="application/pdf" if pdf else "application/octet-stream",
                     as_attachment=not pdf, download_name=item["filename"])


@app.get("/documents/<int:document_id>/versions/<int:version_id>/workbook")
def workbook(document_id, version_id):
    with database() as db:
        item = version(db, document_id, version_id, "extension, content")
    if item["extension"] not in ("xls", "xlsx"):
        abort(404)
    try:
        sheets = read_workbook(item["extension"], item["content"])
    except ValueError as error:
        return jsonify(error=str(error)), 422
    except Exception:
        return jsonify(error="시트를 읽을 수 없습니다. PDF 페이지 대조나 원본을 확인하세요."), 422
    return jsonify(sheets=sheets)


@app.get("/documents/<int:document_id>/versions/<int:version_id>/preview")
@app.get("/documents/<int:document_id>/versions/<int:version_id>/preview/<int:page>.png", endpoint="preview_page")
@app.get("/documents/<int:document_id>/versions/<int:version_id>/preview/export.pdf", endpoint="preview_pdf", defaults={"export_pdf": True})
def visual_preview(document_id, version_id, page=None, export_pdf=False):
    with database() as db:
        item = version(db, document_id, version_id, "filename, extension, sha256")

    def original_content():
        with database() as db:
            return version(db, document_id, version_id, "content")["content"]

    try:
        path = preview_path(item["extension"], item["sha256"], original_content)
        sizes = page_sizes(path)
        count = len(sizes)
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
        sheet_widths=sizes if item["extension"] in ("xls", "xlsx") else [],
        pdf=url_for("preview_pdf", document_id=document_id, version_id=version_id),
    )


@app.errorhandler(SecurityError)
def untrusted_host(error):
    return "허용되지 않는 주소입니다.", error.code


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def show_error(error):
    messages = {403: "이 요청을 허용할 수 없습니다.", 404: "문서나 페이지를 찾을 수 없습니다.",
                413: "파일이 허용 크기인 30MB를 초과했습니다."}
    return render_template("error.html", message=messages.get(error.code, error.description)), error.code


if __name__ == "__main__":
    initialize()
    app.run(host="127.0.0.1", port=int(os.environ.get("DOCVERS_PORT", "8796")))
