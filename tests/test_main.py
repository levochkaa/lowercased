from contextlib import redirect_stdout
from email.message import Message
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import main


class TransformTests(unittest.TestCase):
    def test_placeholders_links_and_escapes_keep_their_case(self):
        source = r'CRAFT {giftName}: %1$@ %2$08X %3$tH \U00C9 \u00AF https://example.com/Path?ID=ABC'
        expected = r'craft {giftName}: %1$@ %2$08X %3$tH \U00C9 \u00AF https://example.com/Path?ID=ABC'
        self.assertEqual(main.lowercase_text(source), expected)

    def test_date_patterns_only_lowercase_literals(self):
        self.assertEqual(main.lowercase_value("formatterMonth", "MMM dd"), "MMM dd")
        self.assertEqual(main.lowercase_value("chatDate", "MMMM d"), "MMMM d")
        self.assertEqual(main.lowercase_value("SendDayAt", r"\'Send on\' MMM d \'at\' HH:mm"),
                         r"\'send on\' MMM d \'at\' HH:mm")
        self.assertEqual(main.lowercase_value("NewPoll.DateFormat", "d MMM 'AT' HH:mm"),
                         "d MMM 'at' HH:mm")
        self.assertEqual(main.lowercase_value("Message.ImportedDateFormat", "%@ Imported %@"),
                         "%@ imported %@")

    def test_strings_preserve_comments_duplicates_and_multiline_values(self):
        source = '// "Ignored" = "UPPER";\r\n"Key" = "FIRST\\nLINE"; // Keep This\n'
        source += '/* "IgnoredToo" = "UPPER"; */\n"Key" = "SECOND\nLINE";'
        expected = '// "Ignored" = "UPPER";\n"Key" = "first\\nline"; // Keep This\n'
        expected += '/* "IgnoredToo" = "UPPER"; */\n"Key" = "second\nline";'
        output, count = main.transform(source, "strings")
        self.assertEqual(output, expected)
        self.assertEqual(count, 2)
        self.assertEqual(main.transform(output, "strings")[0], output)

    def test_xml_preserves_cdata_markup_and_comments(self):
        source = '''<resources>
<!-- <string name="Ignored">UPPER</string> -->
<string name="Key"><![CDATA[<a href="https://example.com/FAQ">READ FAQ</a>]]> &amp; MORE</string>
<string name="Count_one">ONE {giftName}</string>
<string name="Count_other">MANY {giftName}</string>
</resources>'''
        output, count = main.transform(source, "xml")
        self.assertEqual(count, 3)
        self.assertIn('<!-- <string name="Ignored">UPPER</string> -->', output)
        self.assertIn('<![CDATA[<a href="https://example.com/FAQ">read faq</a>]]> &amp; more', output)
        self.assertIn('name="Count_other">many {giftName}', output)
        self.assertEqual(main.transform(output, "xml")[0], output)

    def test_invalid_or_empty_exports_are_rejected(self):
        for source, fmt in [('"Key" = "Missing semicolon"', "strings"),
                            ('<html>Login</html>', "xml"), ('// Empty', "strings")]:
            with self.subTest(fmt=fmt), self.assertRaises(ValueError):
                main.transform(source, fmt)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "en" / "ios_en_v1.strings"
        self.source.parent.mkdir()
        self.source.write_text('"Key" = "OLD";')
        self.output = self.root / "lowercased-en" / "ios.strings"
        self.output.parent.mkdir()
        self.output.write_text('"Key" = "old";')
        self.target = ("ios", "ios.strings", "strings")

    def test_failed_download_leaves_all_existing_files_untouched(self):
        with patch.object(main, "TARGETS", [self.target]), \
             patch.object(main, "download_export", side_effect=ValueError("Invalid export")), \
             redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            main.run(self.root, update=True)
        self.assertEqual(self.source.read_text(), '"Key" = "OLD";')
        self.assertEqual(self.output.read_text(), '"Key" = "old";')

    def test_update_replaces_old_version_and_offline_check_detects_stale_output(self):
        export = (self.target, "ios_en_v2.strings", b'"Key" = "NEW";', '"Key" = "new";', 1)
        with patch.object(main, "TARGETS", [self.target]), \
             patch.object(main, "download_export", return_value=export), redirect_stdout(io.StringIO()):
            main.run(self.root, update=True)
            self.assertFalse(self.source.exists())
            self.assertEqual(self.output.read_text(), '"Key" = "new";')
            main.run(self.root, check=True)
            self.output.write_text('"Key" = "stale";')
            with self.assertRaisesRegex(ValueError, "Outdated"):
                main.run(self.root, check=True)
            self.assertEqual(self.output.read_text(), '"Key" = "stale";')

    def test_download_rejects_unexpected_server_filename(self):
        headers = Message()
        headers['Content-Disposition'] = 'attachment; filename="../ios_en_v2.strings"'
        with patch.object(main, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.headers = headers
            with self.assertRaisesRegex(ValueError, "unexpected export filename"):
                main.download_export(self.target)


if __name__ == "__main__":
    unittest.main()
