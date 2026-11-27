import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from desktop.editor_fonts import baselines, font_spans
from font_policy import editor_font_files, family_key, font_environment, office_replacements, policy_key, resolve_missing
from preview import office_profile


class FontPolicyTests(unittest.TestCase):
    installed = {family_key(name): name for name in ("Arial", "Apple SD Gothic Neo", "Times New Roman", "Courier New")}

    def test_preserves_installed_font_and_selects_script_fallback(self):
        rules = {"Arial": "Courier New", "Missing Face": "Courier New"}
        self.assertIsNone(resolve_missing("ARIAL", self.installed, rules))
        self.assertEqual(resolve_missing("Missing Face", self.installed, rules), "Courier New")
        self.assertEqual(resolve_missing("Missing CJK", self.installed, {}, True), "Apple SD Gothic Neo")
        self.assertEqual(resolve_missing("Missing Serif", self.installed, {}), "Times New Roman")

    def test_office_profile_uses_declared_fonts_but_preserves_embedded_families(self):
        with tempfile.TemporaryDirectory() as folder, patch("font_policy.installed_fonts", return_value=(self.installed, (), ())), \
                patch("font_policy.substitutions", return_value={"Calibri": "Courier New"}):
            path = Path(folder) / "test.docx"
            with ZipFile(path, "w") as writer:
                writer.writestr("word/document.xml", '<document xmlns:w="urn:word"><w:rFonts w:ascii="Arial" w:eastAsia="Missing CJK"/></document>')
                writer.writestr("word/fontTable.xml", '<fonts xmlns:w="urn:word"><w:font w:name="Calibri"><w:embedRegular/></w:font></fonts>')
            choices = office_replacements(path)
            self.assertEqual(choices["Missing CJK"], "Apple SD Gothic Neo")
            self.assertNotIn("Calibri", choices)
            self.assertNotIn("Arial", choices)
            settings = office_profile(Path(folder), choices) / "user/registrymodifications.xcu"
            content = settings.read_text(encoding="utf-8")
            self.assertIn("MacroSecurityLevel", content)
            self.assertIn("FontPairs", content)
            self.assertIn("Apple SD Gothic Neo", content)

    def test_editor_resolves_rendered_font_face_and_installed_fallback(self):
        regular = {"family": "Arial", "path": "/fonts/arial.ttc", "index": 0, "size": 20}
        bold = dict(regular, index=2)
        courier = {"family": "Courier New", "path": "/fonts/courier.ttf", "index": 0, "size": 25}
        faces = {"arial": regular, "arialbold": bold, "couriernew": courier}
        with patch("font_policy.installed_fonts", return_value=(self.installed, (), ())), \
                patch("font_policy.face_index", return_value=faces), \
                patch("font_policy.substitutions", return_value={"Missing Face": "Courier New"}):
            chosen = editor_font_files({"ABCDEF+Arial-Bold": False, "Missing Face": False})
        self.assertEqual(chosen["ABCDEF+Arial-Bold"]["index"], 2)
        self.assertEqual(chosen["Missing Face"]["path"], courier["path"])

    def test_editor_retains_mixed_font_spans_across_spaces(self):
        korean = ("HCR Batang", 15, (32, 32, 32))
        latin = ("Arial", 12, (200, 20, 20))
        self.assertEqual(font_spans("한글 A", [korean, korean, latin]),
                         [[0, *korean], [3, *latin]])

    def test_editor_keeps_pdf_baselines_at_source_character_offsets(self):
        glyphs = [
            ("한", None, None, (.13, .25), 0),
            ("글", None, None, (.18, .25), 0),
            ("A", None, None, (.22, .31), 1),
        ]
        self.assertEqual(baselines(" 한글 A", glyphs), [[1, .13, .25], [4, .22, .31]])
        self.assertEqual(baselines("한글", glyphs), [])

    def test_fontconfig_and_cache_key_change_with_user_rules(self):
        with tempfile.TemporaryDirectory() as folder, patch("font_policy.installed_fonts", return_value=(self.installed, (), ())), \
                patch("font_policy.font_directories", return_value=(Path(folder),)), \
                patch("font_policy.substitutions", return_value={"Missing Face": "Courier New"}) as rules:
            env = font_environment(Path(folder))
            aliases = ElementTree.parse(env["FONTCONFIG_FILE"]).getroot().findall("alias")
            self.assertEqual(next(a.findtext("prefer/family") for a in aliases if a.findtext("family") == "Missing Face"),
                             "Courier New")
            before = policy_key()
            rules.return_value = {"Missing Face": "Arial"}
            self.assertNotEqual(before, policy_key())
            self.assertNotEqual(env["FONTCONFIG_FILE"], font_environment(Path(folder))["FONTCONFIG_FILE"])
