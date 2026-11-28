"""Build links shared by the desktop app, CLI, and MCP tools."""

from urllib.parse import quote, urlencode

from store import database, detached_branch_document, find_document, find_version, view_branch


def document_path(document_id=None, *, base=None, target=None, version=None, branch=None,
                  page=None, change=None, sheet=None, cell=None, row_key=None, view=None):
    if document_id is None:
        return "/"
    path = f"/documents/{document_id}"
    if target is not None:
        path += "/compare"
    params = {"base": base, "target": target, "version": version, "branch": branch, "page": page, "change": change,
              "sheet": sheet, "cell": cell, "row_key": row_key, "view": view}
    query = urlencode({key: value for key, value in params.items() if value is not None}, quote_via=quote)
    return f"{path}?{query}" if query else path


def desktop_url(path):
    return "gitdoc://open" + path


def checked_document_path(document_id=None, *, base=None, target=None, version=None, branch=None,
                          page=None, change=None, sheet=None, cell=None, row_key=None, view=None):
    if document_id is None and any(value is not None for value in
                                   (branch, base, target, version, page, change, sheet, cell, row_key, view)):
        raise ValueError("위치를 열려면 문서 ID를 지정하세요.")
    if target is not None and version is not None:
        raise ValueError("특정 버전 보기와 비교 대상은 함께 지정할 수 없습니다.")
    if target is None and (base is not None or change is not None or row_key is not None):
        raise ValueError("비교 위치를 열려면 비교 대상 버전 ID를 지정하세요.")
    if any(value is not None and value < 1 for value in (page, change)):
        raise ValueError("페이지와 변경 번호는 1 이상이어야 합니다.")
    if change and not page:
        raise ValueError("변경 위치를 열려면 페이지를 지정하세요.")
    if document_id is not None:
        with database(readonly=True) as db:
            if not find_document(db, document_id):
                raise ValueError("문서를 찾을 수 없습니다.")
            opened = None
            for version_id in (base, target, version):
                if version_id is not None:
                    opened = find_version(db, document_id, version_id)
                    if opened is None:
                        raise ValueError("이 문서의 버전을 찾을 수 없습니다.")
            if branch and not view_branch(db, document_id, branch, opened):
                if detached := detached_branch_document(db, document_id, branch):
                    raise ValueError(f"분리된 브랜치입니다. 새 문서 ID: {detached['document_id']}")
                raise ValueError("브랜치를 찾을 수 없습니다.")
    return document_path(document_id, branch=branch, base=base, target=target, version=version,
                         page=page, change=change, sheet=sheet, cell=cell, row_key=row_key, view=view)
