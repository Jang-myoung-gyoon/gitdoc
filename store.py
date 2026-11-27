import difflib
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from versioning import classify_change, next_label

ROOT = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("DOCVERS_DB", str(ROOT / "data" / "documents.sqlite3"))).resolve()
VERSION_FIELDS = """
    id, document_id, number, version_label, change_scale, change_basis, filename, extension, size, sha256, reason, status,
    base_id, restored_from, created_at, review_note, reviewed_at, extraction_note,
    branch_id, merge_parent_id, page_base_count, page_insert_before, page_insert_count, page_delete_at,
    (SELECT name FROM branches WHERE branches.id = versions.branch_id) AS branch_name,
    (SELECT number FROM versions AS source WHERE source.id = versions.restored_from) AS restored_number,
    (SELECT version_label FROM versions AS source WHERE source.id = versions.restored_from) AS restored_version_label
"""
UPLOAD_FIELDS = "filename, extension, size, sha256, extraction_note, extracted, content"


@contextmanager
def database(write=False, readonly=False):
    address = DATABASE.resolve().as_uri() + "?mode=ro" if readonly else str(DATABASE)
    db = sqlite3.connect(address, uri=readonly, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
    finally:
        db.close()


def initialize():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DATABASE), timeout=15)
    try:
        db.execute("PRAGMA journal_mode = WAL")
    finally:
        db.close()
    with database(write=True) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, current_id INTEGER REFERENCES versions(id)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS versions (
                id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
                number INTEGER NOT NULL, filename TEXT NOT NULL, extension TEXT NOT NULL,
                size INTEGER NOT NULL, sha256 TEXT NOT NULL, reason TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('approved', 'pending', 'rejected')),
                base_id INTEGER REFERENCES versions(id), restored_from INTEGER REFERENCES versions(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, review_note TEXT,
                reviewed_at TEXT, extraction_note TEXT NOT NULL, extracted TEXT NOT NULL,
                content BLOB NOT NULL, UNIQUE(document_id, number)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS branches (
                id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
                name TEXT NOT NULL COLLATE NOCASE, head_id INTEGER REFERENCES versions(id),
                UNIQUE (document_id, name)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS workspace_settings (
                key TEXT PRIMARY KEY, value INTEGER NOT NULL
            )
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(versions)")}
        if "branch_id" not in columns:
            db.execute("ALTER TABLE versions ADD COLUMN branch_id INTEGER REFERENCES branches(id)")
            db.execute("ALTER TABLE versions ADD COLUMN merge_parent_id INTEGER REFERENCES versions(id)")
            db.execute("INSERT INTO branches (document_id, name, head_id) SELECT id, 'main', current_id FROM documents")
            db.execute("UPDATE versions SET branch_id = (SELECT id FROM branches WHERE document_id = versions.document_id)")
        if "version_label" not in columns:
            for field in ("version_label", "change_scale", "change_basis"):
                db.execute(f"ALTER TABLE versions ADD COLUMN {field} TEXT")
            label_existing_versions(db)
        for field in ("page_base_count", "page_insert_before", "page_insert_count", "page_delete_at"):
            if field not in columns:
                db.execute(f"ALTER TABLE versions ADD COLUMN {field} INTEGER")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS version_label_unique ON versions(document_id, version_label)")
        db.execute("CREATE INDEX IF NOT EXISTS version_branch ON versions(branch_id, status)")


def desktop_review_enabled(db):
    row = db.execute("SELECT value FROM workspace_settings WHERE key = 'desktop_review'").fetchone()
    return bool(row and row["value"])


def set_desktop_review(enabled):
    if type(enabled) is not bool:
        raise ValueError("검토 설정 값을 확인하세요.")
    with database(write=True) as db:
        db.execute("""INSERT INTO workspace_settings(key, value) VALUES ('desktop_review', ?)
                      ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (enabled,))
    return enabled


def label_existing_versions(db):
    rows = db.execute("""
        SELECT v.id, v.document_id, v.extension, v.extracted, v.sha256,
               parent.extracted AS parent_text, parent.sha256 AS parent_hash
        FROM versions v LEFT JOIN versions parent ON v.base_id = parent.id
        ORDER BY v.document_id, v.number
    """)
    document_id, previous = None, None
    for row in rows:
        if row["document_id"] != document_id:
            document_id, previous = row["document_id"], None
        scale, basis = ("initial", "initial") if previous is None else classify_change(
            row["extension"], row["parent_text"], row["extracted"], identical=row["parent_hash"] == row["sha256"])
        previous = next_label(previous, scale)
        db.execute("UPDATE versions SET version_label = ?, change_scale = ?, change_basis = ? WHERE id = ?",
                   (previous, scale, basis, row["id"]))


def create_document(db, title, upload):
    if not 1 <= len(title) <= 120:
        raise ValueError("문서 제목을 1~120자로 입력하세요.")
    document_id = db.execute("INSERT INTO documents (title) VALUES (?)", (title,)).lastrowid
    db.execute("INSERT INTO branches (document_id, name) VALUES (?, 'main')", (document_id,))
    append_version(db, find_branch(db, document_id, "main"), upload, "최초 등록")
    return document_id


def search_documents(db, query, limit=-1, offset=0):
    return db.execute("""
        SELECT d.*, v.filename, v.number, v.version_label, v.extension, v.created_at,
            (SELECT COUNT(*) FROM versions p WHERE p.document_id = d.id AND p.status = 'pending') pending
        FROM documents d JOIN versions v ON v.id = d.current_id
        WHERE instr(lower(d.title), lower(?)) OR instr(lower(v.filename), lower(?))
        ORDER BY v.id DESC LIMIT ? OFFSET ?
    """, (query, query, limit, offset)).fetchall()


def find_document(db, document_id):
    return db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()


def find_version(db, document_id, version_id, fields=VERSION_FIELDS):
    return db.execute(f"SELECT {fields} FROM versions WHERE document_id = ? AND id = ?",
                      (document_id, version_id)).fetchone()


def find_branch(db, document_id, name):
    return db.execute("""
        SELECT b.*, v.version_label FROM branches b LEFT JOIN versions v ON v.id = b.head_id
        WHERE b.document_id = ? AND b.name = ?
    """,
                      (document_id, name)).fetchone()


def branch_list(db, document_id):
    return db.execute("""
        SELECT b.*, v.number, v.version_label, (SELECT COUNT(*) FROM versions p
                               WHERE p.branch_id = b.id AND p.status = 'pending') AS pending
        FROM branches b JOIN versions v ON v.id = b.head_id WHERE b.document_id = ?
        ORDER BY b.name != 'main', b.name
    """, (document_id,)).fetchall()


def new_branch(db, document_id, name, start_id):
    if not (1 <= len(name) <= 50 and name[0].isalnum()
            and all(char.isalnum() or char in "-_." for char in name)):
        raise ValueError("브랜치 이름은 글자나 숫자로 시작하는 1~50자여야 합니다. -, _, .도 사용할 수 있습니다.")
    if find_branch(db, document_id, name):
        raise ValueError("이미 사용 중인 브랜치 이름입니다.")
    start = find_version(db, document_id, start_id)
    if start is None or start["status"] != "approved":
        raise ValueError("확정된 시작 버전을 선택하세요.")
    db.execute("INSERT INTO branches (document_id, name, head_id) VALUES (?, ?, ?)",
               (document_id, name, start_id))


ANCESTORS = """
    WITH RECURSIVE ancestors(id) AS (
        SELECT ?
        UNION SELECT v.base_id FROM versions v JOIN ancestors a ON a.id = v.id WHERE v.base_id IS NOT NULL
        UNION SELECT v.merge_parent_id FROM versions v JOIN ancestors a ON a.id = v.id WHERE v.merge_parent_id IS NOT NULL
    )
"""


def branch_history(db, branch, limit=-1, offset=0):
    return db.execute(ANCESTORS + f"""
        SELECT {VERSION_FIELDS} FROM versions
        WHERE id IN (SELECT id FROM ancestors) OR (branch_id = ? AND status != 'approved')
        ORDER BY number DESC LIMIT ? OFFSET ?
    """, (branch["head_id"], branch["id"], limit, offset)).fetchall()


def first_parent_ids(db, head):
    return [row[0] for row in db.execute("""
        WITH RECURSIVE chain(id) AS (
            SELECT ? UNION ALL
            SELECT v.base_id FROM versions v JOIN chain c ON c.id = v.id WHERE v.base_id IS NOT NULL
        )
        SELECT id FROM chain
    """, (head,))]


def move_head(db, branch, version_id):
    db.execute("UPDATE branches SET head_id = ? WHERE id = ?", (version_id, branch["id"]))
    if branch["name"] == "main":
        db.execute("UPDATE documents SET current_id = ? WHERE id = ?", (version_id, branch["document_id"]))


def append_version(db, branch, upload, reason, status="approved", restored_from=None, merge_parent_id=None, *,
                   page_edit=None):
    document_id = branch["document_id"]
    last = db.execute("SELECT number, version_label FROM versions WHERE document_id = ? ORDER BY number DESC LIMIT 1",
                      (document_id,)).fetchone()
    number = last["number"] + 1 if last else 1
    parent = find_version(db, document_id, branch["head_id"], "sha256, extracted")
    if last:
        scale, basis = classify_change(upload[1], parent["extracted"] if parent else None, upload[5],
                                      identical=bool(parent and parent["sha256"] == upload[3]))
    else:
        scale, basis = "initial", "initial"
    label = next_label(last["version_label"] if last else None, scale)
    cursor = db.execute("""
        INSERT INTO versions (document_id, number, version_label, change_scale, change_basis,
            reason, status, base_id, restored_from, branch_id, merge_parent_id, filename, extension,
            size, sha256, extraction_note, extracted, content, page_base_count, page_insert_before, page_insert_count,
            page_delete_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (document_id, number, label, scale, basis, reason, status, branch["head_id"], restored_from,
          branch["id"], merge_parent_id, *upload, *(page_edit or (None, None, None, None))))
    if status == "approved":
        move_head(db, branch, cursor.lastrowid)
    return cursor.lastrowid


def merge_state(db, target, source):
    doc_id = target["document_id"]
    left = find_version(db, doc_id, target["head_id"])
    right = find_version(db, doc_id, source["head_id"])
    left_ids = {row[0] for row in db.execute(ANCESTORS + "SELECT id FROM ancestors", (left["id"],))}
    right_ids = {row[0] for row in db.execute(ANCESTORS + "SELECT id FROM ancestors", (right["id"],))}
    shared = left_ids & right_ids
    parents = db.execute("SELECT id, base_id, merge_parent_id FROM versions WHERE document_id = ?", (doc_id,))
    ancestors = {parent for row in parents if row["id"] in shared
                 for parent in (row["base_id"], row["merge_parent_id"]) if parent in shared}
    candidates = shared - ancestors
    common = find_version(db, doc_id, max(candidates))
    if right["id"] in left_ids:
        return common, "merged"
    if left["id"] in right_ids:
        return common, "source"
    if right["sha256"] == left["sha256"]:
        return common, "target"
    if len(candidates) == 1:
        if left["sha256"] == common["sha256"]:
            return common, "source"
        if right["sha256"] == common["sha256"]:
            return common, "target"
    return common, "conflict"


def text_diff(original, updated, limit=600, max_chars=120000):
    if not original or not updated:
        return [], False
    rows = difflib.unified_diff(original.splitlines(), updated.splitlines(), n=2, lineterm="")
    next(rows, None)
    next(rows, None)
    lines = []
    remaining = max_chars
    for line in rows:
        if len(lines) == limit or len(line) > remaining:
            return lines, True
        lines.append(line)
        remaining -= len(line)
    return lines, False
