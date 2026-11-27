import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from font_policy import family_key, font_environment, office_replacements, policy_key, resolve_missing
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
