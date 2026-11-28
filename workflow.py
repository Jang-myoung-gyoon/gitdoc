"""Write operations shared by the desktop UI and the MCP tools."""

from editing import MODES, edit_pdf, modify_document
from store import (
    ANCESTORS, UPLOAD_FIELDS, VERSION_FIELDS, append_version, database, desktop_review_enabled, find_branch, find_version, first_parent_ids,
    detached_branch_document, merge_state, move_head, new_branch, split_origin,
)
from uploads import MAX_FILE, prepare_upload
from versioning import next_label


class MissingEntity(LookupError):
    pass


def require(row, kind):
    if row is None:
        raise MissingEntity(f"{kind}을 찾을 수 없습니다.")
    return row


def reason_for(value, kind="변경"):
    reason = value.strip()
    if not 1 <= len(reason) <= 500:
        raise ValueError(f"{kind} 사유를 1~500자로 입력하세요.")
    return reason


def save_proposal(document_id, name, base, upload, reason, *, page_edit=None, desktop=False):
    reason = reason_for(reason)
    with database(write=True) as db:
        active = require(find_branch(db, document_id, name), "브랜치")
        if active["head_id"] != base:
            raise ValueError("확정본이 변경되었습니다. 새 확정본을 기준으로 다시 제출하세요.")
        current = require(find_version(db, document_id, base), "버전")
        if current["extension"] != upload[1]:
            raise ValueError("확정본과 같은 확장자의 파일을 선택하세요.")
        if current["sha256"] == upload[3]:
            raise ValueError("확정본과 같은 파일입니다.")
        status = "approved" if desktop and not desktop_review_enabled(db) else "pending"
        return append_version(db, active, upload, reason, status, page_edit=page_edit)


def save_document_edit(document_id, name, base, fields, reason, expected_mode=None, *, source=None, desktop=False):
    reason = reason_for(reason.strip() or "문서 편집")
    with database() as db:
        active = require(find_branch(db, document_id, name), "브랜치")
        if active["head_id"] != base:
            raise ValueError("확정본이 변경되었습니다. 새 확정본에서 다시 편집하세요.")
        item = require(find_version(db, document_id, base, VERSION_FIELDS + ", content"), "버전")
    if expected_mode is not None and MODES.get(item["extension"]) != expected_mode:
        raise ValueError("편집 작업이 이 문서 형식과 맞지 않습니다.")
    if source is not None and (item["extension"] != "pdf" or fields.get("operation") != "insert" or source[1] != "pdf"):
        raise ValueError("페이지 삽입 작업에서 PDF 파일을 선택하세요.")
    page_edit = None
    if item["extension"] == "pdf":
        updated, page_edit = edit_pdf(item["content"], fields, source[-1] if source else None)
    else:
        updated = modify_document(item["extension"], item["content"], fields, MAX_FILE)
    return save_proposal(document_id, name, base, prepare_upload(item["filename"], updated), reason,
                         page_edit=page_edit, desktop=desktop)


def review_revision(document_id, version_id, action, base, note="", *, desktop=False):
    note = note.strip()
    if action not in ("approve", "reject") or len(note) > 500:
        raise ValueError("승인 또는 반려와 함께 500자 이하의 의견을 입력하세요.")
    with database(write=True) as db:
        if desktop and not desktop_review_enabled(db):
            raise ValueError("설정에서 수정안 검토를 켜세요.")
        draft = require(find_version(db, document_id, version_id), "버전")
        active = require(find_branch(db, document_id, draft["branch_name"]), "브랜치")
        if draft["status"] != "pending":
            raise ValueError("이미 검토가 끝난 버전입니다.")
        approved = action == "approve"
        if approved and (draft["base_id"] != active["head_id"] or draft["base_id"] != base):
            raise ValueError("확정본이 변경되었습니다. 새 확정본을 기준으로 다시 제출하세요.")
        db.execute("""UPDATE versions SET status = ?, review_note = ?, reviewed_at = CURRENT_TIMESTAMP
                      WHERE id = ?""", ("approved" if approved else "rejected", note, version_id))
        if approved:
            move_head(db, active, version_id)


