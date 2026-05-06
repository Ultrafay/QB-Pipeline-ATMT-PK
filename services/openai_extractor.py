from openai import OpenAI
import json
import base64
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel, Field
import os

from services.gl_reference_data import build_gl_prompt_section

# --- Data Models ---

class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None
    tax_percentage: Optional[float] = None  # 0, 17, 5, 13, 15, etc.
    tax_code: Optional[str] = None  # GST / EX / ZR
    gl_code: Optional[str] = None  # GL Account Name for this line


class InvoiceData(BaseModel):
    date: Optional[str] = None
    supplier_name: Optional[str] = None
    supplier_ntn: Optional[str] = None  # Pakistan NTN — 7-8 digits, often hyphenated
    supplier_strn: Optional[str] = None  # Pakistan STRN (Sales Tax Reg. No.)
    supplier_address: Optional[str] = None
    invoice_number: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[str] = None
    credit_terms: Optional[str] = None
    bill_to: Optional[str] = None
    bill_to_ntn: Optional[str] = None
    purchase_location: Optional[str] = None
    gl_code_suggested: Optional[str] = None
    exclusive_amount: Optional[float] = None
    vat_amount: Optional[float] = None
    invoice_tax_amount: Optional[float] = None
    invoice_tax_percentage: Optional[float] = None
    total_amount: Optional[float] = None
    currency: str = "PKR"
    line_items: List[LineItem] = []
    extraction_confidence: str = "medium"
    extraction_method: str = "openai_gpt4o"
    notes: Optional[str] = None
    raw_response: Optional[str] = None

    # Backwards-compat aliases for downstream code that still reads supplier_trn
    @property
    def supplier_trn(self) -> Optional[str]:
        return self.supplier_ntn or self.supplier_strn


# --- Extractor ---

class OpenAIExtractor:
    def __init__(self, api_key: str, org_id: str = None, project_id: str = None):
        if not api_key:
            raise ValueError("OpenAI API key is required")
        
        self.client = OpenAI(
            api_key=api_key,
            organization=org_id,
            project=project_id
        )
        self.model = "gpt-4o"
        self._gl_prompt: str = build_gl_prompt_section()

    def set_chart_of_accounts(self, account_names: List[str]) -> None:
        self._gl_prompt = build_gl_prompt_section(chart_of_accounts=account_names)
        print(f"[OpenAI] GL prompt updated with {len(account_names)} accounts from QBO")

    # ── System Prompt (Pakistan) ────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        return f"""You are an expert invoice data extraction system for a Pakistani company.

Analyze this invoice and extract ALL relevant data. The invoice may contain English, Urdu, or both.

CRITICAL INSTRUCTIONS:
1. EXTRACT EVERY SINGLE LINE ITEM. Do not group them. Do not summarize them.
2. All amounts must be numeric only (no currency symbols, no commas).
3. Dates must be in YYYY-MM-DD format.
4. If a field is not visible or unclear, use null.
5. NTN (National Tax Number) in Pakistan is typically 7 digits with a check digit
   (e.g. "1234567-8"). Capture it exactly as printed including the hyphen.
6. STRN (Sales Tax Registration Number) is typically 13 digits, often grouped
   as XX-XX-XXXX-XXX-XX (e.g. "42-00-1234-567-89"). Capture exactly as printed.
7. Default currency is PKR (Pakistani Rupee). Only use a different currency
   if the invoice is clearly denominated in something else (USD, AED, etc.).
8. For EACH line item, extract the GST/tax percentage applied (e.g. 17, 5, 13, 0, null if not shown).
9. For EACH line item, assign a tax_code based on the TAX CODE CLASSIFICATION below.
10. For EACH line item, assign a gl_code based on the GL CODE CLASSIFICATION below.
11. Extract the total tax amount from the invoice into invoice_tax_amount.
    If the invoice shows "GST 17%: 17,000.00" then invoice_tax_amount = 17000.00.
    If no tax line is shown, set to 0.
12. Extract the explicit tax percentage applied to the entire invoice into invoice_tax_percentage.
    If the invoice clearly says "GST 17%" or "Sales Tax 5%", use that number. If mixed or
    not stated, use null.

IDENTIFY CORRECTLY:
- SUPPLIER = The company SENDING the invoice
- BILL TO = The company RECEIVING the invoice

PAKISTAN TAX CODE CLASSIFICATION:
Assign one of these codes to EACH line item:

  "GST" — Standard Rated. Any taxable goods or services where the line shows
          a non-zero GST/Sales Tax rate (17%, 13%, 15%, 5%, etc.).
  "EX"  — Exempt / Out of Scope. Government fees, FBR fines, bank charges, insurance
          premiums passed at cost, salary reimbursements, regulatory levies. Use
          when the line clearly has no tax applied AND the invoice as a whole has tax.
  "ZR"  — Zero Rated. Exports, IT services / IT-enabled services exported abroad
          (under SRO 1125(I)/2011), certain healthcare and education supplies.
          Use when the line shows 0% explicitly as zero-rated.

DECISION LOGIC:
  - If the line shows GST > 0% on the invoice → "GST"
  - If the line shows 0% AND the supplier is exporting / IT service exporter → "ZR"
  - If the line shows 0% AND the line is a govt/regulatory pass-through fee → "EX"
  - When unsure between EX and ZR, prefer "EX"
  - When the entire invoice is "Tax Exempt" or "Zero-Rated IT Services" (per FBR
    SRO 1125), assign "ZR" to all lines.

