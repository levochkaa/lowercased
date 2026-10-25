#!/usr/bin/env python3
"""Refresh, import and verify lowercased-en on translations.telegram.org."""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.message import Message
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main as localization
from scripts.telegram_session import SITE, STATE_PATH, page_config, save_session

LANG = "lowercased-en"
BLOCK_METHODS = {
    "tr-change-keys-block": "editImportedKeys",
    "tr-add-keys-block": "addImportedKeys",
    "tr-restore-keys-block": "restoreImportedKeys",
    "tr-remove-keys-block": "removeImportedKeys",
}
ALLOWED_METHODS = {"editImportedKeys", "addImportedKeys", "restoreImportedKeys"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
QUANTITIES = ("zero", "one", "two", "few", "many", "other")
ESCAPE_RE = re.compile(r"\\(?:U[0-9A-Fa-f]{4}(?:[0-9A-Fa-f]{4})?|u[0-9A-Fa-f]{4}|[0-7]{1,3}|.)", re.S)


class ImportPreview(HTMLParser):
    def __init__(self, text, pack):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.rows = defaultdict(list)
        self.pack = pack
        self.synchronized = False
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        classes = set(attrs.get("class", "").split())
        parent = self.stack[-1][1] if self.stack else None
        method = next((BLOCK_METHODS[name] for name in BLOCK_METHODS if name in classes), parent)
        if "tr-keys-block-empty" in classes:
            self.synchronized = True
        if "tr-plain-key-row" in classes:
            if not method or not attrs.get("data-key"):
                raise ValueError("Unrecognized Telegram import preview row")
            if attrs.get("data-lang", LANG) != LANG or attrs.get("data-langpack", self.pack) != self.pack:
                raise ValueError("Import preview contains another language or platform")
            self.rows[method].append(attrs["data-key"])
        if tag not in VOID_TAGS:
            self.stack.append((tag, method))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def actionable(self):
        return {method: list(dict.fromkeys(keys)) for method, keys in self.rows.items()
                if method in ALLOWED_METHODS}


class PhrasePage(HTMLParser):
    """Read the actual phrase shape and applied values, without relying on suggestions."""
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.key = None
        self.critical = False
        self.plural = False
        self.applied = {}
        self.not_found = '<div class="tr-key-row-empty">This phrase does not exist.</div>' in text
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        classes = set(attrs.get("class", "").split())
        state = dict(self.stack[-1][1]) if self.stack else {}
        if "tr-key-full-block" in classes:
            self.key = attrs.get("data-key")
        if attrs.get("data-title") == "Critical phrases can only be translated by coordinators of official languages":
            self.critical = True
        if "key-default-value" in classes:
            state["default"] = True
        if "pluralized" in classes and state.get("default"):
            self.plural = True
        if "key-suggestion-wrap" in classes:
            state["applied"] = "key-suggestion-applied" in classes
        if "p-value" in classes:
            state["quantity"] = attrs.get("data-label", "plain")
        if "value" in classes and state.get("applied") and "data-value" in attrs:
            self.applied[state.get("quantity", "plain")] = attrs["data-value"].replace("\r\n", "\n")
        if tag not in VOID_TAGS:
            self.stack.append((tag, state))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def phrase_key(key, pack, expected):
    suffixes = {"0": "zero", "1": "one", "2": "two", "3_10": "few", "many": "many", "any": "other"} if pack == "ios" else {
        quantity: quantity for quantity in QUANTITIES
    }
    separators = ("#",) if pack == "tdesktop" else ("_",)
    for separator in separators:
        for suffix, quantity in suffixes.items():
            if key.endswith(separator + suffix):
                base = key[:-len(separator + suffix)]
                siblings = sum(base + separator + candidate in expected for candidate in suffixes)
                if siblings >= 2:
                    return base, quantity
    return key, "plain"


def decode_escapes(value):
    escapes = {"n": "\n", "r": "\r", "t": "\t", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
               "'": "'", '"': '"', "\\": "\\"}

    def replace(match):
        token = match.group(0)[1:]
        if token in escapes:
            return escapes[token]
        if token[0] in "uU" and len(token) in (5, 9):
            return chr(int(token[1:], 16))
        if token.isdigit() and all(char in "01234567" for char in token):
            return chr(int(token, 8))
        return match.group(0)

    return ESCAPE_RE.sub(replace, value).replace("\r\n", "\n").replace("\r", "\n")


def values_by_key(text, fmt):
    localization.entry_keys(text, fmt)
    if fmt == "xml":
        pairs = [(node.attrib["name"], "".join(node.itertext())) for node in ET.fromstring(text)]
    else:
        pairs = [(match["key"], match["value"]) for match in localization.strings_tokens(text)
                 if match["key"] is not None]
    return {key: decode_escapes(value) for key, value in pairs}


def compare_export(expected_text, remote_text, fmt):
    expected = values_by_key(expected_text, fmt)
    remote = values_by_key(remote_text, fmt)
    missing = sorted(expected.keys() - remote.keys())
    different = sorted(key for key in expected.keys() & remote.keys() if expected[key] != remote[key])
    return {"expected_keys": len(expected), "exported_keys": len(remote),
            "matching_keys": len(expected.keys() & remote.keys()) - len(different),
            "missing_keys": missing, "different_keys": different,
            "extra_keys": sorted(remote.keys() - expected.keys())}


def unused_plural_forms(expected, remote):
    """English packs export one/other (iOS: 1/any), not every template category."""
    unused = []
    for key in expected.keys() - remote.keys():
        match = re.fullmatch(r"(.+)([_#])(zero|two|few|many)", key)
        required = None
        if match:
            base, separator, _ = match.groups()
            required = [base + separator + suffix for suffix in ("one", "other")]
        if not required or not all(name in expected and name in remote for name in required):
            match = re.fullmatch(r"(.+)_(0|2|3_10|many)", key)
            if match:
                required = [match[1] + suffix for suffix in ("_1", "_any")]
        if required and all(name in expected and name in remote for name in required):
            unused.append(key)
    return sorted(unused)


class TelegramClient:
    def __init__(self, request, report_dir):
        self.request = request
        self.report_dir = report_dir
        self.phrase_cache = {}

    def get(self, path):
        url = urljoin(SITE, path)
        if urlparse(url).netloc != "translations.telegram.org":
            raise ValueError("Refusing a URL outside translations.telegram.org")
        for attempt in range(3):
            response = self.request.get(url, timeout=60000)
            if response.ok:
                return response
            if response.status < 500 and response.status != 429:
                break
            time.sleep(2 ** attempt)
        raise RuntimeError(f"Telegram GET {urlparse(url).path}: HTTP {response.status}")

    def config(self, pack):
        html = self.get(f"/{LANG}/{pack}/import").text()
        config = page_config(html)
        if config.get("unauth"):
            raise RuntimeError("Telegram session expired. Run scripts/telegram_session.py again.")
        state = config.get("state", {})
        if state.get("curLang") != LANG or state.get("curLangpack") != pack:
            raise ValueError("Telegram import page has an unexpected language or platform")
        if not re.fullmatch(r"/api\?hash=[A-Za-z0-9]+", config.get("apiUrl", "")):
            raise ValueError("Unexpected Telegram API endpoint")
        return config

    def post(self, config, method, pack, **fields):
        if method not in ALLOWED_METHODS | {"importFile"}:
            raise ValueError(f"Unsupported mutation: {method}")
        payload = {"method": method, "lang": LANG, "lang_pack": pack, **fields}
        arguments = {"multipart" if "file" in fields else "form": payload}
        response = self.request.post(SITE + config["apiUrl"], timeout=60000,
                                     headers={"Origin": SITE, "Referer": f"{SITE}/{LANG}/{pack}/import"},
                                     **arguments)
        if not response.ok:
            raise RuntimeError(f"Telegram {method} for {pack}: HTTP {response.status}; rerun to reconcile")
        result = response.json()
        with (self.report_dir / "api-results.jsonl").open("a", encoding="utf-8") as file:
            json.dump({"method": method, "platform": pack,
                       "submitted": fields.get("lang_keys", "").split(",") if "lang_keys" in fields else [],
                       "result": {key: value for key, value in result.items() if key not in ("html", "_dlog")}}, file)
            file.write("\n")
        if result.get("error"):
            raise RuntimeError(f"Telegram {method} for {pack}: {result['error']}")
        return result

    def preview(self, target, text):
        pack, output, fmt = target
        config = self.config(pack)
        result = self.post(config, "importFile", pack, file={
            "name": output, "mimeType": "text/xml" if fmt == "xml" else "application/octet-stream",
            "buffer": text.encode("utf-8"),
        })
        if not result.get("import_id") or not isinstance(result.get("html"), str):
            raise ValueError(f"{pack}: Telegram returned no import preview")
        preview = ImportPreview(result["html"], pack)
        if not preview.rows and not preview.synchronized:
            raise ValueError(f"{pack}: Unrecognized empty import preview")
        (self.report_dir / f"{pack}-preview.html").write_text(result["html"], encoding="utf-8")
        return config, result["import_id"], preview

    def apply(self, config, pack, import_id, method, keys, batch_size):
        applied = []
        for start in range(0, len(keys), batch_size):
            pending = keys[start:start + batch_size]
            affected = 0
            for attempt in range(20):
                result = self.post(config, method, pack, import_id=import_id,
                                   lang_keys=",".join(pending), affected_cnt=affected)
                acknowledged = result.get("lang_keys", [])
                if not isinstance(acknowledged, list) or not set(acknowledged) <= set(keys):
                    raise ValueError(f"{pack}: Unexpected acknowledged import keys")
                applied.extend(acknowledged)
                repeated = result.get("repeat_lang_keys", [])
                if not repeated:
                    break
                if not set(repeated) <= set(pending):
                    raise ValueError(f"{pack}: Unexpected import continuation keys")
                if repeated == pending and not acknowledged:
                    raise RuntimeError(f"{pack}: Import made no progress")
                pending = repeated
                affected = result.get("affected_cnt", 0)
            else:
                raise RuntimeError(f"{pack}: Too many import continuation requests")
            print(f"{pack}: {method} {min(start + batch_size, len(keys))}/{len(keys)}", flush=True)
        return list(dict.fromkeys(applied))

    def export(self, target, label):
        pack, _, fmt = target
        # Telegram caches exports by URL even immediately after a successful import.
        response = self.get(f"/{LANG}/{pack}/export?sync_check={time.time_ns()}")
        message = Message()
        message["Content-Disposition"] = response.headers.get("content-disposition", "")
        filename = message.get_filename()
        if not filename or not re.fullmatch(rf"{pack}_{LANG}_v\d+\.{fmt}", filename):
            raise ValueError(f"{pack}: Unexpected remote export filename")
        data = response.body()
        text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        localization.entry_keys(text, fmt)
        (self.report_dir / f"{label}-{filename}").write_bytes(data)
        return filename, text

    def phrase_details(self, pack, keys):
        def check(key):
            path = f"/{LANG}/{pack}/phrase/{quote(key, safe='')}"
            # Critical badges are public; these reads do not need the login cookies.
            for attempt in range(3):
                try:
                    with urlopen(Request(SITE + path, headers={"User-Agent": "lowercased-localizations/1.0"}), timeout=30) as response:
                        html = response.read().decode("utf-8")
                    page = PhrasePage(html)
                    if page.key != key and not page.not_found:
                        raise ValueError(f"{pack}: Unexpected phrase page for {key}")
                    return key, (page, SITE + path)
                except (URLError, TimeoutError):
                    if attempt == 2:
                        raise
                    time.sleep(attempt + 1)
        missing = sorted(key for key in set(keys) if (pack, key) not in self.phrase_cache)
        with ThreadPoolExecutor(max_workers=8) as pool:
            for key, result in pool.map(check, missing):
                self.phrase_cache[pack, key] = result
        return {key: self.phrase_cache[pack, key] for key in set(keys)}

    def protected_keys(self, pack, keys):
        return {key: url for key, (page, url) in self.phrase_details(pack, keys).items() if page.critical}


def verify_result(client, target, expected, remote, remaining=None):
    pack, _, fmt = target
    comparison = compare_export(expected, remote, fmt)
    expected_values = values_by_key(expected, fmt)
    remote_values = values_by_key(remote, fmt)
    unused = unused_plural_forms(expected_values, remote_values)
    empty = [key for key in comparison["missing_keys"] if expected_values[key] == ""]
    remaining = remaining or {}
    unresolved = (set(comparison["missing_keys"]) - set(unused)) | set(comparison["different_keys"])
    unresolved.update(key for keys in remaining.values() for key in keys)
    unresolved -= set(empty)
    mapped = {key: phrase_key(key, pack, expected_values) for key in unresolved}
    details = client.phrase_details(pack, [base for base, _ in mapped.values()])
    protected, template_only, verified_on_site = {}, {}, {}
    for key, (base, quantity) in mapped.items():
        page, url = details[base]
        if page.critical:
            protected[key] = url
        elif page.not_found:
            template_only[key] = {"reason": "phrase_absent_from_portal", "url": url}
        elif quantity == "plain" and page.plural:
            # Some source templates retain the old plain value after migration to plurals.
            template_only[key] = {"reason": "plain_template_replaced_by_plural_phrase", "url": url}
        elif page.applied.get(quantity) == expected_values.get(key) and key not in remote_values:
            verified_on_site[key] = url
    unresolved -= protected.keys() | template_only.keys() | verified_on_site.keys()
    return {"comparison": comparison, "unused_plural_forms": unused, "empty_values_omitted": empty,
            "protected_keys": protected, "template_only_keys": template_only, "verified_on_site": verified_on_site,
            "unresolved_keys": sorted(unresolved),
            "status": "mismatch" if unresolved else "verified_with_exceptions" if protected or template_only or verified_on_site else "verified"}


def sync_platform(client, target, publish, verify_only, batch_size):
    pack, output, fmt = target
    expected = (ROOT / "lowercased-en" / output).read_text(encoding="utf-8")
    report = {"platform": pack, "source": localization.find_file(ROOT / "en", pack, fmt).name}
    filename, remote = client.export(target, "before")
    report["before_export"] = filename
    report["before"] = compare_export(expected, remote, fmt)
    if verify_only:
        report.update(verify_result(client, target, expected, remote))
        return report
    config, import_id, preview = client.preview(target, expected)
    actions = preview.actionable()
    report["planned"] = {method: len(keys) for method, keys in actions.items()}
    report["skipped_removals"] = preview.rows.get("removeImportedKeys", [])
    print(f"{pack}: import preview {report['planned']}", flush=True)
    if not publish:
        report["status"] = "planned"
        return report
    pending_keys = [key for keys in actions.values() for key in keys]
    if len(pending_keys) <= 100:
        protected = client.protected_keys(pack, pending_keys)
        actions = {method: [key for key in keys if key not in protected]
                   for method, keys in actions.items()}
        actions = {method: keys for method, keys in actions.items() if keys}
    report["applied"] = {}
    for method, keys in actions.items():
        report["applied"][method] = client.apply(config, pack, import_id, method, keys, batch_size)
    _, _, remaining = client.preview(target, expected)
    report["remaining"] = remaining.actionable()
    filename, remote = client.export(target, "after")
    report["after_export"] = filename
    report["after"] = compare_export(expected, remote, fmt)
    report.update(verify_result(client, target, expected, remote, report["remaining"]))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish", action="store_true", help="Apply the import and verify it by exporting again")
    mode.add_argument("--verify", action="store_true", help="Only compare remote exports against local generated files")
    parser.add_argument("--no-update", action="store_true", help="Use the existing English exports")
    parser.add_argument("--platform", action="append", choices=[target[0] for target in localization.TARGETS])
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    if not args.state.is_file():
        parser.exit(1, "Sign in first: .venv-sync/bin/python scripts/telegram_session.py\n")
    if not 1 <= args.batch_size <= 500:
        parser.error("--batch-size must be between 1 and 500")
    localization.run(update=not args.no_update and not args.verify, check=args.no_update or args.verify)
    targets = [target for target in localization.TARGETS if not args.platform or target[0] in args.platform]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    report_dir = ROOT / ".telegram-sync" / "runs" / timestamp
    report_dir.mkdir(parents=True, mode=0o700)
    reports = []
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        request = playwright.request.new_context(storage_state=str(args.state))
        try:
            client = TelegramClient(request, report_dir)
            for target in targets:
                try:
                    report = sync_platform(client, target, args.publish, args.verify, args.batch_size)
                except Exception as error:
                    # Playwright errors can include the API URL and cookies in call logs.
                    message = str(error).split("Call log:", 1)[0]
                    message = re.sub(r"/api\?hash=[A-Za-z0-9]+", "/api?[redacted]", message)
                    report = {"platform": target[0], "status": "error", "error": message}
                reports.append(report)
                (report_dir / "report.json").write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"{target[0]}: {report['status']}" + (f" ({report['error']})" if 'error' in report else ""), flush=True)
            save_session(request.storage_state(), args.state)
        finally:
            request.dispose()
    print(f"Report: {report_dir / 'report.json'}")
    if any(report["status"] in ("error", "mismatch") for report in reports):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
