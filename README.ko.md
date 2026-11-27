# GitDoc · 로컬 문서 버전 관리

[English](README.md) · 한국어

한글, Word, PDF, PowerPoint, Excel을 GitDoc에서 열고, 지원하는 항목을 직접 편집하고, 버전을 관리합니다. 두 문서를 나란히 열어 변경 위치를 확인하고, 수정안을 검토하거나 별도 브랜치에서 병합할 수 있습니다. [기획서와 화면 시안](기획서.md)

## 시작

GitDoc은 Rust의 [eframe](https://github.com/emilk/egui/tree/main/crates/eframe)으로 macOS와 Windows에서 네이티브 창을 엽니다. Python 문서 엔진을 자식 프로세스로 시작해 표준 입출력으로 연결합니다. 데스크톱 앱과 MCP가 이 엔진과 SQLite 저장소를 공유합니다.

Python 3.12와 최신 안정 Rust/Cargo가 필요합니다. macOS는 12 이상 및 Xcode Command Line Tools, Windows는 MSVC Rust 도구 체인 및 Visual Studio Build Tools의 C++ 데스크톱 개발 도구가 필요합니다.

**macOS**

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-mcp.txt -r requirements-desktop.txt
.venv/bin/python docvers.py open
```

**Windows PowerShell**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-mcp.txt -r requirements-desktop.txt
.\.venv\Scripts\python.exe docvers.py open
```

`docvers.py open`은 실행 파일이 없으면 Cargo로 빌드합니다. 소스를 갱신한 후에는 `cargo build --locked --manifest-path desktop/native/Cargo.toml`로 다시 빌드합니다. macOS에서 Finder로 실행하려면 `./desktop/build.sh`로 `.build/GitDoc.app`을 빌드합니다. 별도 브라우저 UI는 같은 환경에서 `python app.py`로 시작한 후 [로컬 문서함](http://127.0.0.1:8796/)에서 사용할 수 있습니다.

기본 저장소는 `data/documents.sqlite3`입니다. `DOCVERS_DB`로 경로를 바꾸고, 앱과 MCP에 같은 값을 지정합니다. `DOCVERS_PORT`는 별도 브라우저 UI의 포트를 지정합니다.

## 버전 관리와 비교

1. 문서함에서 원본을 올립니다. 최초 확정본이 `main`의 `v1.0.0`에 저장됩니다. 문서함에서 문서를 선택하면 데스크톱 앱 안의 문서 뷰어에서 바로 읽을 수 있습니다. 이력과 비교 화면의 **문서 보기**로 특정 버전을 열거나, **원본 다운로드**로 기존 편집기에서 사용할 파일을 받습니다. 8개 지원 형식의 파일을 각각 원본 그대로 보관하며 파일당 최대 30MB까지 등록할 수 있습니다.
2. 문서에서 브랜치를 만들거나 **수정안 제출**을 사용합니다. 검토 화면에서 변경을 확인한 다음 승인 또는 반려합니다. **두 버전 비교**에서는 이력에서 다른 버전을 선택해 기준본을 유지한 채 비교 대상을 바꿉니다. 비교를 끝내려면 **한 버전 보기**를 누릅니다.
3. **브랜치 대조**에서 두 최신본과 공통 조상을 확인할 수 있습니다. 양쪽 원본이 서로 다르게 바뀌면 편집기에서 합친 같은 형식의 파일을 올려 병합합니다. 복원하면 이전 파일이 새 버전으로 기록됩니다.
4. 페이지 미리보기를 아래로 스크롤하면 다음 페이지를 이어서 읽을 수 있습니다. 비교 화면에서는 두 버전이 함께 스크롤됩니다. **이전 변경**, **다음 변경**, **변경 목록**으로 바뀐 위치에 이동합니다. 엑셀은 시트가 이동해도 이름으로 맞추며, 셀 주소로 바로 열 수 있습니다.

| 형식 | 비교 화면 |
| --- | --- |
| PDF | 원본 페이지를 렌더링해 나란히 표시 |
| HWP, HWPX | rhwp에서 PDF로 변환 |
| DOCX, PPT, PPTX | LibreOffice에서 PDF로 변환 |
| XLS, XLSX | 시트 이름과 셀 주소로 값·병합·일부 서식 대조. PDF 페이지 보기 제공 |

네이티브 앱은 Rust 작업 스레드에서 PDFium으로 페이지를 렌더링하고 시각적 변경 영역을 계산합니다. Python 브리지는 캐시된 PDF의 경로와 문서 정보를 전달합니다. 브라우저 UI의 페이지 미리보기에는 Poppler의 `pdftoppm`가 필요합니다. LibreOffice의 `soffice`는 PATH와 일반 설치 경로에서 찾습니다. HWP/HWPX는 [rhwp](https://github.com/edwardkim/rhwp)의 `rhwp` 실행 파일을 PATH나 `data/tools/rhwp/rhwp`에 둡니다. 도구 경로는 `DOCVERS_PDFTOPPM`, `DOCVERS_SOFFICE`, `DOCVERS_RHWP`로 직접 지정할 수 있습니다.

문서 페이지는 같은 페이지 번호끼리 대조합니다. 페이지가 중간에 삽입되면 이후 페이지 차이도 함께 잡힐 수 있습니다. HWP와 Office의 글꼴 및 배치는 변환기와 설치된 글꼴에 따라 달라집니다. 스프레드시트 화면에서는 값과 XLSX 수식을 확인하고, 셀 값·병합·일부 서식의 변경을 찾을 수 있습니다. 인쇄 배치와 차트는 PDF 미리보기에서 확인할 수 있습니다. 암호화됐거나 변환할 수 없는 파일도 원본을 내려받을 수 있습니다.

## 자동 버전 번호

최초 등록은 `v1.0.0`입니다. 각 수정은 작업한 브랜치의 기준본과 비교합니다. 버전 라벨은 문서 전체에서 발급 순서대로 하나씩 부여됩니다. 브랜치에서도 라벨이 중복되지 않고 승인하거나 반려해도 라벨이 바뀌지 않습니다. 기존 이력은 같은 규칙으로 표시 라벨을 채우며, 버전 ID와 원본은 유지됩니다.

| 감지한 규모 | 기준 | 예시 |
| --- | --- | --- |
| 작음 | 변경 단위 4개 이하 | `v1.0.0` → `v1.0.1` |
| 중간 | 변경 단위 5개 이상, 큰 변경 기준 미만 | `v1.0.1` → `v1.1.0` |
| 큼 | 변경 단위 30개 이상이며 두 버전 중 더 큰 쪽의 절반 이상 | `v1.1.0` → `v2.0.0` |

엑셀은 시트·주소별 셀 값과 추출된 수식을, 다른 형식은 추출된 낱말과 문장부호를 기준으로 계산합니다. 추출 결과가 같거나 텍스트를 읽을 수 없는 문서는 작은 변경으로 처리합니다. 서식과 이미지 차이는 문서 대조 화면에서 확인할 수 있습니다. MCP 결과의 `version_label`, `change_scale`, `change_basis`에서도 번호와 기준을 확인할 수 있습니다.

## 앱에서 문서 편집

확정본의 **문서 편집**을 열면 미리보기 옆에서 변경을 입력할 수 있습니다. **수정안으로 저장**하면 원본과 수정본의 비교가 열리며, 승인하면 브랜치의 최신 버전이 갱신됩니다. 편집하는 동안 최신 버전이 바뀐 경우 최신본에서 다시 시작할 수 있습니다.

| 형식 | 직접 편집 |
| --- | --- |
| HWP, HWPX | rhwp로 본문·표의 문구 일괄 변경. 편집 명령을 제공하는 rhwp 0.9 계열 필요 |
| DOCX, PPTX | Word 본문·표·머리말·꼬리말·각주, PowerPoint 슬라이드의 기존 문구 변경 |
| XLSX | 셀을 선택해 텍스트·숫자·수식 입력 또는 값 삭제. 수정본을 Excel에서 열면 계산 설정에 따라 수식이 갱신됩니다 |
| PDF | 페이지 회전 및 메모 주석 추가 |
| PPT, XLS | 기존 편집기에서 수정한 파일을 GitDoc에 제출 |

[office_oxide](https://github.com/yfedoseev/office_oxide)는 DOCX/PPTX 텍스트 편집에 사용합니다. 검증 기준과 일치하는 편집 XML만 원본 문서 패키지에 넣고, 다른 경우에는 GitDoc의 XML 편집 경로를 사용합니다. XLSX는 기존 셀 편집 경로로 처리합니다. Word와 PowerPoint에서 하나의 문구가 여러 서식에 걸쳐 있으면 교체 문구에는 시작 지점의 서식이 적용됩니다. 앱의 페이지 미리보기는 서식과 배치 검토에 사용할 수 있습니다.

## 버전 내보내기

**저장**에서 선택한 버전을 내보냅니다. 지원하는 모든 문서를 PDF로 저장할 수 있으며, 원본 형식에 따라 편집 가능한 내보내기 형식을 제공합니다.

| 원본 | 추가로 내보낼 수 있는 편집 형식 |
| --- | --- |
| HWP, HWPX | 다른 한글 형식 |
| DOCX | ODT |
| PPT, PPTX | 다른 PowerPoint 형식 및 ODP |
| XLS, XLSX | 다른 Excel 형식 및 ODS |

PDF는 앱의 인쇄 레이아웃을 사용합니다. ODT, ODP, ODS는 해당 형식과 호환되는 편집기에서 열 수 있습니다. macOS와 Windows에서 **인쇄**를 누르면 선택한 버전의 시스템 인쇄 대화상자가 열리고 프린터와 인쇄할 페이지를 선택할 수 있습니다. 비교 화면에서는 **저장**과 **인쇄**가 수정본에 적용됩니다.

## 로컬 작업 폴더

`/path/to/gitdoc`은 이 프로젝트의 실제 절대 경로로 바꿉니다.

```sh
alias docvers='/path/to/gitdoc/.venv/bin/python /path/to/gitdoc/docvers.py'
docvers list
docvers checkout 1
docvers branch budget-review
docvers checkout 1 -b budget-review
docvers status
docvers diff
docvers commit -m '산출내역 수정'
```

체크아웃한 원본을 기존 편집기에서 편집합니다. 파일을 수정한 상태에서 브랜치를 전환하면 덮어쓰기를 막습니다. `main` 반영은 앱의 검토나 브랜치 병합을 사용합니다.

## MCP와 데스크톱 스킬

```sh
codex mcp add gitdoc -- "$PWD/.venv/bin/python" "$PWD/mcp_server.py"
```

Windows에서는 위 설정 명령의 Python 경로를 `.venv\Scripts\python.exe`의 절대 경로로 바꿉니다. 새 Codex 세션에서 `search_documents`, `list_branches`, `list_versions`, `read_version`, `compare_versions`, `compare_branches`로 조회할 수 있습니다. `register_document`, `create_document_branch`, `submit_revision`, `edit_revision`, `review_draft`, `merge_document_branches`, `restore_document_version`으로 로컬 저장소를 조작합니다. 파일을 올리는 도구는 사용자가 선택한 파일의 로컬 절대 경로를 받습니다. 승인·병합·복원에는 조회한 브랜치의 최신 버전 ID를 사용합니다. 앱과 MCP가 같은 `DOCVERS_DB`를 가리키도록 설정하세요.

[데스크톱 스킬](.agents/skills/document-versions-desktop/SKILL.md)은 이 프로젝트의 비교 화면을 열고 확인하는 절차를 담습니다. 아래 숫자는 문서·버전 ID 예시입니다. 화면에서는 같은 버전의 `v1.0.1` 같은 라벨을 표시합니다.

```sh
.venv/bin/python docvers.py open 1 --version 2 --page 2
.venv/bin/python docvers.py open 1 --base 2 --target 5 --page 2 --change 1
.venv/bin/python docvers.py open 1 --base 2 --target 5 --sheet '산출 내역' --cell B4
```

## 라이선스

GitDoc 소스는 [MIT 라이선스](LICENSE)로 배포합니다. 편집에 사용하는 [rhwp](https://github.com/edwardkim/rhwp)는 MIT 라이선스, [office_oxide](https://github.com/yfedoseev/office_oxide)는 MIT 또는 Apache-2.0 라이선스이며, [lxml](https://github.com/lxml/lxml/blob/master/LICENSES.txt)과 [pypdf](https://github.com/py-pdf/pypdf/blob/main/LICENSE)는 BSD 계열 라이선스를 적용합니다. 외부 도구와 라이브러리에는 각각의 원본 라이선스와 고지가 적용됩니다.
