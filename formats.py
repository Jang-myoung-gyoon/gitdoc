"""File families shared by uploads, previews, and comparisons."""

ODF_MIMETYPES = {
    "odt": b"application/vnd.oasis.opendocument.text",
    "ods": b"application/vnd.oasis.opendocument.spreadsheet",
    "odp": b"application/vnd.oasis.opendocument.presentation",
}
MACRO_CONTENT_TYPES = {
    "docm": "application/vnd.ms-word.document.macroEnabled.main+xml",
    "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
    "pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
}
WORKBOOK_EXTENSIONS = ("xls", "xlsx", "xlsm")
EXTENSIONS = ("hwp", "hwpx", "doc", "docx", "pdf", "ppt", "pptx", *WORKBOOK_EXTENSIONS,
              *ODF_MIMETYPES, "docm", "pptm")
