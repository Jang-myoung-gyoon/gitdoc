# GitDoc · 로컬 문서 버전 관리

[English](README.md) · 한국어

한글, Word, PDF, PowerPoint, Excel을 GitDoc에서 열고, 지원하는 항목을 직접 편집하고, 버전을 관리합니다. 두 문서를 나란히 열어 변경 위치를 확인하고, 수정안을 검토하거나 별도 브랜치에서 병합할 수 있습니다. [기획서와 화면 시안](기획서.md)

## GitDoc Hub

[GitDoc Hub](hub/README.md)는 텍스트 문서 공동 편집, 저장소 초대, 버전 관리, 검토, MCP를 제공하는 웹 작업 공간입니다.

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

`docvers.py open`은 실행 파일이 없으면 Cargo로 빌드합니다. 소스를 갱신한 후에는 `cargo build --locked --manifest-path desktop/native/Cargo.toml`로 다시 빌드합니다. macOS에서 Finder로 실행하려면 `./desktop/build.sh`로 `.build/GitDoc.app`을 빌드합니다.

기본 저장소는 `data/documents.sqlite3`입니다. `DOCVERS_DB`로 경로를 바꾸고 데스크톱 앱, CLI, MCP에 같은 값을 지정합니다.

## 브라우저 뷰어

데스크톱 앱에서 **브라우저에서 보기**를 누르면 현재 버전 또는 두 버전 비교가 기본 브라우저에서 열립니다. 보고 있던 페이지나 시트의 선택 셀도 이어서 볼 수 있습니다.

`python docvers.py open --browser`로 로컬 문서함을 열 수 있습니다. Codex 내장 브라우저에서 확인하려면 데스크톱 앱의 **웹 링크 복사** 또는 **웹 문서함 링크 복사**로 얻은 주소를 붙여넣으세요. GitDoc MCP의 `open_browser_view`나 CLI의 `--url`도 같은 작업 공간의 주소를 반환합니다. 로컬 앱과 웹의 상단에서 작업 공간 이름과 ID를 확인할 수 있습니다. 다른 화면에서 등록하거나 수정한 내용은 브라우저를 새로고침하거나 데스크톱 앱의 **새로 고침**을 눌러 확인할 수 있습니다.

```sh
python docvers.py open 1 --version 2 --page 3 --browser --url
python docvers.py open 1 --base 2 --target 3 --browser --url
```

뷰어는 기본적으로 `127.0.0.1:8796`에서 실행됩니다. 같은 컴퓨터의 브라우저에서 주소를 여세요. 기본 포트를 다른 작업 공간이 사용 중이면 별도 포트를 찾습니다. `DOCVERS_PORT`로 포트를 고정하거나 `python app.py`로 직접 실행할 수 있습니다. 작업 공간 링크는 문서 ID를 열기 전에 저장소가 일치하는지 확인합니다.

## 버전 관리와 비교

데스크톱 앱 왼쪽 버전 이력에서 커밋을 우클릭하면 해당 버전까지 되돌리거나 그 커밋부터 최신 커밋까지 현재 브랜치에서 제거할 수 있습니다. 확인 후 기존 확정본에 `recovery-…` 브랜치를 만들어 복구할 수 있게 합니다. 기존 버전 복원 기능은 이전 내용을 새 버전으로 기록합니다. MCP에서는 `reset_document_branch`를 사용합니다.

문서를 읽으면서 데스크톱 앱의 왼쪽 패널이나 브라우저의 고정 작업 표시줄에서 브랜치를 전환하고, 버전 이력을 확인하고, 수정안을 제출할 수 있습니다. 브라우저에서 작업 창을 닫으면 읽던 위치가 유지됩니다.

