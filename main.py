#!/usr/bin/env python3
"""Generate lowercase Telegram language packs, optionally refreshing English exports."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent
# (Telegram language pack, output filename, format)
TARGETS = [
    ("android", "android.xml", "xml"),
    ("android_x", "androidx.xml", "xml"),
    ("tdesktop", "desktop.strings", "strings"),
    ("ios", "ios.strings", "strings"),
    ("macos", "macos.strings", "strings"),
    ("unigram", "unigram.xml", "xml"),
    ("weba", "weba.strings", "strings"),
    ("webk", "webk.strings", "strings"),
]

XML_STRING_RE = re.compile(
    r"(?P<comment><!--.*?-->)|(?P<start><string\b[^>]*>)(?P<value>.*?)(?P<end></string>)",
    re.DOTALL,
)
XML_NAME_RE = re.compile(r"\bname\s*=\s*([\"'])(.*?)\1")
CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
STRINGS_TOKEN_RE = re.compile(
    r'(?P<comment>//[^\r\n]*|/\*.*?\*/)|'
    r'(?P<start>"(?P<key>(?:\\.|[^"\\])*)"\s*=\s*")'
    r'(?P<value>(?:\\.|[^"\\])*)(?P<end>"[ \t]*;)|'
    r'(?P<space>\s+|\ufeff)',
    re.DOTALL,
)
# These tokens are interpreted by clients and may be case-sensitive.
PROTECTED_RE = re.compile(
    r"\\U[0-9A-Fa-f]{4}(?:[0-9A-Fa-f]{4})?|\\u[0-9A-Fa-f]{4}|\\.|"
    r"\{[^{}\r\n]+\}|"
    r"%(?:\d+\$)?[-+#0 ']*(?:\d+|\*)?(?:\.(?:\d+|\*))?"
    r"(?:hh|ll|[hlLzjt])?(?:[diuoxXfFeEgGaAcCsSpn@%]|[tT][A-Za-z])|"
    r"(?:https?://|tg://|mailto:)[^\s<>\"'\\)\]]+|"
    r"<!--.*?-->|</?[A-Za-z][^>]*>|&(?:[A-Za-z][A-Za-z0-9]+|#\d+|#x[0-9A-Fa-f]+);",
    re.DOTALL,
)
DATE_KEY_RE = re.compile(
    r"formatter\w*|chat(?:Full)?Date(?:Short)?|formatDateSchedule\w*|"
    r"(?:Send|Start|Starts|Publish|StartShort|Remind)(?:Today|Day|DayYear)At|"
    r"NewPoll\.DateFormat"
)
DATE_LITERAL_RE = re.compile(r"(?P<quote>\\?')(?P<literal>.*?)(?P=quote)", re.DOTALL)


def lowercase_text(value):
    parts = []
    cursor = 0
    for match in PROTECTED_RE.finditer(value):
        parts.extend((value[cursor:match.start()].lower(), match.group(0)))
        cursor = match.end()
    parts.append(value[cursor:].lower())
    return "".join(parts)


def lowercase_value(key, value):
    if DATE_KEY_RE.fullmatch(key):
        # M means month and m means minute; only quoted date-format literals are prose.
        return DATE_LITERAL_RE.sub(
            lambda m: m["quote"] + lowercase_text(m["literal"]) + m["quote"], value
        )
    return lowercase_text(value)


def lowercase_xml_value(key, value):
    parts = []
    cursor = 0
    for match in CDATA_RE.finditer(value):
        parts.append(lowercase_value(key, value[cursor:match.start()]))
        parts.append(f"<![CDATA[{lowercase_value(key, match.group(1))}]]>")
        cursor = match.end()
    parts.append(lowercase_value(key, value[cursor:]))
    return "".join(parts)


def strings_tokens(text):
    cursor = 0
    while cursor < len(text):
        match = STRINGS_TOKEN_RE.match(text, cursor)
        if not match:
            line = text.count("\n", 0, cursor) + 1
            raise ValueError(f"Invalid .strings syntax at line {line}")
        yield match
        cursor = match.end()


def entry_keys(text, fmt):
    if fmt == "xml":
        root = ET.fromstring(text)
        if root.tag != "resources" or any(node.tag != "string" for node in root):
            raise ValueError("Expected a Telegram XML export containing <string> resources")
        keys = [node.attrib["name"] for node in root]
    else:
        keys = [match["key"] for match in strings_tokens(text) if match["key"] is not None]
    if not keys:
        raise ValueError("The language pack contains no strings")
    # Upstream exports contain duplicate keys. Preserve their order and multiplicity.
    return keys


def transform(text, fmt):
    # Match the original script's universal-newline output on every platform.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    keys = entry_keys(text, fmt)
    if fmt == "xml":
        def replace(match):
            if match["comment"] is not None:
                return match.group(0)
            key = XML_NAME_RE.search(match["start"]).group(2)
            value = lowercase_xml_value(key, match["value"])
            return match["start"] + value + match["end"]

        result = XML_STRING_RE.sub(replace, text)
    else:
        result = "".join(
            match.group(0) if match["key"] is None else
            match["start"] + lowercase_value(match["key"], match["value"]) + match["end"]
            for match in strings_tokens(text)
        )
    if entry_keys(result, fmt) != keys:
        raise ValueError("Conversion changed the language pack's keys")
    return result, len(keys)


def source_files(directory, pack, fmt):
    return sorted(
        path for path in directory.glob(f"{pack}_en_v*.{fmt}")
        if re.fullmatch(rf"{re.escape(pack)}_en_v\d+\.{fmt}", path.name)
    )


def find_file(directory, pack, fmt):
    matches = source_files(directory, pack, fmt)
    if len(matches) != 1:
        raise ValueError(f"Expected one English export for {pack}, found {len(matches)}. Run --update.")
    return matches[0]


def download_export(target):
    pack, _, fmt = target
    url = f"https://translations.telegram.org/en/{pack}/export"
    request = Request(url, headers={"User-Agent": "lowercased-localizations/1.0"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=45) as response:
                filename = response.headers.get_filename()
                if not filename or not re.fullmatch(rf"{pack}_en_v\d+\.{fmt}", filename):
                    raise ValueError(f"{pack}: unexpected export filename {filename!r}")
                data = response.read()
            generated, count = transform(data.decode("utf-8"), fmt)
            return target, filename, data, generated, count
        except (URLError, TimeoutError) as error:
            if isinstance(error, HTTPError) and error.code < 500 and error.code != 429:
                raise
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def write_atomic(path, data):
    if path.exists() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".localization-", delete=False) as file:
        temporary_path = Path(file.name)
        try:
            file.write(data)
            file.close()
            temporary_path.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)


def run(root=ROOT, *, update=False, check=False):
    en_dir = root / "en"
    output_dir = root / "lowercased-en"
    if update:
        print("Downloading current English exports from translations.telegram.org…", flush=True)
        # Validate every download before changing any repository file.
        with ThreadPoolExecutor(max_workers=4) as pool:
            exports = list(pool.map(download_export, TARGETS))
    else:
        exports = []
        for target in TARGETS:
            pack, _, fmt = target
            source = find_file(en_dir, pack, fmt)
            data = source.read_bytes()
            generated, count = transform(data.decode("utf-8"), fmt)
            exports.append((target, source.name, data, generated, count))

    stale = []
    for (pack, output_name, fmt), filename, data, generated, count in exports:
        output_path = output_dir / output_name
        output_data = generated.encode("utf-8")
        if check:
            if not output_path.exists() or output_path.read_bytes() != output_data:
                stale.append(output_name)
        else:
            if update:
                source = en_dir / filename
                write_atomic(source, data)
                for previous in source_files(en_dir, pack, fmt):
                    if previous != source:
                        previous.unlink()
            write_atomic(output_path, output_data)
        print(f"{filename} -> lowercased-en/{output_name}: {count} entries")
    if stale:
        raise ValueError("Outdated generated files: " + ", ".join(stale))
    print("All 8 language packs verified." if check else "All 8 language packs generated.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--update", action="store_true", help="Download current English exports, then regenerate")
    mode.add_argument("--check", action="store_true", help="Validate local exports and generated files without writing")
    args = parser.parse_args()
    try:
        run(update=args.update, check=args.check)
    except (OSError, ValueError, ET.ParseError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
