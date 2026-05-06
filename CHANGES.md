# Pakistan Pipeline — Changes from UAE/ATH version

This package adapts the original UAE invoice processing pipeline (ATH) for a
Pakistani accounting firm. The changes are concentrated in tax handling, tax
codes, GL keyword mapping, currency, and a Google Sheets display fix.

## Files in this package

```
services/
  gst_processor.py          ← NEW (replaces vat_processor.py)
  openai_extractor.py       ← UPDATED (Pakistan prompt: NTN/STRN/PKR/GST)
  quickbooks.py             ← UPDATED (no RCM, PKR home currency)
  gl_reference_data.py      ← UPDATED (Pakistan-relevant keywords)
  sheets_service.py         ← UPDATED (fixes "date in Unit Price" bug)
ocr_engine.py               ← UPDATED (uses gst_processor)
workers/
  drive_processor.py        ← UPDATED (uses gst_processor)
```

Files NOT included (use the originals from your existing repo):
`app.py`, `services/drive_watcher.py`, `services/gl_classifier.py`,
`utils/credentials_helper.py`, `static/*`, `Dockerfile`, `requirements.txt`,
`railway.json`, `.dockerignore`, `.gitignore`.

You can also **delete `services/vat_processor.py`** from the new repo — it's
been superseded by `gst_processor.py`.

---

## Issue 1: RCM logic firing on Pakistani invoices

**Symptom in logs:**
```
[QBO] Line 1: tax='RC Reverse Charge' → {'value': 'NON'}
[QBO] Warning: Tax code 'RC Reverse Charge' not found in QBO, falling back to NON
```

**Root cause:** `vat_processor.py` classified the supplier as `Foreign` because
it wasn't UAE/GCC, and assigned `RC` (Reverse Charge) to every line. Pakistan
QBO has no such tax code, so the fallback `NON` got used. When you enabled 5%
tax in QBO, this fallback couldn't carry tax → bill posting started failing.

**Fix:** New `gst_processor.py` uses only three Pakistan-relevant codes:

| Code | Meaning | When to use |
|---|---|---|
| `GST` | Standard rated | Any line with non-zero GST/Sales Tax (17%, 13%, 5%, etc.) |
| `EX` | Exempt | Govt fees, FBR fines, bank charges, salary reimbursements |
| `ZR` | Zero rated | Exports, IT services under SRO 1125, healthcare/education |

Legacy UAE codes are auto-mapped if the extractor still emits them
(`SR`/`IG` → `GST`, `RC` → `EX`).

---

## Issue 2: Hardcoded AED home currency

**Symptom in logs:**
```
[QBO] Fetched Exchange Rate: 1 PKR = 1 AED as of 2025-05-06
```

**Root cause:** `quickbooks.py` had `if currency_code == "AED": return 1.0`,
making AED the implicit home currency. PKR was treated as a foreign currency
needing conversion to AED.

**Fix:** Home currency is now configurable via the `QBO_HOME_CURRENCY` env var
(default: `PKR`). The exchange-rate function returns 1.0 when the invoice
currency matches the home currency, and only fetches a rate for actual foreign
currencies. Sensible PKR-based fallback rates are included for USD, AED, EUR,
GBP, SAR.

**Add to your `.env` (or Railway env vars):**
```
QBO_HOME_CURRENCY=PKR
```

---

## Issue 3: GL keyword mapping had no IT-services coverage

**Symptom in logs:**
```
[GL] No match for: 'Enterprise Resource Planning (ERP) Implementation Module: Finance & Accounts, HR'
[GL] No match for: 'Network Infrastructure Setup Server room cabling, switches, firewall config'
[GL] No match for: 'Staff Training - ERP End Users On-site training, 3 days, up to 20 users'
[GL] No match for: 'Cloud Hosting – AWS Pakistan Region Dedicated VPS, 100 GB SSD, SSL, daily backup'
```

Only "maintenance" matched (line 2). Everything else hit Uncategorised Expense.

**Fix:** `gl_reference_data.py` now includes Pakistan-relevant categories:

- **Software & Technology**: erp, implementation, integration, cloud hosting,
  aws, azure, vps, ssl, domain
- **Office Equipment**: networking gear (switches, firewall, cabling, ups)
- **Training & Development**: training, workshop, seminar, certification
- **Dues & Subscriptions**: saas, subscription, license, office 365, github
- **Utilities**: K-Electric, LESCO, FESCO, IESCO, WAPDA, SSGC, SNGPL, PTCL, Jazz, Telenor
- **Travel**: Uber, Careem, indrive, bykea, PIA, Airblue, Serene Air
- **Fuel & Vehicle**: petrol, diesel, CNG, PSO, Shell, Total Parco
- **Employee Benefits**: EOBI, Provident Fund, Punjab ESSI, social security
- **Freight & Delivery**: TCS, Leopard Courier, M&P, FedEx, DHL