def merge_revision(document_id, target_name, source_name, target_head, source_head, reason, resolution=None):
    reason = reason_for(reason, "병합")
    with database(write=True) as db:
        target = require(find_branch(db, document_id, target_name), "대상 브랜치")
        source = require(find_branch(db, document_id, source_name), "가져올 브랜치")
        if target["head_id"] != target_head or source["head_id"] != source_head:
            raise ValueError("브랜치가 변경되었습니다. 최신 버전을 다시 대조하세요.")
        _, state = merge_state(db, target, source)
        if state == "merged":
            raise ValueError("이 버전은 이미 대상 브랜치 이력에 포함되어 있습니다.")
        if state == "conflict":
            if not resolution:
                raise ValueError("양쪽 변경을 반영한 병합 파일을 올려주세요.")
            if resolution[1] != find_version(db, document_id, target_head)["extension"]:
                raise ValueError("대상 브랜치와 같은 파일 형식을 사용하세요.")
            data = resolution
        else:
            origin = source if state == "source" else target
            data = find_version(db, document_id, origin["head_id"], UPLOAD_FIELDS)
        return append_version(db, target, data, reason, merge_parent_id=source_head)


def restore_revision(document_id, name, version_id, base, reason):
    reason = reason_for(reason, "복원")
    with database(write=True) as db:
        active = require(find_branch(db, document_id, name), "브랜치")
        source = require(find_version(db, document_id, version_id), "복원할 버전")
        if source["status"] != "approved" or version_id == active["head_id"] or active["head_id"] != base:
            raise ValueError("지금은 이 버전을 복원할 수 없습니다. 현재 확정본을 확인하세요.")
        upload = find_version(db, document_id, version_id, UPLOAD_FIELDS)
        return append_version(db, active, upload, reason, restored_from=version_id)


def reset_branch(document_id, name, version_id, expected_head):
    """Move the active branch back and keep its previous history in a recovery branch."""
    with database(write=True) as db:
        active = require(find_branch(db, document_id, name), "브랜치")
        if active["head_id"] != expected_head:
            raise ValueError("브랜치 확정본이 변경되었습니다. 새로 고침한 뒤 다시 시도하세요.")
        chain = first_parent_ids(db, expected_head)
        if version_id not in chain[1:]:
            raise ValueError("현재 브랜치의 이전 확정 버전을 선택하세요.")
        prefix = f"recovery-{expected_head}"
        backup = prefix
        number = 2
        while find_branch(db, document_id, backup):
            backup = f"{prefix}-{number}"
            number += 1
        new_branch(db, document_id, backup, expected_head)
        recovery = find_branch(db, document_id, backup)
        db.execute(ANCESTORS + """
            UPDATE versions SET branch_id = ? WHERE branch_id = ? AND status != 'approved'
            AND base_id NOT IN (SELECT id FROM ancestors)
        """, (version_id, recovery["id"], active["id"]))
        move_head(db, active, version_id)
        return {"head_id": version_id, "recovery_branch": backup,
                "removed_commits": chain.index(version_id)}


