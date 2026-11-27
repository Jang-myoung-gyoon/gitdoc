"""Install a read-only GitDoc connection through supported local agent CLIs."""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import store

NAME = "gitdoc-local"
SKILL = "document-versions-desktop"
LANGUAGES = {"codex": "Codex", "claude": "Claude Code"}
ENV_KEYS = ("PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT",
            "WINDIR", "TMP", "TEMP", "TMPDIR", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME")


def cli_environment():
    env = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
    env.update(NO_COLOR="1", DISABLE_TELEMETRY="1", DISABLE_AUTOUPDATER="1")
    return env


def find_cli(target, home):
    name = target + ".exe" if os.name == "nt" else target
    candidates = [shutil.which(name), home / ".local/bin" / name]
    if os.name == "nt":
        candidates.append(Path(os.environ.get("LOCALAPPDATA", home)) / "Programs" / target / name)
    for candidate in filter(None, candidates):
        path = Path(candidate).resolve()
        if path.is_file() and os.access(path, os.X_OK) and (os.name != "nt" or path.suffix.lower() == ".exe"):
            return str(path)
    return None


def paths_for(target, home):
    if target == "codex":
        return (Path(os.environ.get("CODEX_HOME", home / ".codex")).expanduser().resolve() / "config.toml",
                home / ".agents/skills" / SKILL)
    config_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")).expanduser().resolve()
    config = config_home / ".claude.json" if os.environ.get("CLAUDE_CONFIG_DIR") else home / ".claude.json"
    return config, config_home / "skills" / SKILL


def skill_bytes():
    paths = [store.ROOT / ".agents/skills" / SKILL / "SKILL.md", store.ROOT / "skills" / SKILL / "SKILL.md"]
    original = next((p for p in paths if p.is_file()), None)
    if original is None:
        raise ValueError("GitDoc MCP 스킬이 번들에 없습니다. 앱을 다시 설치하세요.")
    content = original.read_text(encoding="utf-8")
    end = content.index("\n---\n", 3) + 5
    notice = ("\n## Local read-only connection\n\n"
              "When using gitdoc-local, call only tools that the server advertises. "
              "Use the GitDoc desktop app for actions that change documents. "
              "This connection reads one explicitly chosen workspace.\n")
    content = content[:end] + notice + content[end:]
    return content.encode()


def configured_entry(plan):
    path = Path(plan["config"])
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        data = tomllib.loads(content) if plan["target"] == "codex" else json.loads(content)
        return data.get("mcp_servers" if plan["target"] == "codex" else "mcpServers", {}).get(NAME)
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError("에이전트 설정을 읽을 수 없어 변경하지 않았습니다.") from None


def matches(plan, entry):
    return (isinstance(entry, dict) and entry.get("command") == plan["server"][0]
            and entry.get("args") == plan["server"][1:]
            and entry.get("env") == {"DOCVERS_DB": plan["database"]}
            and entry.get("type", "stdio") == "stdio")


def run_cli(argv, home):
    try:
        return subprocess.run(argv, cwd=home, env=cli_environment(), shell=False,
                              stdin=subprocess.DEVNULL, capture_output=True, check=False, timeout=20,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        raise ValueError("에이전트 CLI를 실행하지 못했습니다.") from None


async def verify_server(plan):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command, *args = plan["server"]
    options = StdioServerParameters(command=command, args=args, env={"DOCVERS_DB": plan["database"]})
    async with asyncio.timeout(12), stdio_client(options) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.list_tools()
        names = {tool.name for tool in result.tools}
        if not {"search_documents", "list_versions"} <= names or any(
                not tool.annotations or not tool.annotations.readOnlyHint for tool in result.tools):
            raise ValueError("읽기 전용 MCP 도구를 확인할 수 없습니다.")


class AgentSetup:
    def __init__(self):
        self.prepared = {}

    def preview(self, target):
        if target not in LANGUAGES:
            raise ValueError("지원하는 로컬 에이전트를 선택하세요.")
        home = Path.home().resolve()
        root = store.ROOT.resolve()
        for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR"):
            if os.environ.get(key) and not Path(os.environ[key]).is_absolute():
                raise ValueError("에이전트 설정 위치는 절대 경로여야 합니다.")
        if store.PACKAGED and (root.is_relative_to("/Volumes") or "AppTranslocation" in root.parts):
            raise ValueError("디스크 이미지에서 앱을 설치 위치로 옮긴 뒤 다시 시도하세요.")
        cli = find_cli(target, home)
        config, skill = paths_for(target, home)
        if not cli:
            raise ValueError(f"{LANGUAGES[target]} CLI를 찾을 수 없습니다. 설치 프롬프트를 복사해 설정하세요.")
        if not store.DATABASE.is_file() or not (root / "mcp_server.py").is_file():
            raise ValueError("현재 문서함 데이터베이스 또는 GitDoc MCP 엔진을 찾을 수 없습니다.")
        payload = skill_bytes()
        server = [str(Path(sys.executable).absolute()), "-E", "-s", "-B", str(root / "mcp_server.py"), "--read-only"]
        database = str(store.DATABASE)
        install_env = ["--env", "DOCVERS_DB=" + database]
        install_options = [*install_env, NAME]
        remove_options = []
        if target == "claude":
            install_options = [NAME, "--scope", "user", "--transport", "stdio", *install_env]
            remove_options = ["--scope", "user"]
        plan = {"target": target, "home": str(home), "config": str(config), "skill": str(skill),
                "database": database, "server": server,
                "install": [cli, "mcp", "add", *install_options, "--", *server],
                "remove": [cli, "mcp", "remove", *remove_options, NAME],
                "skill_digest": hashlib.sha256(payload).hexdigest()}
        entry = configured_entry(plan)
        if entry is not None and not matches(plan, entry):
            raise ValueError("같은 이름의 MCP 설정이 이미 있습니다. 기존 항목은 변경하지 않았습니다.")
        self.check_skill(skill, payload)
        plan["registered"] = entry is not None
        check = run_cli([cli, "--version"], home)
        if check.returncode:
            raise ValueError("에이전트 CLI의 버전을 확인하지 못했습니다.")
        plan["version"] = (check.stdout.decode(errors="replace").strip()[:100])
        plan["id"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
        self.prepared[target] = plan
        return plan

    @staticmethod
    def check_skill(folder, payload):
        if folder.is_symlink() or (folder.exists() and (
                not folder.is_dir() or list(folder.iterdir()) != [folder / "SKILL.md"]
                or (folder / "SKILL.md").is_symlink() or (folder / "SKILL.md").read_bytes() != payload)):
            raise ValueError("같은 이름의 스킬이 이미 있습니다. 기존 스킬은 변경하지 않았습니다.")

    def apply(self, target, plan_id, confirmed, operation):
        plan = self.prepared.get(target)
        if confirmed is not True or not plan or plan["id"] != plan_id or operation not in ("install", "remove"):
            raise ValueError("설치 계획을 확인한 뒤 다시 시도하세요.")
        if self.preview(target)["id"] != plan_id:
            raise ValueError("설정이 바뀌었습니다. 새 설치 계획을 확인하세요.")
        folder = Path(plan["skill"])
        payload = skill_bytes()
        if operation == "remove":
            if plan["registered"]:
                run_cli(plan["remove"], plan["home"])
                if configured_entry(plan) is not None:
                    raise ValueError("MCP 등록을 해제하지 못했습니다.")
            if folder.is_dir():
                self.check_skill(folder, payload)
                (folder / "SKILL.md").unlink()
                folder.rmdir()
            self.prepared.pop(target, None)
            return {"message": "GitDoc 연결을 해제했습니다."}

        try:
            asyncio.run(verify_server(plan))
        except Exception:
            raise ValueError("GitDoc MCP 엔진을 시작하거나 읽기 전용 도구를 확인하지 못했습니다.") from None
        made_skill = False
        try:
            if not folder.exists():
                folder.parent.mkdir(parents=True, exist_ok=True)
                folder.mkdir()
                made_skill = True
                with (folder / "SKILL.md").open("xb") as output:
                    output.write(payload)
            if not plan["registered"]:
                if configured_entry(plan) is not None:
                    raise ValueError("MCP 설정이 바뀌었습니다. 다시 확인하세요.")
                run_cli(plan["install"], plan["home"])
                if not matches(plan, configured_entry(plan)):
                    raise ValueError("에이전트에 GitDoc MCP를 등록하지 못했습니다.")
        except (OSError, ValueError):
            if made_skill:
                (folder / "SKILL.md").unlink(missing_ok=True)
                folder.rmdir()
            raise
        self.prepared.pop(target, None)
        return {"message": "읽기 전용 GitDoc MCP와 스킬을 설치했습니다."}


agent_setup = AgentSetup()
