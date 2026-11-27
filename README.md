# GitDoc · 로컬 문서 버전 관리

한글, Word, PDF, PowerPoint, Excel을 기존 편집기에서 수정하고 GitDoc에서 버전을 관리합니다. 두 문서를 나란히 열어 변경 위치를 확인하고, 수정안을 검토하거나 별도 브랜치에서 병합할 수 있습니다. [기획서와 화면 시안](기획서.md)

## 시작

macOS 12 이상, Python 3.12, Xcode Command Line Tools가 필요합니다.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-mcp.txt
.venv/bin/python docvers.py open
```

첫 실행에서 `.build/GitDoc.app`을 빌드하고 엽니다. 이후 이 앱을 Finder에서 열거나 같은 명령을 사용할 수 있습니다. 앱은 필요할 때 자체 로컬 서버를 실행합니다. 브라우저에서 작업하려면 `.venv/bin/python app.py`를 실행한 뒤 [로컬 문서함](http://127.0.0.1:8796/)을 여세요.

저장소는 `data/documents.sqlite3`입니다. `DOCVERS_DB`와 `DOCVERS_PORT`로 저장소와 포트를 바꿀 수 있습니다. 다른 저장소나 포트로 전환할 때는 GitDoc을 종료하고 새 설정으로 엽니다. 이 프로젝트는 기존 시제품 코드와 화면 시안을 사용하며 저장소는 별도로 시작합니다.

## 버전 관리와 비교

1. 문서함에서 원본을 올립니다. 최초 확정본이 `main`의 v1에 저장됩니다. 8개 지원 형식의 파일을 각각 원본 그대로 보관하며 파일당 최대 30MB까지 등록할 수 있습니다.
2. 문서에서 브랜치를 만들거나 **수정안 제출**을 사용합니다. 검토 화면에서 변경을 확인한 다음 승인 또는 반려합니다.
3. **브랜치 대조**에서 두 최신본과 공통 조상을 확인할 수 있습니다. 양쪽 원본이 서로 다르게 바뀌면 편집기에서 합친 같은 형식의 파일을 올려 병합합니다. 복원하면 이전 파일이 새 버전으로 기록됩니다.
4. 비교 화면의 **이전 변경**, **다음 변경**, **변경 목록**으로 바뀐 위치에 이동합니다. 엑셀은 시트가 이동해도 이름으로 맞추며, 셀 주소로 바로 열 수 있습니다.

| 형식 | 비교 화면 |
| --- | --- |
| PDF | 원본 페이지를 렌더링해 나란히 표시 |
| HWP, HWPX | rhwp에서 PDF로 변환 |
| DOCX, PPT, PPTX | LibreOffice에서 PDF로 변환 |
| XLS, XLSX | 시트 이름과 셀 주소로 값·병합·일부 서식 대조. PDF 페이지 보기 제공 |

페이지 미리보기에 Poppler의 `pdftoppm`가 필요합니다. LibreOffice의 `soffice`와 `pdftoppm`는 PATH, 일반 macOS 설치 경로, Codex 번들 도구 경로에서 찾습니다. HWP/HWPX는 [rhwp](https://github.com/edwardkim/rhwp)의 `rhwp` 실행 파일을 PATH나 `data/tools/rhwp/rhwp`에 둡니다. 도구 경로는 `DOCVERS_PDFTOPPM`, `DOCVERS_SOFFICE`, `DOCVERS_RHWP`로 직접 지정할 수 있습니다.

문서 페이지는 같은 페이지 번호끼리 대조합니다. 페이지가 중간에 삽입되면 이후 페이지 차이도 함께 잡힐 수 있습니다. HWP와 Office의 글꼴 및 배치는 변환기와 설치된 글꼴에 따라 달라집니다. 스프레드시트 화면은 값, XLSX 수식 문자열, 병합 셀과 일부 서식을 표시합니다. 인쇄 배치와 차트는 PDF 미리보기에서 확인할 수 있습니다. 암호화됐거나 변환할 수 없는 파일도 원본을 내려받을 수 있습니다.

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

새 Codex 세션에서 `search_documents`, `list_branches`, `list_versions`, `read_version`, `compare_versions`, `compare_branches`를 사용할 수 있습니다. MCP 서버는 읽기 전용입니다. 앱과 다른 저장소를 쓰도록 구성한 경우 MCP 환경의 `DOCVERS_DB`도 같은 경로로 맞춥니다.

[데스크톱 스킬](.agents/skills/document-versions-desktop/SKILL.md)은 이 프로젝트의 비교 화면을 열고 확인하는 절차를 담습니다. 아래 숫자는 화면의 v번호가 아닌 문서·버전 ID 예시입니다.

```sh
.venv/bin/python docvers.py open 1 --base 2 --target 5 --page 2 --change 1
.venv/bin/python docvers.py open 1 --base 2 --target 5 --sheet '산출 내역' --cell B4
```