def split_branch(document_id, name, branch_id, expected_head, title, *, confirmed=False):
    """Copy a branch to an independent document and archive the original branch reference."""
    if confirmed is not True:
        raise ValueError("원본 문서에서 브랜치가 제거되는 데 동의해야 합니다.")
    title = title.strip()
    if not 1 <= len(title) <= 120:
        raise ValueError("문서 제목을 1~120자로 입력하세요.")
    if name.lower() == "main":
        raise ValueError("기본 브랜치 main은 분리할 수 없습니다.")

    with database(write=True) as db:
        branch = find_branch(db, document_id, name)
        if branch is None and (split := detached_branch_document(db, document_id, name)):
            raise ValueError(f"이미 분리된 브랜치입니다. 새 문서 ID: {split['document_id']}")
        branch = require(branch, "브랜치")
        if branch_id != branch["id"] or expected_head != branch["head_id"]:
            raise ValueError("브랜치가 변경되었습니다. 새로 고침한 뒤 다시 시도하세요.")
        if not find_branch(db, document_id, "main"):
            raise ValueError("원본의 기본 브랜치를 확인할 수 없어 분리할 수 없습니다.")
        history, snapshot_id = split_history(db, branch)
        new_document = db.execute("INSERT INTO documents (title) VALUES (?)", (title,)).lastrowid
        main = db.execute("INSERT INTO branches (document_id, name) VALUES (?, 'main')",
                          (new_document,)).lastrowid
        copies = {}
        label = None
        for number, row in enumerate(history, 1):
            initial = row["id"] == snapshot_id
            scale = "initial" if initial else row["change_scale"]
            basis = "initial" if initial else row["change_basis"]
            label = next_label(label, scale)
            copies[row["id"]] = db.execute("""
                INSERT INTO versions (document_id, branch_id, number, version_label, change_scale,
                    change_basis, base_id, merge_parent_id, restored_from, reason, status, created_at,
                    review_note, reviewed_at, filename, extension, size, sha256, extraction_note, extracted,
                    content, page_base_count, page_insert_before, page_insert_count, page_delete_at)
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, status, created_at, review_note, reviewed_at,
                    filename, extension, size, sha256, extraction_note, extracted, content,
                    CASE WHEN ? THEN NULL ELSE page_base_count END,
                    CASE WHEN ? THEN NULL ELSE page_insert_before END,
                    CASE WHEN ? THEN NULL ELSE page_insert_count END,
                    CASE WHEN ? THEN NULL ELSE page_delete_at END
                FROM versions WHERE id = ?
            """, (new_document, main, number, label, scale, basis,
                  None if initial else copies[row["base_id"]],
                  None if initial else copies.get(row["merge_parent_id"]),
                  None if initial else copies.get(row["restored_from"]),
                  "브랜치 분리 시작 버전" if initial else row["reason"],
                  initial, initial, initial, initial, row["id"])).lastrowid
        head = copies[expected_head]
        move_head(db, {"id": main, "name": "main", "document_id": new_document}, head)
        db.execute("""INSERT INTO branch_splits (document_id, source_branch_id, source_name, source_head_id)
                      VALUES (?, ?, ?, ?)""", (new_document, branch["id"], branch["name"], expected_head))
        # Archived versions keep their original owner and ID. The internal name releases the live name.
        db.execute("UPDATE branches SET name = ? WHERE id = ?", (f"~split-{branch['id']}", branch["id"]))
        origin = dict(split_origin(db, new_document))
        return {"document_id": new_document, "head_id": head, "version_count": len(copies),
                "origin": origin}


def split_history(db, branch):
    identifier = branch["id"]
    document_id = branch["document_id"]
    rows = db.execute("""
        SELECT id, branch_id, base_id, merge_parent_id, restored_from, number, status,
               change_scale, change_basis, reason FROM versions WHERE document_id = ?
    """, (document_id,)).fetchall()
    versions = {row["id"]: row for row in rows}
    own = {row["id"] for row in rows if row["branch_id"] == identifier}
    if any(versions[item]["status"] == "pending" for item in own):
        raise ValueError("검토 대기 중인 수정안을 먼저 승인하거나 반려하세요.")
    if db.execute("""SELECT 1 FROM branches b JOIN versions v ON v.id = b.head_id
                     WHERE b.document_id = ? AND b.id != ? AND v.branch_id = ?
                       AND NOT EXISTS (SELECT 1 FROM branch_splits WHERE source_branch_id = b.id)""",
                  (document_id, identifier, identifier)).fetchone() or any(
                      row["id"] not in own and any(row[field] in own
                                                   for field in ("base_id", "merge_parent_id", "restored_from"))
                      for row in rows):
        raise ValueError("다른 브랜치에서 이 브랜치의 버전을 사용하고 있어 분리할 수 없습니다.")

    history = []
    seen = set()
    cursor = branch["head_id"]
    while cursor in versions and cursor not in seen:
        row = versions[cursor]
        if row["status"] != "approved":
            break
        history.append(row)
        seen.add(cursor)
        if row["branch_id"] != identifier or row["base_id"] is None:
            break
        cursor = row["base_id"]
    if not history or (history[-1]["branch_id"] == identifier and history[-1]["base_id"] is not None):
        raise ValueError("이 브랜치의 분기점을 확인할 수 없습니다.")
    snapshot_id = history[-1]["id"]
    for item in own - seen:
        row = versions[item]
        if row["status"] != "rejected" or row["base_id"] not in seen:
            raise ValueError("현재 이력에 연결되지 않은 버전이 있어 분리할 수 없습니다.")
        history.append(row)
    for row in history:
        if row["id"] == snapshot_id:
            continue
        for field in ("base_id", "merge_parent_id", "restored_from"):
            parent = row[field]
            if parent is not None and (parent not in seen or versions[parent]["number"] >= row["number"]):
                raise ValueError("다른 브랜치의 병합 또는 복원 기록을 포함하여 분리할 수 없습니다.")
    return sorted(history, key=lambda row: row["number"]), snapshot_id
