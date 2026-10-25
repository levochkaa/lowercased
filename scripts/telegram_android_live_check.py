#!/usr/bin/env python3
import argparse
import concurrent.futures as cf
import html as html_lib
import json
import re
import socket
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs


QUANTITIES = ("zero", "one", "two", "few", "many", "other")
LABEL_TO_SUFFIX = {
    "zero": "zero",
    "one": "one",
    "two": "two",
    "few": "few",
    "many": "many",
    "other": "other",
}


def load_import_keys(curl_path):
    text = Path(curl_path).read_text()
    match = re.search(r"--data-raw '([^']*)'", text, re.S)
    if not match:
        raise ValueError("Could not find --data-raw in curl")
    parsed = parse_qs(match.group(1), keep_blank_values=True)
    return [key for key in parsed["lang_keys"][0].split(",") if key]


def load_raw_strings(xml_path):
    text = Path(xml_path).read_text()
    strings = {}
    for match in re.finditer(r'<string\s+name="([^"]+)"[^>]*>(.*?)</string>', text, re.S):
        strings[match.group(1)] = match.group(2)
    return strings


def normalize(value):
    if value is None:
        return None
    value = html_lib.unescape(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\\n", "\n")
    value = value.replace('\\"', '"').replace("\\'", "'")
    value = value.replace("&#33;", "!")
    value = re.sub(r"[ \t]+\n", "\n", value)
    return value.strip()


def strip_tags(value):
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return html_lib.unescape(value)


def plural_bases(strings):
    bases = {}
    for name in strings:
        match = re.match(r"^(.*)_(zero|one|two|few|many|other)$", name)
        if match:
            bases.setdefault(match.group(1), set()).add(match.group(2))
    return {base: quantities for base, quantities in bases.items() if len(quantities) >= 2}


def expected_for(key, strings, bases):
    if key in strings:
        return {"plain": normalize(strings[key])}
    if key in bases:
        plural = {}
        for quantity in QUANTITIES:
            name = f"{key}_{quantity}"
            if name in strings:
                plural[quantity] = normalize(strings[name])
        return {"plural": plural}
    return {"missing_local_key": key}


def load_url_map(check_path):
    if not check_path or not Path(check_path).exists():
        return {}
    data = json.loads(Path(check_path).read_text())
    return {row["key"]: row.get("url") for row in data.get("results", []) if row.get("url")}


def resolve_missing_urls(keys, url_by_key):
    sections = [
        "unsorted",
        "general",
        "bots_and_payments",
        "groups_and_channels",
        "settings",
        "private_chats",
        "chat_list",
        "login",
        "profile",
        "stories",
        "camera_and_media",
        "passport",
        "unused",
    ]
    for key in keys:
        if key in url_by_key:
            continue
        for section in sections:
            url = f"https://translations.telegram.org/lowercased-en/android/{section}/{key}"
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=12) as response:
                    body = response.read(2000).decode("utf-8", "replace")
                if f'data-key="{key}"' in body or f">{key}<" in body:
                    url_by_key[key] = url
                    break
            except Exception:
                continue
    return url_by_key


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return response.read().decode("utf-8", "replace")
        except Exception as error:
            last_error = repr(error)
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(last_error)


def attr_value(attrs, name):
    match = re.search(name + r'="([^"]*)"', attrs)
    return html_lib.unescape(match.group(1)) if match else None


def parse_applied(page):
    match = re.search(r'<div class="([^"]*key-suggestion-wrap[^"]*key-suggestion-applied[^"]*)"', page)
    if not match:
        return None
    start = match.start()
    next_wrap = page.find('<div class="key-suggestion-wrap', match.end())
    add_form = page.find('<div class="key-add-suggestion-wrap', match.end())
    end_candidates = [value for value in (next_wrap, add_form) if value != -1]
    block = page[start : min(end_candidates) if end_candidates else len(page)]

    plural = {}
    for plural_match in re.finditer(
        r'<span class="p-value"\s+data-label="([^"]+)"[\s\S]*?<span class="value"([^>]*)>',
        block,
    ):
        label = plural_match.group(1)
        value = attr_value(plural_match.group(2), "data-value")
        if value is not None:
            plural[LABEL_TO_SUFFIX.get(label, label)] = normalize(value)
    if plural:
        return {"plural": plural, "source": "applied"}

    values = []
    for value_match in re.finditer(r'<span class="value"([^>]*)>', block):
        value = attr_value(value_match.group(1), "data-value")
        if value is not None:
            values.append(normalize(value))
    if values:
        return {"plain": values[0], "source": "applied"}
    return None


