from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from scripts.telegram_session import is_authenticated, save_session
from scripts.telegram_sync import (
    ImportPreview, PhrasePage, TelegramClient, compare_export, phrase_key, unused_plural_forms, values_by_key, verify_result,
)


class PreviewTests(unittest.TestCase):
    def test_import_groups_do_not_mix_actions_or_languages(self):
        html = '''<div class="tr-change-keys-block"><section><div class="tr-plain-key-row" data-key="New" /></section></div>
<div class="tr-remove-keys-block"><div class="tr-plain-key-row" data-key="Old"></div></div>
<div class="tr-keys-block-empty"></div>'''
        preview = ImportPreview(html, "weba")
        self.assertEqual(preview.actionable(), {"editImportedKeys": ["New"]})
        self.assertEqual(preview.rows["removeImportedKeys"], ["Old"])
        with self.assertRaisesRegex(ValueError, "another language"):
            ImportPreview('<div class="tr-change-keys-block"><div class="tr-plain-key-row" '
                          'data-lang="en" data-key="Key"></div></div>', "weba")

    def test_unknown_import_row_is_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            ImportPreview('<div class="tr-plain-key-row" data-key="Key"></div>', "weba")


class VerificationTests(unittest.TestCase):
    def test_export_comparison_decodes_escapes_but_preserves_significant_whitespace(self):
        expected = '"Key" = "café\\n\\\"ok\\\"";\n"Space" = " padded ";'
        remote = '"Key" = "café\n\\\"ok\\\"";\n"Space" = "padded";'
        result = compare_export(expected, remote, "strings")
        self.assertEqual(result["different_keys"], ["Space"])
        self.assertEqual(result["missing_keys"], [])
        self.assertEqual(values_by_key('<resources><string name="Key">a &amp; b</string></resources>', "xml"),
                         {"Key": "a & b"})

    def test_only_unused_plural_forms_with_exported_siblings_are_excluded(self):
        expected = dict.fromkeys(["Count_one", "Count_other", "Count_few", "Count_many", "Lost_few",
                                  "Ios_1", "Ios_any", "Ios_3_10", "Ios_0", "Ios_many"], "value")
        remote = {key: value for key, value in expected.items() if key.endswith(("_one", "_other", "_1", "_any"))}
        self.assertEqual(unused_plural_forms(expected, remote),
                         ["Count_few", "Count_many", "Ios_0", "Ios_3_10", "Ios_many"])

    def test_protected_rows_are_reported_but_real_mismatches_still_fail(self):
        client = Mock()
        critical = PhrasePage('<div class="tr-key-full-block" data-key="Critical"><span '
                              'data-title="Critical phrases can only be translated by coordinators of official languages"></span></div>')
        normal = PhrasePage('<div class="tr-key-full-block" data-key="Key"></div>')
        client.phrase_details.return_value = {"Critical": (critical, "https://translations.telegram.org/example"),
                                              "Key": (normal, "https://translations.telegram.org/key")}
        target = ("weba", "weba.strings", "strings")
        result = verify_result(client, target, '"Critical" = "lower"; "Key" = "new";',
                               '"Critical" = "UPPER"; "Key" = "old";')
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["unresolved_keys"], ["Key"])
        result = verify_result(client, target, '"Critical" = "lower";', '"Critical" = "UPPER";')
        self.assertEqual(result["status"], "verified_with_exceptions")

    def test_phrase_details_use_applied_values_and_map_plural_resource_keys(self):
        page = PhrasePage('''<div class="tr-key-full-block" data-key="Count">
<div class="key-default-value"><span class="pluralized"></span></div>
<div class="key-suggestion-wrap key-suggestion-applied"><span class="p-value" data-label="one">
<span class="value" data-value="one &amp; only"></span></span></div>
<div class="key-suggestion-wrap"><span class="value" data-value="not applied"></span></div></div>''')
        self.assertTrue(page.plural)
        self.assertEqual(page.applied, {"one": "one & only"})
        self.assertEqual(phrase_key("Count_any", "ios", {"Count_1": "", "Count_any": ""}), ("Count", "other"))
        self.assertEqual(phrase_key("Count_one", "weba", {"Count_one": "", "Count_other": ""}), ("Count", "one"))
        self.assertEqual(phrase_key("Count_one", "weba", {"Count_one": ""}), ("Count_one", "plain"))

    def test_template_and_export_limitations_are_explicit(self):
        absent = PhrasePage('<div class="tr-key-row-empty">This phrase does not exist.</div>')
        plural = PhrasePage('<div class="tr-key-full-block" data-key="Migrated">'
                            '<div class="key-default-value"><span class="pluralized"></span></div></div>')
        applied = PhrasePage('<div class="tr-key-full-block" data-key="Applied">'
                             '<div class="key-suggestion-wrap key-suggestion-applied">'
                             '<span class="value" data-value="correct"></span></div></div>')
        client = Mock()
        client.phrase_details.return_value = {"Gone": (absent, "gone-url"),
                                              "Migrated": (plural, "plural-url"),
                                              "Applied": (applied, "applied-url")}
        result = verify_result(client, ("macos", "macos.strings", "strings"),
                               '"Gone" = "text"; "Migrated" = "old text"; "Applied" = "correct"; "Empty" = ""; "OK" = "ok";',
                               '"Migrated" = "Old Text"; "OK" = "ok";')
        self.assertEqual(result["status"], "verified_with_exceptions")
        self.assertEqual(set(result["template_only_keys"]), {"Gone", "Migrated"})
        self.assertEqual(result["verified_on_site"], {"Applied": "applied-url"})
        self.assertEqual(result["empty_values_omitted"], ["Empty"])


