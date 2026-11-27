"""Build links shared by the CLI and read-only MCP tools."""

from urllib.parse import quote, urlencode


def document_path(document_id=None, *, base=None, target=None, branch=None,
                  page=None, change=None, sheet=None, cell=None):
    if document_id is None:
        return "/"
    path = f"/documents/{document_id}"
    if target is not None:
        path += "/compare"
    params = {"base": base, "target": target, "branch": branch, "page": page, "change": change,
              "sheet": sheet, "cell": cell}
    query = urlencode({key: value for key, value in params.items() if value is not None}, quote_via=quote)
    return f"{path}?{query}" if query else path


def desktop_url(path):
    return "gitdoc://open" + path