DO NOT use UAE-style codes like SR, RC, IG. Only use GST / EX / ZR.

{self._gl_prompt}

EXTRACT INTO THIS EXACT JSON STRUCTURE:

{{
  "date": "YYYY-MM-DD",
  "supplier_name": "Company issuing the invoice",
  "supplier_ntn": "NTN as printed (e.g. 1234567-8) or null",
  "supplier_strn": "STRN as printed (e.g. 42-00-1234-567-89) or null",
  "supplier_address": "Full supplier address as a single string, or null",
  "purchase_location": "Branch / location if mentioned, or null",
  "invoice_number": "Invoice reference number",
  "description": "General description of invoice (optional)",
  "due_date": "YYYY-MM-DD or null",
  "credit_terms": "Net 30, Cash, Cheque, On Receipt, etc.",
  "bill_to": "Customer name",
  "bill_to_ntn": "Customer NTN if shown",
  "gl_code_suggested": "Primary GL category for the invoice overall",
  "exclusive_amount": 0.00,
  "vat_amount": 0.00,
  "invoice_tax_amount": 0.00,
  "invoice_tax_percentage": null,
  "total_amount": 0.00,
  "currency": "PKR",
  "line_items": [
    {{
      "description": "ERP implementation - Finance & HR module",
      "quantity": 1.0,
      "unit_price": 485000.00,
      "amount": 485000.00,
      "tax_percentage": 0,
      "tax_code": "ZR",
      "gl_code": "Software & Technology"
    }},
    {{
      "description": "Industrial Electric Motor 5HP",
      "quantity": 10.0,
      "unit_price": 45500.00,
      "amount": 455000.00,
      "tax_percentage": 17,
      "tax_code": "GST",
      "gl_code": "Cost of Goods Sold"
    }}
  ],
  "extraction_confidence": "high|medium|low",
  "notes": "Any issues found"
}}

IMPORTANT:
- 'amount' in line_items = Quantity * Unit Price BEFORE tax.
- tax_percentage per line: numeric (17, 13, 5, 0) or null.
- tax_code per line: MUST be GST, EX, or ZR.
- gl_code per line: MUST be a GL Account Name from the keyword mapping or chart of accounts.
- invoice_tax_amount: Total GST/Sales Tax shown on the invoice. If exempt or zero-rated, 0.
- invoice_tax_percentage: Numeric percentage (e.g. 17, not "17%").
- Ensure 'total_amount' matches the sum of line items + tax (less any WHT).
- If the invoice mentions "Withholding Tax" or "WHT", do NOT subtract it from
  total_amount — capture total_amount as the gross amount due.

Return ONLY valid JSON. No markdown."""

    def _encode_image_to_base64(self, image_path: str) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _get_mime_type(self, file_path: str) -> str:
        ext = Path(file_path).suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".gif": "image/gif",
            ".webp": "image/webp", ".bmp": "image/bmp",
        }
        return mime_map.get(ext, "image/jpeg")

    def extract_from_image(self, image_path: str) -> InvoiceData:
        print(f"OpenAI: Extracting from image {image_path}")
        b64_image = self._encode_image_to_base64(image_path)
        mime_type = self._get_mime_type(image_path)
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self._build_system_prompt()},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract all invoice data from this image."},
                        {"type": "image_url", "image_url": {
                            "url": f"data:{mime_type};base64,{b64_image}",
                            "detail": "high"
                        }}
                    ]
                }
            ],
            max_tokens=4096,
            temperature=0.1
        )
        
        return self._parse_response(response.choices[0].message.content)

    def extract_from_pdf(self, pdf_path: str) -> InvoiceData:
        print(f"OpenAI: Extracting from PDF {pdf_path}")
        from pdf2image import convert_from_path
        import tempfile
        
        images = convert_from_path(pdf_path, first_page=1, last_page=1, dpi=200)
        if not images:
            raise ValueError("Could not convert PDF to image")
        
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            images[0].save(tmp.name, "JPEG", quality=95)
            tmp_path = tmp.name
        
        try:
            result = self.extract_from_image(tmp_path)
        finally:
            os.unlink(tmp_path)
        
        return result

    def _parse_response(self, response_text: str) -> InvoiceData:
        try:
            clean_text = response_text
            if "```json" in clean_text:
                clean_text = clean_text.split("```json")[1].split("```")[0]
            elif "```" in clean_text:
                clean_text = clean_text.split("```")[1].split("```")[0]
            clean_text = clean_text.strip()
            
            data = json.loads(clean_text)
            
            # Default to PKR for Pakistan deployment
            extracted_currency = str(data.get("currency", "") or "").upper().strip()
            extracted_currency = extracted_currency.split()[0] if extracted_currency else ""
            if not extracted_currency or len(extracted_currency) != 3 or not extracted_currency.isalpha():
                data["currency"] = "PKR"
            else:
                data["currency"] = extracted_currency

            # Backwards-compat: if old prompt returned supplier_trn, map it to NTN
            if "supplier_trn" in data and not data.get("supplier_ntn"):
                data["supplier_ntn"] = data.pop("supplier_trn")
            
            invoice = InvoiceData(**data)
            invoice.raw_response = clean_text
            return invoice
        except Exception as e:
            print(f"Error parsing OpenAI response: {e}")
            print(f"Raw response: {response_text}")
            raise ValueError(f"Failed to parse JSON from OpenAI response: {e}")
