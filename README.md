# lowercased English for Telegram

Generate lowercase English language packs for Android, Android X, iOS, macOS,
Telegram Desktop, Unigram, WebA and WebK. Requires Python 3.9+; no extra packages.

```sh
python3 main.py --update   # download current English exports and regenerate all packs
python3 main.py            # regenerate from the exports already in en/
python3 main.py --check    # check syntax and verify that generated files are up to date locally
python3 -m unittest discover -s tests
```

The script resolves paths relative to itself, so it also works from another directory.
`--update` downloads from `https://translations.telegram.org/en/<platform>/export`
and validates all eight packs before writing. It uses the server's versioned filenames
and removes superseded exports for each platform. A failed download or invalid export
leaves the existing files intact. Writes replace individual files atomically.

Only string values are lowercased. Keys, comments, plural variants, placeholders,
escape sequences, URLs, markup, and case-sensitive date-format symbols are preserved.
Quoted text inside date formats is lowercased. Upstream duplicate keys are retained.

Generated files are saved in `lowercased-en/`. The commands above only update local files.

## Automatic export → import → verification

One-time setup (Python 3.9+ and an installed Google Chrome):

```sh
python3 -m venv .venv-sync
.venv-sync/bin/python -m pip install -r requirements-sync.txt
.venv-sync/bin/python scripts/telegram_session.py
```

Complete Telegram sign-in in the opened window. The script saves a separate session
in `.telegram-sync/session.json` with owner-only permissions. `.telegram-sync/` is
ignored by Git. Subsequent runs use that session without opening a browser; sign in
again only if Telegram expires or revokes it. The login browser profile is also kept
locally to make reauthentication easier. To use Playwright's Chromium instead of
Chrome, install it with `.venv-sync/bin/playwright install chromium` and pass
`--channel chromium` to the login command.

Run the entire update:

```sh
.venv-sync/bin/python scripts/telegram_sync.py --publish
```

This downloads current official English exports, regenerates the lowercase files,
backs up each remote language pack, uploads an import preview, applies changes in
batches, and verifies with both a new preview and a fresh remote export. The target
language is fixed to `lowercased-en`. All eight platforms are processed by default.
Existing strings absent from the English export are not deleted from Telegram.

Other modes:

```sh
.venv-sync/bin/python scripts/telegram_sync.py              # refresh files and upload previews; no publishing
.venv-sync/bin/python scripts/telegram_sync.py --verify     # read-only remote comparison with local files
.venv-sync/bin/python scripts/telegram_sync.py --publish --platform ios
.venv-sync/bin/python scripts/telegram_sync.py --publish --no-update  # use already-downloaded English
```

Each run saves its report, API acknowledgements, preview HTML, and before/after exports
under `.telegram-sync/runs/`. An interrupted run can be repeated: a new preview finds
what remains. Unknown mismatches and errors return exit status 1.

Telegram restricts **Critical** phrases to coordinators of official languages. The
script identifies those restrictions from the live phrase pages and reports the
keys and links as `verified_with_exceptions`, preserving Telegram's official
values. English packs may also omit unused plural forms that appear in the source
templates; these are listed separately and only accepted when their English
one/other counterparts are present. All other missing or differing values fail
verification. Empty template values omitted from exports are recorded separately.
The report also identifies obsolete template entries whose phrase no longer exists
on the portal, plain template entries replaced by plural phrases, and strings verified
as applied on their phrase page but absent from Telegram's export. These are explicit
export limitations, not successful exported translations. Exports use unique request
URLs to avoid Telegram's stale export cache.

The older `telegram_import_payload.py` and `telegram_android_live_check.py` scripts
remain available for manual Android import diagnostics.

## License

Code (`main.py`, `scripts/`, `tests/`) is licensed under the MIT License (see `LICENSE`). Language strings in `en/` and `lowercased-en/` come from translations.telegram.org, belong to Telegram, and are not covered by the MIT License.
