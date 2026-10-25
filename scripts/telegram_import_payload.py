#!/usr/bin/env python3
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlencode


DATA_RE = re.compile(r"--data-raw '([^']*)'", re.DOTALL)
QUANTITY_SUFFIXES = ("zero", "one", "two", "few", "many", "other")
QUANTITY_RE = re.compile(r"^(.*)_(zero|one|two|few|many|other)$")


def load_data_raw(curl_path):
    text = Path(curl_path).read_text()
    match = DATA_RE.search(text)
    if not match:
        raise ValueError("Could not find --data-raw '...' in pasted curl")
    return text, match.group(1)


def load_android_names(xml_path):
    root = ET.parse(xml_path).getroot()
    return {node.attrib["name"] for node in root if node.tag == "string"}


def plural_base_map(names):
    bases = {}
    for name in names:
        match = QUANTITY_RE.match(name)
        if not match:
            continue
        base, quantity = match.groups()
        bases.setdefault(base, set()).add(quantity)

    return {base: quantities for base, quantities in bases.items() if len(quantities) >= 2}


def expand_plural_keys(keys, names, base_map):
    expanded = []
    plural_bases = []

    for key in keys:
        if key in base_map and key not in names:
            plural_bases.append(key)
            expanded.extend(f"{key}_{suffix}" for suffix in QUANTITY_SUFFIXES if f"{key}_{suffix}" in names)
        else:
            expanded.append(key)

    return expanded, plural_bases


def rewrite_data(data_raw, keys):
    parsed = parse_qs(data_raw, keep_blank_values=True)
    flat = {key: values[0] for key, values in parsed.items()}
    flat["lang_keys"] = ",".join(keys)
    return urlencode(flat)


def main():
    parser = argparse.ArgumentParser(
        description="Rewrite translations.telegram.org import curl payloads for Android plural-key debugging."
    )
    parser.add_argument("curl_path", help="Path to a copied curl command")
    parser.add_argument(
        "--android-xml",
        default="lowercased-en/android.xml",
        help="Android XML file used to discover plural variants",
    )
    parser.add_argument(
        "--mode",
        choices=("report", "drop-plurals", "expand-plurals"),
        default="report",
        help="report only, remove plural-base rows, or expand plural-base rows to *_zero/*_one/etc.",
    )
    parser.add_argument(
        "--print-curl",
        action="store_true",
        help="Print the full curl with rewritten --data-raw instead of only the encoded payload.",
    )
    args = parser.parse_args()

    curl_text, data_raw = load_data_raw(args.curl_path)
    parsed = parse_qs(data_raw, keep_blank_values=True)
    keys = parsed.get("lang_keys", [""])[0].split(",") if parsed.get("lang_keys") else []
    names = load_android_names(args.android_xml)
    base_map = plural_base_map(names)
    expanded, plural_bases = expand_plural_keys(keys, names, base_map)
    exact_keys = [key for key in keys if key not in plural_bases]

    print(f"lang_pack={parsed.get('lang_pack', [''])[0]}", file=sys.stderr)
    print(f"lang={parsed.get('lang', [''])[0]}", file=sys.stderr)
    print(f"import_id={parsed.get('import_id', [''])[0]}", file=sys.stderr)
    print(f"original_keys={len(keys)}", file=sys.stderr)
    print(f"plural_base_keys={len(plural_bases)}", file=sys.stderr)
    print(f"without_plural_keys={len(exact_keys)}", file=sys.stderr)
    print(f"expanded_keys={len(expanded)}", file=sys.stderr)

    if args.mode == "report":
        for key in plural_bases:
            variants = [f"{key}_{suffix}" for suffix in QUANTITY_SUFFIXES if f"{key}_{suffix}" in names]
            print(f"{key}: {','.join(variants)}")
        return

    rewritten_keys = exact_keys if args.mode == "drop-plurals" else expanded
    rewritten_data = rewrite_data(data_raw, rewritten_keys)

    if args.print_curl:
        print(DATA_RE.sub(f"--data-raw '{rewritten_data}'", curl_text))
    else:
        print(rewritten_data)


if __name__ == "__main__":
    main()
