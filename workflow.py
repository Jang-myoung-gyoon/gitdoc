"""Write operations shared by the desktop UI and the MCP tools."""

from editing import MODES, edit_pdf, modify_document
from store import (
    ANCESTORS, UPLOAD_FIELDS, VERSION_FIELDS, append_version, database, find_branch, find_version, first_parent_ids,
    merge_state, move_head, new_branch,
)
from uploads import MAX_FILE, prepare_upload


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


def save_proposal(document_id, name, base, upload, reason, *, page_edit=None):
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
        return append_version(db, active, upload, reason, "pending", page_edit=page_edit)


def save_document_edit(document_id, name, base, fields, reason, expected_mode=None, *, source=None):
    reason = reason_for(reason)
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
    return save_proposal(document_id, name, base, prepare_upload(item["filename"], updated), reason, page_edit=page_edit)


def review_revision(document_id, version_id, action, base, note=""):
    note = note.strip()
    if action not in ("approve", "reject") or len(note) > 500:
        raise ValueError("승인 또는 반려와 함께 500자 이하의 의견을 입력하세요.")
    with database(write=True) as db:
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
