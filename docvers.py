import argparse
import hashlib
import json
import os
import sys
import subprocess
import tempfile
from pathlib import Path

from desktop_link import desktop_url, document_path
from app import MAX_FILE, prepare_upload
from store import (
    DATABASE, ROOT, VERSION_FIELDS, append_version, branch_list, create_document, database, find_branch,
    find_document, find_version, initialize, new_branch, search_documents, text_diff,
)

TRACKING = ".docvers.json"


def require(value, message):
    if value is None:
        raise ValueError(message)
    return value


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tracking(required=True):
    path = Path(TRACKING)
    if not path.exists():
        if required:
            raise ValueError("이 폴더에서 먼저 checkout을 실행하세요.")
        return None
    info = json.loads(path.read_text(encoding="utf-8"))
    if info["database"] != str(DATABASE.resolve()):
        raise ValueError("이 작업 폴더는 다른 로컬 저장소를 사용합니다.")
    if info["filename"] != Path(info["filename"]).name:
        raise ValueError("작업 파일 이름을 확인하세요.")
    return info


def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)


def save_tracking(info):
    atomic_write(Path(TRACKING), (json.dumps(info, ensure_ascii=False, indent=2) + "\n").encode())


def current_branch(db, info):
    return require(find_branch(db, info["document_id"], info["branch"]), "브랜치를 찾을 수 없습니다.")


def add(args):
    upload = working_upload(args.file)
    with database(write=True) as db:
        document_id = create_document(db, (args.title or args.file.stem).strip(), upload)
    print(f"{document_id} · main v1 · {args.file.name}")


def documents(args):
    with database() as db:
        for row in search_documents(db, args.query.strip()[:150]):
            print(f"{row['id']} · {row['title']} · main v{row['number']}")


def checkout(args):
    prior = tracking(required=False)
    if prior and prior["document_id"] != args.document_id:
        raise ValueError("다른 문서는 별도 작업 폴더에서 체크아웃하세요.")
    with database() as db:
        branch = current_branch(db, {"document_id": args.document_id, "branch": args.branch})
        current = require(find_version(db, args.document_id, branch["head_id"], VERSION_FIELDS + ", content"),
                          "버전을 찾을 수 없습니다.")
    path = Path(prior["filename"] if prior else current["filename"])
    if path.name == TRACKING:
        raise ValueError("작업 파일 이름을 확인하세요.")
    if path.exists():
        actual = hash_file(path)
        if actual != current["sha256"] and (not prior or actual != prior["sha256"]):
            raise ValueError("수정된 작업 파일이 있습니다. 먼저 커밋하거나 다른 폴더에서 체크아웃하세요.")
    if not path.exists() or actual != current["sha256"]:
        atomic_write(path, current["content"])
    save_tracking({"database": str(DATABASE.resolve()), "document_id": args.document_id,
                   "branch": branch["name"], "version_id": current["id"],
                   "sha256": current["sha256"], "filename": path.name})
    print(f"{branch['name']} v{current['number']} · {path}")


def branches(args):
    info = tracking()
    with database(write=bool(args.name)) as db:
        branch = current_branch(db, info)
        if args.name:
            if branch["head_id"] != info["version_id"]:
                raise ValueError("브랜치에 최신 버전이 있습니다. 다시 체크아웃하세요.")
            new_branch(db, info["document_id"], args.name.strip().lower(), info["version_id"])
        for item in branch_list(db, info["document_id"]):
            mark = "*" if item["id"] == branch["id"] else " "
            print(f"{mark} {item['name']} v{item['number']}")


def status(_args):
    info = tracking()
    path = Path(info["filename"])
    with database() as db:
        branch = current_branch(db, info)
        base = find_version(db, info["document_id"], info["version_id"])
    print(f"{branch['name']} v{base['number']} · {path}")
    if not path.exists():
        print("작업 파일이 없습니다.")
    else:
        print("수정됨" if hash_file(path) != info["sha256"] else "변경 없음")
    if branch["head_id"] != info["version_id"]:
        print("브랜치에 최신 버전이 있습니다.")


def working_upload(path):
    with path.open("rb") as stream:
        return prepare_upload(path.name, stream.read(MAX_FILE + 1))


def diff(_args):
    info = tracking()
    path = Path(info["filename"])
    if hash_file(path) == info["sha256"]:
        print("변경 없음")
        return
    with database() as db:
        base = find_version(db, info["document_id"], info["version_id"], VERSION_FIELDS + ", extracted")
    _, _, _, _, note, content, _ = working_upload(path)
    if note or base["extraction_note"]:
        print(note or base["extraction_note"])
        return
    lines, truncated = text_diff(base["extracted"], content)
    print("\n".join(lines) if lines else "추출된 내용이 같습니다. 원본 파일의 바이트는 변경되었습니다.")
    if truncated:
        print("일부 변경 줄만 표시합니다.")