class SessionTests(unittest.TestCase):
    def test_authenticated_page_omits_unauth_instead_of_setting_false(self):
        html = '<script>ajInit({"state":{}});</script><a href="/lang/edit/lowercased-en">Edit</a>'
        self.assertTrue(is_authenticated(html))
        self.assertFalse(is_authenticated(html.replace('"state":{}', '"unauth":true,"state":{}')))
        self.assertFalse(is_authenticated('<script>ajInit({"state":{}});</script>'))

    def test_session_file_contains_only_translations_cookies_and_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth" / "session.json"
            save_session({"cookies": [{"domain": "translations.telegram.org", "value": "session"},
                                       {"domain": "oauth.telegram.org", "value": "exclude"}],
                          "origins": [{"origin": "https://example.com"}]}, path)
            saved = json.loads(path.read_text())
            self.assertEqual(len(saved["cookies"]), 1)
            self.assertEqual(saved["origins"], [])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class ImportProtocolTests(unittest.TestCase):
    def test_server_continuation_and_skipped_keys_are_left_for_readback(self):
        client = TelegramClient(None, Path("unused"))
        client.post = Mock(side_effect=[
            {"lang_keys": ["A"], "repeat_lang_keys": ["B"], "affected_cnt": 1},
            {"lang_keys": ["B"], "repeat_lang_keys": [], "affected_cnt": 2},
        ])
        with redirect_stdout(io.StringIO()):
            applied = client.apply({}, "weba", "id", "editImportedKeys", ["A", "Critical", "B"], 100)
        self.assertEqual(applied, ["A", "B"])
        self.assertEqual(client.post.call_args.kwargs["lang_keys"], "B")
        self.assertEqual(client.post.call_args.kwargs["affected_cnt"], 1)

    def test_unexpected_continuation_and_removal_mutations_are_rejected(self):
        client = TelegramClient(None, Path("unused"))
        client.post = Mock(return_value={"lang_keys": [], "repeat_lang_keys": ["Other"]})
        with self.assertRaisesRegex(ValueError, "Unexpected import continuation"):
            client.apply({}, "weba", "id", "editImportedKeys", ["A"], 100)
        with self.assertRaisesRegex(ValueError, "Unsupported mutation"):
            TelegramClient(None, Path("unused")).post({}, "removeImportedKeys", "weba")


if __name__ == "__main__":
    unittest.main()
