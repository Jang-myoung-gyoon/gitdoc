# 로컬 MCP와 CLI

[처음으로](Home.md) · [설치와 업데이트](Install-and-Update.md)

## 연결

GitDoc MCP는 로컬 `mcp_server.py`를 표준 입출력으로 실행합니다. 설치본은 앱에 포함된 Python과 `mcp_server.py`를 사용합니다. 소스 실행이라면 프로젝트 가상 환경의 Python과 프로젝트의 `mcp_server.py`를 사용합니다. 앱과 동일한 `DOCVERS_DB`를 지정하고, 연결 후 `get_document_capabilities`로 작업 공간 ID와 실행 중인 기능을 확인하세요. [설치본과 소스의 실행 경로](https://github.com/Jang-myoung-gyoon/gitdoc/blob/f1c35814ff2b127b6859869ee91229da0304279d/README.ko.md#시작)

- **macOS 설치본**에서 `/Applications/GitDoc.app/Contents/Resources`의 `python/bin/python3`와 `app/mcp_server.py`를 지정합니다.
- **Windows 설치본**에서 `%LOCALAPPDATA%\Programs\GitDoc`의 `python\python.exe`와 `app\mcp_server.py`를 지정합니다.
- **소스 실행**에서는 프로젝트 가상 환경의 Python과 `mcp_server.py`를 절대 경로로 등록합니다.

| 작업 | 실제 도구 |
| --- | --- |
| 문서 찾고 이력 읽기 | `search_documents`, `list_branches`, `list_versions`, `read_version` |
| 두 버전이나 브랜치 비교 | `compare_versions`, `compare_branches` |
| 쪽 수, 비교 이미지, 변경 영역 | `get_preview_info`, `render_document_page`, `list_visual_changes` |
| PDF 메모와 시트 구조 확인 | `get_pdf_notes`, `inspect_workbook` |
| 로컬 브라우저 열기와 파일 내보내기 | `open_browser_view`, `export_document` |
| 기존 파일 등록, 빈 문서 생성 | `register_document`, `create_blank_document` (**main**) |
| 브랜치 생성, 수정안 제출·편집 | `create_document_branch`, `submit_revision`, `edit_revision` |
| 승인·반려, 병합, 이전 버전 복원 | `review_draft`, `merge_document_branches`, `restore_document_version` |
| 브랜치 이력 되돌리기 | `reset_document_branch` |

`search_documents`로 문서를 찾고 `list_branches`, `list_versions`에서 대상 브랜치와 최신 버전을 확인합니다. 도구에는 화면의 `v1.0.1` 같은 라벨 대신 숫자 ID를 전달합니다. 제출 파일 및 내보내기에는 로컬 절대 경로를 사용합니다. `edit_revision`은 지원되는 찾기·바꾸기, DOCX/PPTX 문단 블록, XLSX 셀, PDF 페이지 작업을 수정안으로 만듭니다. DOCX/PPTX 블록 ID는 `list_editable_blocks`에서 확인합니다. 데스크톱 캔버스의 개체 편집 명령은 데스크톱에서 사용하세요.

## CLI로 실행

아래 숫자는 예시 ID입니다. 설치본은 패키지의 Python으로 실행합니다.

```sh
python docvers.py list
python docvers.py open 1 --version 2 --page 1
python docvers.py open 1 --base 2 --target 3 --browser --url
python docvers.py tool
python docvers.py tool render_document_page --json '{"document_id":1,"version_id":2,"page":1}' --output preview.png
```

외부 편집기로 파일을 수정하는 CLI 흐름은 `checkout`, `status`, `diff`, `commit`입니다. [작업 폴더 예제](https://github.com/Jang-myoung-gyoon/gitdoc/blob/f1c35814ff2b127b6859869ee91229da0304279d/README.ko.md#로컬-작업-폴더) · [MCP 도구 정의](https://github.com/Jang-myoung-gyoon/gitdoc/blob/f1c35814ff2b127b6859869ee91229da0304279d/mcp_server.py)
