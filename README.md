# Conrad Balluff Parser

A Python scraper that turns Balluff product pages on Conrad Germany into a CSV
for catalog review. It collects product identifiers, technical specifications,
document links, and available offer details such as price, VAT, and stock.
An SQLite checkpoint lets a run pick up where it stopped.

## Try the example

[View the two fictional records](examples/products.synthetic.csv). They use all
21 export columns. Product IDs and EANs are text. In Excel, import those columns
as Text to keep leading zeros. The CSV uses UTF-8 with a BOM. Fields such as
`technical_specs_json`, `documents_json`, and `offers_json` contain JSON so that
variable-length lists and repeated specification names are preserved.

| product_id | brand | title | technical_specs_json |
| --- | --- | --- | --- |
| `0012345` | Balluff | Synthetic proximity sensor | `[{"name":"Sensing distance","value":"4 mm"}]` |

### Windows PowerShell

Use Python 3.10 or newer. The live workflow was developed for **Windows 11** with
an installed Google Chrome browser. From the repository directory, run:

```powershell
$env:PYTHONUTF8 = '1'
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
& .\.venv\Scripts\python.exe conrad.py --help
& .\.venv\Scripts\python.exe conrad.py validate examples/products.synthetic.csv --expected 2
```

### macOS and Linux

These commands are for local tests and CSV validation. The managed Chrome
workflow depends on Windows process checks and is only supported on Windows.

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m pip check
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python conrad.py --help
./.venv/bin/python conrad.py validate examples/products.synthetic.csv --expected 2
```

## Collect live products on Windows

Open the dedicated browser with `browser-open`:

```powershell
& .\.venv\Scripts\python.exe conrad.py browser-open --browser chrome
```

Complete any site security check manually and sign in to your Conrad account in
that window. Keep it visible. Verify login, restart the project browser, and
verify the persisted session before running the collector:

```powershell
& .\.venv\Scripts\python.exe conrad.py session-check --browser chrome
& .\.venv\Scripts\python.exe conrad.py browser-close --browser chrome
& .\.venv\Scripts\python.exe conrad.py browser-open --browser chrome
& .\.venv\Scripts\python.exe conrad.py session-check --browser chrome --after-restart
```

The checks look for the visible **Abmelden** account marker and a Balluff
product heading. The restart check should pass without another sign-in. After a
later restart, run the `--after-restart` check before a normal check. Session
files and the dedicated profile live under `local/`.

```powershell
& .\.venv\Scripts\python.exe conrad.py one --browser chrome
& .\.venv\Scripts\python.exe conrad.py discover --browser chrome --pages 2
& .\.venv\Scripts\python.exe conrad.py collect --browser chrome --count 5 --delay 2
& .\.venv\Scripts\python.exe conrad.py validate output/balluff-5.csv --expected 5
```

`one` writes `output/one-product.csv` and `output/one-product.json`. `discover`
follows the Balluff listing and saves deduplicated candidate URLs to
`local/links.json`. If discovery stops partway, rerun it before collecting.
`collect` verifies the brand and signed-in state on each new card, waits between
pages, and stores records in `local/progress.sqlite3`. Repeat the same command
to resume. Completed records are reused. With `--count 100`, the output is
`output/balluff.csv`. Use a matching `--expected` when validating.

## How it works and where it can fail

- Python Playwright attaches to the isolated Chrome session over loopback.
  Initial sign-in requires a person. If a security check or expired login stops
  collection, resolve it in the project browser and verify the session again.
- Product details combine visible page content with product-specific Nuxt state.
  The parser waits for the displayed quantity, total, and VAT label to agree with
  the current offer. Site layout or state changes can make extraction fail.
- Stock, summary fields, and notes can be missing. The CSV distinguishes absent
  fields from failed extraction. Some alternative offers are restricted by
  account context. Unknown shippers and unavailable offer panel details can be
  missing from the export.
- The repository contains code, offline tests, and fictional records. Live
  exports and browser data are kept locally.
