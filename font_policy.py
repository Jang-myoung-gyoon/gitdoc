"""Inventory local fonts and resolve missing document families without changing originals."""

import hashlib
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic
from unicodedata import normalize
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from defusedxml import DefusedXmlException, ElementTree as safe_xml
from fontTools.ttLib import TTCollection, TTFont, TTLibError

from store import DATABASE

FONT_SUFFIXES = {".ttf", ".otf", ".ttc", ".otc", ".dfont"}
KOREAN = {
    "sans": ("Noto Sans CJK KR", "Noto Sans KR", "Malgun Gothic", "Apple SD Gothic Neo", "NanumGothic", "Hancom Gothic"),
    "serif": ("Noto Serif CJK KR", "Noto Serif KR", "Batang", "AppleMyungjo", "NanumMyeongjo", "HCR Batang"),
}
GENERIC = {
    "sans": ("Liberation Sans", "Arial", "DejaVu Sans", "Noto Sans"),
    "serif": ("Liberation Serif", "Times New Roman", "DejaVu Serif", "Noto Serif"),
    "mono": ("Liberation Mono", "Courier New", "DejaVu Sans Mono", "Menlo", "Consolas", "Noto Sans Mono"),
}
EQUIVALENTS = {
    "Calibri": ("Carlito", "Aptos"), "Calibri Light": ("Carlito", "Aptos"),
    "Cambria": ("Caladea", "Liberation Serif"), "Arial": ("Liberation Sans",),
    "Times New Roman": ("Liberation Serif",), "Courier New": ("Liberation Mono",),
    "Helvetica": ("Arial", "Liberation Sans"), "Times-Roman": ("Times New Roman", "Liberation Serif"),
    "Courier": ("Courier New", "Liberation Mono"), "맑은 고딕": KOREAN["sans"],
    "Malgun Gothic": KOREAN["sans"], "함초롬돋움": ("HCR Dotum", *KOREAN["sans"]),
    "함초롬바탕": ("HCR Batang", *KOREAN["serif"]), "바탕": KOREAN["serif"],
    "Batang": KOREAN["serif"], "돋움": KOREAN["sans"], "Dotum": KOREAN["sans"],
    "굴림": KOREAN["sans"], "Gulim": KOREAN["sans"], "Apple SD Gothic Neo": KOREAN["sans"],
}


def family_key(name):
    return "".join(char for char in normalize("NFKC", name).casefold() if char.isalnum())