> ⚠️ **Important:** The `GLClassifier` (in your existing
> `services/gl_classifier.py`) reads from your **Google Sheet "GL Mapping" tab**
> at runtime, not from `gl_reference_data.py`. That Python file only feeds the
> GPT-4o prompt. **You must also update the Google Sheet GL Mapping tab** with
> these same keywords/account-name pairs. Otherwise classification at QBO-post
> time will still miss, even though GPT-4o suggests the right account.

---

## Issue 4: "Unit Price" column showing dates instead of numbers

**Symptom (your screenshot):** Column K (Unit Price) showed values like
`21-Feb-2090`, `22-Feb-2067`, `18-Nov-3227`. These are **numeric values that
Google Sheets is rendering as dates**.

**Root cause:** The columns J, K, L were manually formatted as **Date** in the
spreadsheet at some point. With `valueInputOption="USER_ENTERED"`, Sheets
applies the column's stored format. A unit price of `69450` rendered as a
date is approximately Feb 2090 (Excel/Sheets day-0 epoch is 1899-12-30).

**Fix:** `sheets_service.py` now calls a new `_apply_numeric_column_formats()`
method on every `ensure_headers()` call. This uses the Sheets `batchUpdate` API
to force NUMBER format (`#,##0.00`) on:

- Column J (Quantity)
- Column K (Unit Price)
- Column L (Line Amount)
- Column R (Exclusive Amount)
- Column S (GST Amount)
- Column T (Invoice Total)
- Column U (Tax %)

It also runs once at first append, ensuring existing sheets get fixed without
manual intervention. Numeric values are also explicitly coerced to `float`
before writing, so they're never strings that could be parsed as dates.

---

## Other changes worth noting

### Schema change in Google Sheets

Column G renamed from `Supplier TRN` to `Supplier NTN`. Column S renamed from
`VAT Amount` to `GST Amount`. The number of columns went from 27 (A:AA) to
29 (A:AC). If you're reusing an existing sheet, either:
- Manually rename the old headers to match, OR
- Create a fresh "Invoices" tab; the code will populate it.

### Pakistan-specific extraction prompt

`openai_extractor.py` system prompt now:
- Defaults `currency` to `PKR` (not USD/AED)
- Captures `supplier_ntn` AND `supplier_strn` as separate fields
- Mentions FBR and SRO 1125(I)/2011 for zero-rated IT services
- Removes references to UAE TRN format / 15-digit "100..." pattern
- Removes UAE/GCC/Foreign location decision tree
- Removes RCM-related instructions

Backwards-compat: if old downstream code reads `supplier_trn`, the data model
exposes it as a `@property` returning NTN or STRN.

### Ditching tax distribution / TaxInclusive

`quickbooks.py` no longer distributes foreign tax into line amounts. Pakistani
invoices always post with `GlobalTaxCalculation = "TaxExcluded"`. The
`create_rcm_journal_entry` method has been removed entirely.

---

## Setup checklist for the new repo

1. Create the new repo and copy in your **existing** files (`app.py`,
   `services/drive_watcher.py`, `services/gl_classifier.py`, `utils/`,
   `static/`, `Dockerfile`, `requirements.txt`, `railway.json`).
2. Drop in the files from this zip (overwriting where they conflict).
3. **Delete `services/vat_processor.py`** from the new repo.
4. Update environment variables in Railway:
   - Add `QBO_HOME_CURRENCY=PKR`
   - Update `QBO_REALM_ID`, `QBO_ACCESS_TOKEN`, `QBO_REFRESH_TOKEN` from your
     new Pakistani QBO trial connection (re-run the OAuth flow at
     `/auth/quickbooks/connect`).
5. In your QBO Pakistan trial, ensure tax codes named one of `GST`, `Sales Tax`,
   `Standard GST`, or similar are configured at whatever rate you want to test
   (5% for testing, 17% for production). The fuzzy matcher will find them.
6. Update the Google Sheet "GL Mapping" tab with the new keywords/accounts
   listed in Issue 3.
7. Either create a fresh "Invoices" tab in the tracking sheet, or rename the
   `Supplier TRN` column to `Supplier NTN` and `VAT Amount` to `GST Amount`.

## Smoke test

```bash
python -c "import app"
python -c "import ocr_engine"
python -c "from workers.drive_processor import DriveProcessor"
python -c "from services.gst_processor import process_gst; print(process_gst({'line_items':[{'description':'test','amount':100,'tax_code':'GST'}],'invoice_tax_amount':17,'invoice_tax_percentage':17}))"
```

All should run without error.
