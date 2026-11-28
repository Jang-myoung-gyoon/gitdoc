# 설치와 업데이트

[처음으로](Home.md) · [릴리스 상태](Release-Status.md)

## 공개 설치본 v0.1.4

| 운영체제 | 설치와 업데이트 |
| --- | --- |
| Apple Silicon macOS | DMG를 열고 GitDoc을 **응용 프로그램**에 복사합니다. Developer ID 서명과 Apple 공증이 적용된 설치본입니다. v0.1.4에서 **설정 → 업데이트 확인…**을 열고, 새 버전이 표시되면 앱에서 설치를 승인합니다. |
| Windows x64 | 공식 EXE를 내려받아 현재 사용자 계정으로 설치합니다. v0.1.4 EXE에는 Authenticode 서명이 없습니다. 앱의 **설정 → 업데이트 확인…**에서 버전과 공식 다운로드 링크를 확인하고 직접 설치합니다. |

v0.1.3에는 앱 내 업데이트 메뉴가 없으므로 처음 한 번은 같은 설치 위치에 v0.1.4를 덮어씁니다. 앱을 교체해도 기존 문서함과 설정은 아래 데이터 폴더에서 계속 읽습니다. [macOS DMG](https://github.com/Jang-myoung-gyoon/gitdoc/releases/download/v0.1.4/GitDoc-0.1.4-macos-arm64.dmg) · [Windows EXE](https://github.com/Jang-myoung-gyoon/gitdoc/releases/download/v0.1.4/GitDoc-0.1.4-windows-x64-setup.exe)

## 문서함 위치

| 실행 방식 | 기본 데이터베이스 |
| --- | --- |
| macOS 설치본 | `~/Library/Application Support/GitDoc/documents.sqlite3` |
| Windows 설치본 | `%APPDATA%\GitDoc\documents.sqlite3` |
| 소스 실행 | 프로젝트의 `data/documents.sqlite3` |

별도 문서함을 쓰려면 앱과 CLI, MCP를 시작하기 전에 동일한 `DOCVERS_DB` 절대 경로를 지정합니다. 브라우저 링크에는 작업 공간이 포함됩니다. 앱과 브라우저 상단에 표시된 작업 공간 이름과 ID로 대조할 수 있습니다.

## 문서를 열기 위해 추가로 설치하는 도구

| 도구 | 필요한 경우 | GitDoc이 찾는 위치 |
| --- | --- | --- |
| rhwp | HWP·HWPX 미리보기와 편집. 빈 한글 문서 생성은 **main**에서 **0.9.0 이상** 필요 | PATH, GitDoc 데이터 폴더의 `tools/rhwp/rhwp` (Windows는 `rhwp.exe`) 또는 `DOCVERS_RHWP` |
| LibreOffice | Office·OpenDocument 페이지 미리보기, 호환 형식 변환, DOC 편집 | PATH와 일반 설치 위치 또는 `DOCVERS_SOFFICE` |
| Poppler | 일부 PDF 양식·글꼴 대체 렌더링 | PATH 또는 `DOCVERS_PDFTOPPM` |
| Tesseract | Apple Vision이 없는 환경에서 스캔 등 이미지 글자 OCR | PATH 또는 `DOCVERS_TESSERACT`. 한국어·영어 언어 데이터 설치 |

GitDoc 패키지에는 Python 실행 환경, PDFium, Rust PDF 편집 도구가 포함됩니다. macOS에는 Apple Vision OCR 도구도 포함됩니다. 빈 한글 문서를 만들 때 PATH 도구가 오래됐으면 데이터 폴더의 지원 버전도 확인합니다. `DOCVERS_RHWP`를 지정했다면 그 실행 파일을 사용하므로 해당 도구의 버전과 경로를 확인하세요. [rhwp 0.9.0](https://github.com/edwardkim/rhwp/releases/tag/v0.9.0) · [소스 실행 안내](https://github.com/Jang-myoung-gyoon/gitdoc/blob/f1c35814ff2b127b6859869ee91229da0304279d/README.ko.md#소스에서-실행)