def font_directories():
    home = Path.home()
    if sys.platform == "darwin":
        directories = [home / "Library/Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts"),
                       Path("/Network/Library/Fonts"),
                       Path("/Applications/Hancom Office HWP Viewer.app/Contents/Resources/Hnc/Shared/TTF/Install")]
    elif os.name == "nt":
        directories = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
        if os.environ.get("LOCALAPPDATA"):
            directories.insert(0, Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Windows/Fonts")
    else:
        directories = [Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "fonts", home / ".fonts",
                       *(Path(directory) / "fonts" for directory in os.environ.get(
                           "XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if directory)]
    extra = [Path(part).expanduser() for part in os.environ.get("DOCVERS_FONT_DIRS", "").split(os.pathsep) if part]
    return tuple(dict.fromkeys(directory.absolute() for directory in [*extra, *directories] if directory.is_dir()))


def faces(path):
    collection = None
    font = None
    try:
        if path.suffix.lower() in (".ttc", ".otc"):
            collection = TTCollection(path, lazy=True)
            fonts = collection.fonts
        else:
            font = TTFont(path, lazy=True)
            fonts = [font]
        for index, item in enumerate(fonts):
            try:
                names = item["name"]
                family = names.getBestFamilyName()
                if family:
                    yield family, [record.toUnicode() for record in names.names if record.nameID in (1, 4, 6, 16, 21)], index
            except (KeyError, UnicodeError, TTLibError, ValueError):
                continue
    except (TTLibError, OSError, ValueError):
        return
    finally:
        if collection is not None:
            collection.close()
        if font is not None:
            font.close()


@lru_cache(maxsize=2)
def scan_fonts(directories, _refresh):
    manifest = {}
    for directory in directories:
        for folder, _, files in os.walk(directory):
            for name in sorted(files):
                path = Path(folder) / name
                if path.suffix.lower() not in FONT_SUFFIXES or path in manifest:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                manifest[path] = (stat.st_mtime_ns, stat.st_size)
    return inventory(tuple(sorted(manifest.items())))


@lru_cache(maxsize=3)
def inventory(manifest):
    names = {}
    families = set()
    for key, entry in face_index(manifest).items():
        names[key] = entry["family"]
        families.add(entry["family"])
    return names, tuple(sorted(families, key=str.casefold)), manifest


@lru_cache(maxsize=3)
def face_index(manifest):
    result = {}
    for path, stats in manifest:
        for family, aliases, index in faces(path):
            entry = {"family": family, "path": str(path), "index": index, "size": stats[1]}
            for alias in (family, *aliases):
                result.setdefault(family_key(alias), entry)
    return result



def installed_fonts():
    # Refresh the file list periodically and only reopen font tables if it changes.
    return scan_fonts(font_directories(), int(monotonic() // 3))


def substitutions_path():
    return DATABASE.parent / "font-substitutions.json"


def substitutions():
    try:
        rules = json.loads(substitutions_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}
    return {source: target for source, target in rules.items()
            if isinstance(source, str) and isinstance(target, str)} if isinstance(rules, dict) else {}


def resolve_missing(name, installed, rules, east_asian=False):
    key = family_key(name)
    if not key or key in installed:
        return None
    configured = {family_key(source): target for source, target in rules.items()}
    equivalents = {family_key(source): choices for source, choices in EQUIVALENTS.items()}
    match = equivalents.get(key, ())
    east_asian = east_asian or any("\uac00" <= char <= "\ud7a3" for char in name)
    if any(word in key for word in ("mono", "courier", "consolas", "fixed")):
        category = "mono"
    elif any(word in key for word in ("times", "cambria", "batang", "바탕", "명조", "mincho")) or (
            "serif" in key and "sans" not in key):
        category = "serif"
    else:
        category = "sans"
    choices = (configured.get(key, ""), *match, *(KOREAN.get(category, ()) if east_asian else ()), *GENERIC[category])
    return next((installed[family_key(choice)] for choice in choices if family_key(choice) in installed), None)



def editor_font_files(requests):
    names, _, manifest = installed_fonts()
    sources = face_index(manifest)
    rules = substitutions()
    result = {}
    for font, east_asian in requests.items():
        if not font:
            continue
        name = font.split("+", 1)[-1] if len(font) > 7 and font[6] == "+" and font[:6].isupper() else font
        key = family_key(name)
        options = [key, family_key(re.sub(r"[- ](?:Regular|Book|Roman|MT)$", "", name))]
        candidates = [sources[option] for option in options if option in sources]
        if not candidates:
            fallback = resolve_missing(name, names, rules, east_asian)
            if fallback is not None and family_key(fallback) in sources:
                candidates.append(sources[family_key(fallback)])
        if candidates:
            result[font] = {**candidates[0], "label": candidates[0]["family"] if key in sources else name}
    return result

def office_font_requests(source):
    requests, embedded = {}, set()
    try:
        with ZipFile(source) as package:
            total = 0
            for item in package.infolist():
                name = item.filename
                if not name.endswith(".xml") or not (
                    name.startswith(("word/", "ppt/", "xl/theme/")) or name in ("xl/styles.xml", "styles.xml", "content.xml")
                ):
                    continue
                if item.file_size > 8 * 1024 * 1024 or total + item.file_size > 32 * 1024 * 1024:
                    continue
                total += item.file_size
                root = safe_xml.fromstring(package.read(item))
                for node in root.iter():
                    kind = node.tag.rsplit("}", 1)[-1]
                    for attr, family in node.attrib.items():
                        key = attr.rsplit("}", 1)[-1]
                        if key in ("typeface", "font-family") or (kind == "rFonts" and key in ("ascii", "hAnsi", "eastAsia", "cs")):
                            family = family.split(",")[0].strip(" '\"")
                            if family and not family.startswith("+") and len(family) <= 160:
                                requests[family] = requests.get(family, False) or key == "eastAsia" or kind == "ea"
                    if kind == "font":
                        family = next((value for attr, value in node.attrib.items() if attr.rsplit("}", 1)[-1] == "name"), "")
                        if family and any(child.tag.rsplit("}", 1)[-1].startswith("embed") for child in node):
                            embedded.add(family_key(family))
                        font_name = node.find("./{*}name")
                        if font_name is not None and font_name.get("val"):
                            requests.setdefault(font_name.get("val"), False)
                    elif kind == "embeddedFont":
                        font = node.find("./{*}font")
                        if font is not None:
                            embedded.add(family_key(font.get("typeface", "")))
                    elif kind == "font-face" and node.find("./{*}font-face-src") is not None:
                        embedded.update(family_key(value.strip(" '\"")) for attr, value in node.attrib.items()
                                        if attr.rsplit("}", 1)[-1] in ("name", "font-family"))
    except (BadZipFile, DefusedXmlException, ET.ParseError, OSError):
        pass
    return requests, embedded


def office_replacements(source):
    installed, _, _ = installed_fonts()
    rules = substitutions()
    requests = dict.fromkeys([*EQUIVALENTS, *rules], False)
    declared, embedded = office_font_requests(source)
    requests.update(declared)
    return {name: replacement for name, east in requests.items()
            if family_key(name) not in embedded
            and (replacement := resolve_missing(name, installed, rules, east)) is not None}


def generic_fallback(kind, *, east_asian=False):
    installed, _, _ = installed_fonts()
    families = (*KOREAN.get(kind, ()), *GENERIC[kind]) if east_asian else GENERIC[kind]
    return next((installed[family_key(name)] for name in families if family_key(name) in installed), None)


def policy_key():
    _, _, manifest = installed_fonts()
    payload = ([(str(path), stats) for path, stats in manifest], substitutions(), os.environ.get("FONTCONFIG_FILE"))
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def font_environment(cache, requests=None):
    installed, _, _ = installed_fonts()
    rules = substitutions()
    root = ET.Element("fontconfig")
    inherited = os.environ.get("FONTCONFIG_FILE", "/etc/fonts/fonts.conf")
    if Path(inherited).is_file():
        ET.SubElement(root, "include", ignore_missing="yes").text = str(Path(inherited).absolute())
    for directory in font_directories():
        ET.SubElement(root, "dir").text = str(directory)
    ET.SubElement(root, "cachedir", prefix="xdg").text = "fontconfig"
    for kind, alternatives in GENERIC.items():
        available = [installed[family_key(name)] for name in (*alternatives, *KOREAN.get(kind, ()))
                     if family_key(name) in installed]
        if not available:
            continue
        generic = {"sans": "sans-serif", "serif": "serif", "mono": "monospace"}[kind]
        alias = ET.SubElement(root, "alias")
        ET.SubElement(alias, "family").text = generic
        preferred = ET.SubElement(alias, "prefer")
        for name in available:
            ET.SubElement(preferred, "family").text = name
    names = dict.fromkeys([*EQUIVALENTS, *rules], False)
    names.update(requests or {})
    for name, east in names.items():
        target = installed.get(family_key(name)) or resolve_missing(name, installed, rules, east)
        if target and name.casefold() != target.casefold():
            alias = ET.SubElement(root, "alias", binding="strong")
            ET.SubElement(alias, "family").text = name
            ET.SubElement(ET.SubElement(alias, "prefer"), "family").text = target
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    key = hashlib.sha256(data).hexdigest()[:16]
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"fontconfig-{key}.conf"
    if not path.is_file():
        with NamedTemporaryFile(dir=cache, delete=False) as stream:
            staged = Path(stream.name)
            try:
                stream.write(data)
                stream.close()
                staged.replace(path)
            finally:
                staged.unlink(missing_ok=True)
    return dict(os.environ, FONTCONFIG_FILE=str(path), RHWP_WEBFONT_OFFLINE="1")