def parse_default(page):
    match = re.search(r'<div class="key-default-value">([\s\S]*?)<div class="key-default-info">', page)
    if not match:
        binding_match = re.search(r'<div class="binding-items"[^>]*data-value="([^"]*)"', page)
        if binding_match:
            return {"plain": normalize(binding_match.group(1)), "source": "binding-default"}
        return {"error": "default_not_found"}

    block = match.group(1)
    plural = {}
    for plural_match in re.finditer(
        r'<span class="p-value"\s+data-label="([^"]+)"[\s\S]*?<span class="value">([\s\S]*?)</span>',
        block,
    ):
        label = plural_match.group(1)
        plural[LABEL_TO_SUFFIX.get(label, label)] = normalize(strip_tags(plural_match.group(2)))
    if plural:
        return {"plural": plural, "source": "default"}

    value_match = re.search(r'<span class="value">([\s\S]*?)</span>', block)
    if value_match:
        return {"plain": normalize(strip_tags(value_match.group(1))), "source": "default"}

    binding_match = re.search(r'<div class="binding-items"[^>]*data-value="([^"]*)"', page)
    if binding_match:
        return {"plain": normalize(binding_match.group(1)), "source": "binding-default"}
    return {"error": "value_not_found"}


def compare_expected_remote(expected, remote):
    if "error" in remote:
        return False
    if "plain" in expected:
        return remote.get("plain") == expected["plain"]
    if "plural" in expected:
        remote_plural = remote.get("plural") or {}
        if not remote_plural:
            return False
        return all(remote_plural.get(label) == expected["plural"].get(label) for label in remote_plural)
    return False


def check_one(item):
    key, url, expected = item
    if not url:
        return {"key": key, "status": "error", "error": "url_not_found", "expected": expected}
    try:
        page = fetch(url)
        remote = parse_applied(page) or parse_default(page)
        source = remote.pop("source", "error") if isinstance(remote, dict) else "error"
        ok = compare_expected_remote(expected, remote)
        return {
            "key": key,
            "status": "ok" if ok else "mismatch",
            "source": source,
            "url": url,
            "expected": expected,
            "remote": remote,
        }
    except Exception as error:
        return {"key": key, "status": "error", "url": url, "error": repr(error), "expected": expected}


def summarize(keys, results):
    mismatches = [row for row in results if row["status"] == "mismatch"]
    errors = [row for row in results if row["status"] == "error"]
    summary = {
        "total": len(keys),
        "ok": sum(1 for row in results if row["status"] == "ok"),
        "mismatches": len(mismatches),
        "errors": len(errors),
        "mismatch_sources": {},
        "plural_mismatches": sum(1 for row in mismatches if "plural" in row.get("expected", {})),
        "plain_mismatches": sum(1 for row in mismatches if "plain" in row.get("expected", {})),
    }
    for row in mismatches:
        source = row.get("source", "")
        summary["mismatch_sources"][source] = summary["mismatch_sources"].get(source, 0) + 1
    return summary, mismatches, errors


def main():
    parser = argparse.ArgumentParser(description="Live-check Telegram Android import rows against local android.xml.")
    parser.add_argument("--curl", required=True, help="Copied import curl containing lang_keys")
    parser.add_argument("--xml", default="lowercased-en/android.xml", help="Local lowercased Android XML")
    parser.add_argument("--previous-check", default="/tmp/lowercased_android_check_after_resend.json")
    parser.add_argument("--output", default="/tmp/lowercased_android_live_check.json")
    parser.add_argument("--mismatches-output", default="/tmp/lowercased_android_live_mismatches.json")
    parser.add_argument("--keys-json", help="Optional JSON list of keys to check instead of all import keys")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    socket.setdefaulttimeout(25)
    keys = json.loads(Path(args.keys_json).read_text()) if args.keys_json else load_import_keys(args.curl)
    strings = load_raw_strings(args.xml)
    bases = plural_bases(strings)
    url_by_key = resolve_missing_urls(keys, load_url_map(args.previous_check))
    items = [(key, url_by_key.get(key), expected_for(key, strings, bases)) for key in keys]

    results = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(check_one, item) for item in items]
        for index, future in enumerate(cf.as_completed(futures), 1):
            results.append(future.result())
            if index % 500 == 0:
                print(f"checked {index}/{len(items)}", flush=True)

    order = {key: index for index, key in enumerate(keys)}
    results.sort(key=lambda row: order.get(row["key"], len(order)))
    summary, mismatches, errors = summarize(keys, results)
    Path(args.output).write_text(json.dumps({"summary": summary, "results": results}, indent=2, ensure_ascii=False))
    Path(args.mismatches_output).write_text(json.dumps(mismatches, indent=2, ensure_ascii=False))

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if mismatches:
        print("sample mismatches:")
        for row in mismatches[:15]:
            print(row["key"], row.get("source"), row.get("expected"), row.get("remote"))
    if errors:
        print("sample errors:")
        for row in errors[:10]:
            print(row["key"], row.get("error"))


if __name__ == "__main__":
    main()