1. 문서함에서 원본을 올립니다. 최초 확정본이 `main`의 `v1.0.0`에 저장됩니다. 문서함에서 문서를 선택하면 데스크톱 앱 안의 문서 뷰어에서 바로 읽을 수 있습니다. 이력과 비교 화면의 **문서 보기**로 특정 버전을 열거나, **원본 다운로드**로 기존 편집기에서 사용할 파일을 받습니다. 지원하는 파일은 원본 그대로 보관하며 기본 등록 한도는 파일당 30MiB입니다. 데스크톱 앱, 웹 앱, CLI, MCP 서버를 시작하기 전에 `DOCVERS_MAX_FILE_MIB`에 1~100 사이의 정수를 지정해 한도를 바꿀 수 있습니다.
2. 문서에서 브랜치를 만들거나 **수정안 제출**을 사용합니다. 검토 화면에서 변경을 확인한 다음 승인 또는 반려합니다. **두 버전 비교**에서는 이력에서 다른 버전을 선택해 기준본을 유지한 채 비교 대상을 바꿉니다. 비교를 끝내려면 **한 버전 보기**를 누릅니다.
3. **브랜치 대조**에서 두 최신본과 공통 조상을 확인할 수 있습니다. 양쪽 원본이 서로 다르게 바뀌면 편집기에서 합친 같은 형식의 파일을 올려 병합합니다. 복원하면 이전 파일이 새 버전으로 기록됩니다.
4. 데스크톱 앱과 브라우저에서 스크롤로 다음 페이지를 이어서 읽거나 페이지 버튼으로 이동할 수 있습니다. 스크롤하면 현재 페이지 번호도 갱신됩니다. 화면에 보이는 페이지를 우선 렌더링하고, 그 위와 아래의 한 쪽씩을 미리 준비합니다. 비교 화면에서는 내용이 같은 페이지를 맞추고 각 버전의 원래 페이지 번호를 보여 줍니다. 페이지 버튼 아래의 **이전 변경**, **다음 변경**으로 시각적 변경 위치를 이동할 수 있습니다. 가까운 변경 영역을 합쳐 페이지당 최대 7개의 상자로 표시하며, 상자가 페이지의 80% 이상을 덮으면 전체 페이지를 변경 1건으로 표시합니다. 전체 확인이 끝나면 변경 건수를 볼 수 있습니다. 엑셀은 시트가 이동해도 이름으로 맞추며, 셀 주소로 바로 열 수 있습니다.

| 형식 | 비교 화면 |
| --- | --- |
| PDF | 원본 페이지를 렌더링해 나란히 표시 |
| HWP, HWPX | rhwp에서 PDF로 변환 |
| DOC, DOCX, DOCM, ODT, PPT, PPTX, PPTM, ODP, ODS | LibreOffice에서 PDF로 변환 |
| XLS, XLSX, XLSM | 시트 이름과 셀 주소로 값·병합·일부 서식 대조. PDF 페이지 보기 제공 |
| CSV, TSV | 셀 내용을 텍스트로 표시하고 비교합니다. 행 번호로 비교하거나 데스크톱 또는 `--row-key`에서 공통 고유 열을 지정할 수 있습니다. |

