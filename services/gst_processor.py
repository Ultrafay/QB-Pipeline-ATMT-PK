"""
GST Processor — validates and finalises per-line tax codes for Pakistani invoices.

Pakistan tax landscape (FBR):
  - Standard GST/Sales Tax rate: 17% (some sectors apply different rates such as 5% / 13% / 15%)
  - Withholding Tax (WHT): typically 4.5% on services, 4% on goods (deducted by buyer)
  - IT services / IT-enabled services: zero-rated under SRO 1125(I)/2011
  - Exports: zero-rated

This module is intentionally simpler than the UAE vat_processor:
  - No reverse-charge mechanism (RCM)
  - No foreign-tax distribution / TaxInclusive mode
  - No supplier location categorisation (UAE/GCC/Foreign)

Tax codes assigned:
  GST    — Standard rated (any non-zero GST rate found on the line / invoice)
  EX     — Exempt (no tax on the line, e.g. govt fees, bank charges)
  ZR     — Zero rated (exports, IT services, certain healthcare/education)
"""
import re
from typing import List

# Shorthand → human-friendly QBO TaxCode display name.
# QuickBooks Pakistan trial typically exposes a single "GST" tax code at whatever
# rate the user has configured. The fuzzy resolver in quickbooks.py will match
# this against codes like "GST", "GST 17%", "Sales Tax 5%", etc.
TAX_CODE_MAP = {
    "GST": "GST",
    "EX":  "Exempt",
    "ZR":  "Zero Rated",
}

VALID_CODES = set(TAX_CODE_MAP.keys())

# Mismatch threshold (PKR) — lines whose computed tax differs from the
# invoice tax line by more than this trigger a manual review note.
_MISMATCH_THRESHOLD = 1.0


# ── Line-level helpers ────────────────────────────────────────────────────

def _fallback_code_for_line(tax_pct, has_invoice_tax: bool) -> str:
    """
    Pick a sensible fallback tax code when the extractor didn't provide one.

    Rules:
      - If the line explicitly shows a non-zero %, treat as GST
      - If the line shows 0%, treat as EX (exempt)
      - If unknown but the invoice as a whole has tax, default to GST
      - Otherwise EX
    """
    if tax_pct is not None:
        try:
            pct = float(tax_pct)
            if pct > 0:
                return "GST"
            return "EX"
        except (TypeError, ValueError):
            pass
    return "GST" if has_invoice_tax else "EX"


# ── Main entry point ─────────────────────────────────────────────────────

def process_gst(invoice_data: dict) -> dict:
    """
    Validate per-line tax codes for a Pakistani invoice. Assigns fallbacks
    where the extractor didn't provide a code, maps to QBO display names,
    and runs a tax-total mismatch sanity check.

    Mutates and returns the invoice_data dict.
    """
    vat_amount  = float(invoice_data.get("vat_amount", 0.0) or 0.0)
    invoice_tax = float(invoice_data.get("invoice_tax_amount", 0.0) or 0.0)
    line_items: List[dict] = invoice_data.get("line_items", []) or []
    has_invoice_tax = vat_amount > 0 or invoice_tax > 0

    print(
        f"[GST] Pakistani invoice — vat_amount={vat_amount}, "
        f"invoice_tax={invoice_tax}, lines={len(line_items)}"
    )

    review_messages: List[str] = []

    # ── Validate / assign per-line codes ──────────────────────────────────
    for idx, item in enumerate(line_items, start=1):
        raw_code = str(item.get("tax_code", "") or "").upper().strip()

        # Map common UAE codes onto Pakistan codes if the extractor still emits them.
        # SR/IG → GST, RC/EX → EX, ZR → ZR
        legacy_map = {"SR": "GST", "IG": "GST", "RC": "EX"}
        if raw_code in legacy_map:
            raw_code = legacy_map[raw_code]

        if raw_code in VALID_CODES:
            code = raw_code
        else:
            code = _fallback_code_for_line(item.get("tax_percentage"), has_invoice_tax)
            if raw_code:
                review_messages.append(
                    f"Line {idx}: unrecognised tax_code '{raw_code}', defaulted to '{code}'"
                )

        item["tax_code"]     = code
        item["qbo_tax_code"] = TAX_CODE_MAP[code]

    # ── Mismatch sanity check ─────────────────────────────────────────────
    # Pakistan applies GST per-line at the SAME rate (whatever the invoice uses).
    # We don't know the exact rate without reading invoice_tax_percentage, so we
    # just compare the invoice-level tax to (subtotal × stated rate). If the
    # extractor gave us a percentage, we can validate.
    pct = invoice_data.get("invoice_tax_percentage")
    reference_tax = invoice_tax if invoice_tax > 0 else vat_amount

    if pct is not None and pct != "" and reference_tax > 0:
        try:
            rate = float(pct) / 100.0
            taxable_subtotal = sum(
                float(item.get("amount", 0.0) or 0.0)
                for item in line_items
                if item.get("tax_code") == "GST"
            )
            implied_tax = round(taxable_subtotal * rate, 2)
            diff = abs(implied_tax - reference_tax)
            if diff > _MISMATCH_THRESHOLD:
                msg = (
                    f"TAX MISMATCH: implied tax = {implied_tax} "
                    f"({pct}% on {taxable_subtotal}), invoice tax = {reference_tax}, "
                    f"diff = {diff:.2f}"
                )
                review_messages.append(msg)
                print(f"[GST] {msg}")
        except (TypeError, ValueError):
            pass

    # ── Assemble review memo ──────────────────────────────────────────────
    if review_messages:
        combined = " | ".join(review_messages)
        existing = invoice_data.get("manual_review_memo", "") or ""
        invoice_data["manual_review_memo"] = (
            f"{existing} | {combined}" if existing else combined
        )
        print(f"[GST] Review flagged: {combined}")

    # ── Metadata for downstream consumers ─────────────────────────────────
    invoice_data["line_items"]     = line_items
    invoice_data["is_pk_invoice"]  = True
    invoice_data["tax_inclusive"]  = False  # Pakistan invoices use TaxExcluded

    return invoice_data