def commit(args):
    info = tracking()
    reason = args.message.strip()
    if not 1 <= len(reason) <= 500:
        raise ValueError("변경 사유를 1~500자로 입력하세요.")
    path = Path(info["filename"])
    upload = working_upload(path)
    with database(write=True) as db:
        branch = current_branch(db, info)
        if branch["name"] == "main":
            raise ValueError("작업 브랜치에서 커밋한 뒤 main으로 병합하세요.")
        if branch["head_id"] != info["version_id"]:
            raise ValueError("브랜치에 최신 버전이 있습니다. 수정 파일을 별도로 보관하고 다시 체크아웃하세요.")
        if info["sha256"] == upload[3]:
            raise ValueError("커밋할 변경이 없습니다.")
        version_id = append_version(db, branch, upload, reason)
        number = find_version(db, info["document_id"], version_id)["number"]
    info.update(version_id=version_id, sha256=upload[3])
    save_tracking(info)
    print(f"{branch['name']} v{number} · {reason}")


def open_desktop(args):
    if args.document_id is None and any(value is not None for value in
                                        (args.branch, args.base, args.target, args.version, args.page,
                                         args.change, args.sheet, args.cell)):
        raise ValueError("위치를 열려면 문서 ID를 지정하세요.")
    if args.target is not None and args.version is not None:
        raise ValueError("--version은 문서 보기에서 사용합니다. 비교할 때는 --target을 사용하세요.")
    if args.target is None and (args.base is not None or args.change is not None):
        raise ValueError("비교 위치를 열려면 --target 버전 ID를 지정하세요.")
    if any(value is not None and value < 1 for value in (args.page, args.change)):
        raise ValueError("페이지와 변경 번호는 1 이상이어야 합니다.")
    if args.change and not args.page:
        raise ValueError("--change를 사용할 때 --page를 지정하세요.")
    if args.document_id is not None:
        with database(readonly=True) as db:
            require(find_document(db, args.document_id), "문서를 찾을 수 없습니다.")
            for version_id in (args.base, args.target, args.version):
                if version_id is not None:
                    require(find_version(db, args.document_id, version_id), "이 문서의 버전을 찾을 수 없습니다.")
            if args.branch:
                require(find_branch(db, args.document_id, args.branch), "브랜치를 찾을 수 없습니다.")
    path = document_path(args.document_id, branch=args.branch, base=args.base, target=args.target,
                         version=args.version, page=args.page, change=args.change,
                         sheet=args.sheet, cell=args.cell)
    link = desktop_url(path)
    if args.url:
        print(link)
        return
    if sys.platform != "darwin":
        raise ValueError("데스크톱 앱은 macOS에서 사용할 수 있습니다.")
    bundle = ROOT / ".build" / "GitDoc.app"
    if not (bundle / "Contents/MacOS/GitDoc").is_file():
        subprocess.run([str(ROOT / "desktop/build.sh")], check=True)
    environment = ["--env", f"DOCVERS_DB={DATABASE}"]
    for key in ("DOCVERS_PORT", "DOCVERS_RHWP", "DOCVERS_SOFFICE", "DOCVERS_PDFTOPPM"):
        if key in os.environ:
            environment.extend(["--env", f"{key}={os.environ[key]}"])
    subprocess.run(["/usr/bin/open", *environment, "-a", str(bundle), "-u", link], check=True)
    print(link)


def main():
    parser = argparse.ArgumentParser(description="로컬 문서 버전 관리")
    parser.add_argument("-C", "--directory", type=Path, default=Path.cwd(), help="작업 폴더")
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("add", help="원본 파일을 새 문서로 등록")
    new.add_argument("file", type=Path)
    new.add_argument("-t", "--title")
    new.set_defaults(run=add)
    ls = commands.add_parser("list", help="로컬 문서와 문서 ID 조회")
    ls.add_argument("query", nargs="?", default="")
    ls.set_defaults(run=documents)
    co = commands.add_parser("checkout", help="문서 원본을 작업 폴더에 저장")
    co.add_argument("document_id", type=int)
    co.add_argument("-b", "--branch", default="main")
    co.set_defaults(run=checkout)
    br = commands.add_parser("branch", help="로컬 브랜치 조회 또는 생성")
    br.add_argument("name", nargs="?")
    br.set_defaults(run=branches)
    commands.add_parser("status", help="작업 파일 변경 확인").set_defaults(run=status)
    commands.add_parser("diff", help="커밋 전 내용 대조").set_defaults(run=diff)
    cm = commands.add_parser("commit", help="작업 브랜치에 변경 저장")
    cm.add_argument("-m", "--message", required=True)
    cm.set_defaults(run=commit)
    desktop = commands.add_parser("open", help="GitDoc 데스크톱 앱에서 문서 또는 비교 화면 열기")
    desktop.add_argument("document_id", type=int, nargs="?")
    desktop.add_argument("-b", "--branch")
    desktop.add_argument("--base", type=int)
    desktop.add_argument("--target", type=int)
    desktop.add_argument("--version", type=int, help="특정 버전을 문서 뷰어에서 열기")
    desktop.add_argument("--page", type=int)
    desktop.add_argument("--change", type=int)
    desktop.add_argument("--sheet")
    desktop.add_argument("--cell")
    desktop.add_argument("--url", action="store_true", help="앱을 실행하지 않고 링크 출력")
    desktop.set_defaults(run=open_desktop)
    args = parser.parse_args()
    try:
        os.chdir(args.directory)
        initialize()
        args.run(args)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