네이티브 앱은 Rust 작업 스레드에서 PDFium으로 페이지를 렌더링하고 시각적 변경 영역을 계산합니다. Python 브리지는 캐시된 PDF의 경로와 문서 정보를 전달합니다. GitDoc은 저장된 페이지 지문을 브라우저와 데스크톱 앱에서 함께 사용합니다. 브라우저는 일반 PDF 미리보기에 PDFium을 사용하고, 양식이나 글꼴 대체가 필요한 경우 Poppler의 `pdftoppm`를 사용합니다. 비교할 때 어느 한쪽이라도 Poppler가 필요하면 양쪽에 적용합니다. LibreOffice의 `soffice`는 PATH와 일반 설치 경로에서 찾습니다. HWP/HWPX는 [rhwp](https://github.com/edwardkim/rhwp)의 `rhwp` 실행 파일을 PATH나 `data/tools/rhwp/rhwp`에 둡니다. 도구 경로는 `DOCVERS_PDFTOPPM`, `DOCVERS_SOFFICE`, `DOCVERS_RHWP`로 직접 지정할 수 있습니다.

렌더링된 페이지의 글자를 드래그한 뒤 macOS에서는 **Cmd+C**, Windows에서는 **Ctrl+C**로 복사하세요. 두 버전을 비교할 때도 양쪽에서 선택할 수 있습니다. 내장 텍스트를 우선 사용하며, 스캔이나 윤곽선으로 된 글자는 페이지를 표시한 뒤 OCR로 준비합니다. macOS 앱 빌드에는 Apple Vision OCR 도구가 포함됩니다. 이 도구 없이 OCR을 쓰려면 한글·영어 언어 데이터가 포함된 Tesseract를 설치해 PATH에 추가하거나 `DOCVERS_TESSERACT`를 지정하세요. `DOCVERS_OCR_LANGUAGES`로 언어를 바꿀 수 있으며 기본값은 `kor+eng`입니다. OCR 결과에는 오인식이 있을 수 있습니다.

삽입·삭제된 페이지는 따로 표시하고 반대쪽에는 대응하는 페이지가 없다고 표시합니다. 위치가 밀려도 같은 페이지는 변경으로 집계하지 않습니다. 쪽이 밀린 뒤 같은 하단 위치에 반복되는 차이만 있으면 화면에 강조하되 본문 변경 목록과 건수에서 분리합니다. HWP와 Office의 글꼴 및 배치는 변환기와 설치된 글꼴에 따라 달라집니다. 스프레드시트 화면에서는 값과 XLSX 수식을 확인하고, 셀 값·병합·일부 서식의 변경을 찾을 수 있습니다. 인쇄 배치와 차트는 PDF 미리보기에서 확인할 수 있습니다. 암호화됐거나 변환할 수 없는 파일도 원본을 내려받을 수 있습니다. DOCM·XLSM·PPTM은 원본 그대로 보관하고 변환 환경에서 매크로 실행을 비활성화합니다. ODS는 페이지 단위로 비교합니다. CSV는 쉼표, TSV는 탭으로 구분합니다. UTF-8, BOM이 있는 UTF-16, CP949를 지원합니다. 순서가 바뀐 행을 비교하려면 두 버전에서 같은 위치에 있으며 중복이나 빈 값이 없는 열 제목을 선택합니다. 표 보기는 버전당 최대 25,000개 필드를 지원하며, 더 큰 표는 원본과 PDF로 내보낼 수 있습니다.

## 문서 미리보기 글꼴

GitDoc은 시스템과 사용자 글꼴 폴더를 검색합니다. Office 문서는 설치되거나 내장된 글꼴을 우선 사용하고, 없는 글꼴은 설치된 대체 글꼴로 표시합니다. 한글 문서는 시스템 글꼴과 한글용 명조·고딕 대체 글꼴을 사용합니다. 글꼴이 내장되지 않은 PDF는 Poppler가 있으면 브라우저와 데스크톱에서 함께 사용합니다. 네이티브 PDFium 렌더러도 시스템 글꼴을 읽습니다. 저장된 원본 파일은 그대로 유지됩니다.

`python docvers.py fonts`로 설치된 글꼴 목록을 볼 수 있습니다. 특정 글꼴을 확인하거나 대체 글꼴을 지정할 수 있습니다.

```sh
python docvers.py fonts --resolve "Calibri"
python docvers.py fonts --set "Calibri" "Arial"
python docvers.py fonts --remove "Calibri"
```

대체 규칙은 로컬 데이터베이스 옆의 `font-substitutions.json`에 저장되며 LibreOffice 및 Poppler 미리보기에 적용됩니다. 규칙을 바꾼 뒤 문서를 다시 여세요. `DOCVERS_FONT_DIRS`에 OS 경로 구분자로 추가 폴더를 지정하면 글꼴 탐색과 rhwp, Fontconfig, 네이티브 PDFium에서 사용할 수 있습니다. `DOCVERS_UI_FONT`로 데스크톱 UI에서 우선 사용할 글꼴 파일을 지정할 수 있습니다. 글꼴이 대체되면 글자 폭이나 줄바꿈이 달라질 수 있습니다.

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
| DOCX, PPTX | Word 단락과 PowerPoint 슬라이드 텍스트를 직접 수정하고, 글꼴·크기·색·굵게·기울임·밑줄·정렬을 적용하거나 찾기·바꾸기 |
| XLSX | 최신 버전에서 셀을 더블클릭해 기존 값이나 수식을 불러와 수정하거나 삭제. Excel에서 문서의 계산 설정에 따라 수식 갱신 |
| PDF | 페이지 회전·삭제, 메모 추가, 빈 페이지 또는 다른 PDF의 페이지 삽입 |

페이지를 삽입할 때는 새 페이지가 들어갈 쪽 번호를 지정합니다. 맨 뒤에 넣으려면 현재 페이지 수에 1을 더하세요. 삽입 위치와 장수는 버전 이력에 저장됩니다. 비교 화면에서는 **GitDoc 편집 기록**을 우선 적용합니다. 적용할 기록이 없는 외부 업로드의 삽입은 **자동 판정**으로 표시합니다.
| DOC, PPT, XLS, ODT, ODS, ODP, DOCM, XLSM, PPTM, CSV, TSV | 기존 편집기에서 수정한 파일을 GitDoc에 제출 |

[office_oxide](https://github.com/yfedoseev/office_oxide)는 DOCX/PPTX의 호환되는 찾기·바꾸기에 사용합니다. 단락별 텍스트와 서식 변경, 셀 편집은 GitDoc이 문서 XML에 적용합니다. 여러 단락을 수정안 하나로 저장한 뒤 렌더링된 비교 화면에서 검토합니다. MCP에서는 `list_editable_blocks`로 단락을 조회하고 `edit_revision`에 `operation: edit_blocks`를 지정합니다.

도형·차트·페이지 레이아웃은 **전문 편집기로 열기**에서 편집할 수 있습니다. GitDoc이 만든 로컬 작업 사본이 파일 형식에 연결된 앱에서 열립니다. 원래 파일 형식으로 저장한 뒤 GitDoc의 **저장한 파일 가져오기**를 눌러 수정안으로 제출하세요.

## 버전 내보내기

**저장**에서 선택한 버전을 내보냅니다. 지원하는 모든 문서를 PDF로 저장할 수 있으며, 원본 형식에 따라 편집 가능한 내보내기 형식을 제공합니다.

| 원본 | 추가로 내보낼 수 있는 편집 형식 |
| --- | --- |
| HWP, HWPX | 다른 한글 형식 |
| DOC | DOCX 및 ODT |
| DOCX | ODT |
| ODT, ODS, ODP | 각각 DOCX, XLSX, PPTX |
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

CSV·TSV의 행 순서가 바뀐 경우 `compare_versions`의 `row_key`에 열 제목을 지정합니다. 생성된 데스크톱 링크에도 같은 행 맞추기 기준이 적용됩니다.

| 작업 | MCP 도구 |
| --- | --- |
| 직접 편집 및 내보내기 형식 조회 | `get_document_capabilities` |
| 페이지 수와 두 버전의 대응 위치 조회 | `get_preview_info` |
| 단일 페이지 또는 두 버전 비교를 PNG로 받기 | `render_document_page` |
| 시각적 변경 영역 번호 및 하단 표시 변경 찾기 | `list_visual_changes` |
| PDF 메모와 시트의 셀·수식·서식·병합 조회 및 비교 | `get_pdf_notes`, `inspect_workbook` |
| 원본·PDF·호환 형식으로 저장 | `export_document` |

MCP는 렌더링 결과를 `image/png` 이미지로 직접 반환합니다. 비교 이미지는 대상 버전을 `version_id`, 기준 버전을 `base_version_id`, 정렬된 비교 위치를 `page`에 전달합니다. `change`를 지정하면 해당 변경 영역을 강조합니다. 위치나 변경 목록은 `next_offset`으로 이어서 조회할 수 있습니다. 내보내려면 로컬 절대 경로를 지정하고, 기존 파일을 교체하려면 `overwrite`를 명시하세요.

모든 MCP 도구는 CLI에서도 실행할 수 있습니다. `docvers tool`은 도구 목록과 입력 스키마를 출력합니다. `--json -`는 표준 입력으로 JSON을 받고, `--output -`는 표준 출력으로 PNG를 보냅니다.

```sh
docvers tool render_document_page --json '{"document_id":1,"version_id":2,"page":1}' --output preview.png
docvers tool export_document --json '{"document_id":1,"version_id":2,"output_path":"/absolute/path/export.pdf"}'
docvers print 1 2
```

CLI 인쇄는 macOS와 Linux에서 CUPS의 `lp`를 사용하고, Windows에서 기본 프린터와 등록된 PDF 인쇄 프로그램을 사용합니다. 인쇄 작업을 보내지 않고 인쇄 레이아웃을 준비하려면 `export_document`로 PDF를 저장하세요.

[데스크톱 스킬](.agents/skills/document-versions-desktop/SKILL.md)은 이 프로젝트의 비교 화면을 열고 확인하는 절차를 담습니다. 아래 숫자는 문서·버전 ID 예시입니다. 화면에서는 같은 버전의 `v1.0.1` 같은 라벨을 표시합니다.

```sh
.venv/bin/python docvers.py open 1 --version 2 --page 2
.venv/bin/python docvers.py open 1 --base 2 --target 5 --page 2 --change 1
.venv/bin/python docvers.py open 1 --base 2 --target 5 --sheet '산출 내역' --cell B4
```

## 라이선스

GitDoc 소스는 [MIT 라이선스](LICENSE)로 배포합니다. 편집에 사용하는 [rhwp](https://github.com/edwardkim/rhwp)는 MIT 라이선스, [office_oxide](https://github.com/yfedoseev/office_oxide)는 MIT 또는 Apache-2.0 라이선스이며, [lxml](https://github.com/lxml/lxml/blob/master/LICENSES.txt)과 [pypdf](https://github.com/py-pdf/pypdf/blob/main/LICENSE)는 BSD 계열 라이선스를 적용합니다. 외부 도구와 라이브러리에는 각각의 원본 라이선스와 고지가 적용됩니다.
