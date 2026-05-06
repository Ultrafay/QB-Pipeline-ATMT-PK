from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime
from typing import List, Dict, Any
import os

class GoogleSheetsService:
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
    
    # Column headers for the invoice tracking sheet (Pakistan version).
    # Column letters in comments make the format-fix code below easier to follow.
    HEADERS = [
        "Timestamp",          # A
        "File ID",             # B
        "Line #",              # C
        "File Name",           # D
        "Invoice Date",        # E
        "Supplier Name",       # F
        "Supplier NTN",        # G  (was: Supplier TRN in UAE version)
        "Invoice Number",      # H
        "Item Description",    # I
        "Quantity",            # J  ← numeric, was being shown as date
        "Unit Price",          # K  ← numeric, was being shown as date
        "Line Amount",         # L  ← numeric, was being shown as date
        "Due Date",            # M
        "Credit Terms",        # N
        "Purchase Location",   # O
        "Bill To",             # P
        "GL Code (Suggested)", # Q
        "Exclusive Amount",    # R  ← numeric
        "GST Amount",          # S  ← numeric (was: VAT Amount)
        "Invoice Total",       # T  ← numeric
        "Tax %",               # U  ← numeric
        "Currency",            # V
        "Confidence",          # W
        "Status",              # X
        "QB Transaction ID",   # Y
        "Notes",               # Z
        "QBO Status",          # AA
        "QBO Bill ID",         # AB
        "QBO Currency",        # AC
    ]

    # Numeric columns that need explicit number formatting to prevent Sheets
    # from interpreting them as dates.
    # Column letter -> 0-based index mapping for clarity:
    # J=9, K=10, L=11, R=17, S=18, T=19, U=20
    _NUMERIC_COLUMN_INDICES = [9, 10, 11, 17, 18, 19, 20]

    def __init__(self, credentials_path: str, spreadsheet_id: str):
        if not os.path.exists(credentials_path):
            raise FileNotFoundError(f"Credentials file not found at {credentials_path}")
             
        self.spreadsheet_id = spreadsheet_id
        self.creds = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=self.SCOPES
        )
        self.service = build('sheets', 'v4', credentials=self.creds)
        self.sheet = self.service.spreadsheets()
        self._headers_ensured = False
    
    def ensure_headers(self, sheet_name: str = "Invoices"):
        """
        Create headers if sheet is empty AND apply explicit number formats
        to numeric columns so they don't render as dates.
        """
        if self._headers_ensured:
            return

        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id, range=f"{sheet_name}!A1:AC1"
            ).execute()
            values = result.get('values', [])
            
            if not values:
                body = {'values': [self.HEADERS]}
                self.sheet.values().append(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"{sheet_name}!A1",
                    valueInputOption="RAW",
                    body=body
                ).execute()
                print("[Sheets] Headers added.")

            # Always run the format fix — covers existing sheets where columns
            # got auto-formatted as dates.
            self._apply_numeric_column_formats(sheet_name)
            self._headers_ensured = True
        except Exception as e:
            print(f"[Sheets] ensure_headers error: {e}")

    def _apply_numeric_column_formats(self, sheet_name: str = "Invoices"):
        """
        Force NUMBER format on the numeric columns (J, K, L, R, S, T, U).
        This overrides any manual Date formatting the user may have applied
        previously, which is what was causing 'Unit Price' to show dates.
        """
        try:
            sheet_metadata = self.sheet.get(
                spreadsheetId=self.spreadsheet_id
            ).execute()

            sheet_id = None
            for s in sheet_metadata.get('sheets', []):
                if s['properties']['title'] == sheet_name:
                    sheet_id = s['properties']['sheetId']
                    break

            if sheet_id is None:
                print(f"[Sheets] Sheet '{sheet_name}' not found — skipping format fix.")
                return

            requests_list = []
            for col_idx in self._NUMERIC_COLUMN_INDICES:
                requests_list.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,           # skip header row
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "numberFormat": {
                                    "type": "NUMBER",
                                    "pattern": "#,##0.00"
                                }
                            }
                        },
                        "fields": "userEnteredFormat.numberFormat"
                    }
                })

            if requests_list:
                self.sheet.batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"requests": requests_list}
                ).execute()
                print(f"[Sheets] Applied NUMBER format to {len(requests_list)} numeric column(s).")
        except Exception as e:
            print(f"[Sheets] _apply_numeric_column_formats error: {e}")
    
    def append_invoice(self, invoice_data: dict, file_id: str, filename: str) -> bool:
        """Append extracted invoice data as new row(s). One row per line item."""
        try:
            self.ensure_headers()
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows_to_add = []
            line_items = invoice_data.get('line_items', [])
            
            if not line_items:
                line_items = [{
                    "description": invoice_data.get('description', ''),
                    "quantity": 1,
                    "unit_price": invoice_data.get('total_amount', 0.0),
                    "amount": invoice_data.get('total_amount', 0.0)
                }]

            # Pakistan deployment: prefer NTN field, but fall back to legacy TRN
            ntn = (invoice_data.get('supplier_ntn')
                   or invoice_data.get('supplier_trn')
                   or invoice_data.get('supplier_strn')
                   or '')

            for index, item in enumerate(line_items, start=1):
                line_id = f"{invoice_data.get('invoice_number', 'UNK')}-L{index}"

                # Coerce numeric fields to floats so Sheets stores them as
                # numbers, not strings that might be parsed as dates.
                quantity     = self._to_number(item.get('quantity'))
                unit_price   = self._to_number(item.get('unit_price'))
                line_amount  = self._to_number(item.get('amount'))
                excl_amount  = self._to_number(invoice_data.get('exclusive_amount'))
                gst_amount   = self._to_number(invoice_data.get('vat_amount'))
                total_amount = self._to_number(invoice_data.get('total_amount'))
                tax_pct      = self._to_number(invoice_data.get('invoice_tax_percentage'))

                row = [
                    timestamp,
                    file_id,
                    line_id,
                    filename,
                    invoice_data.get('date', ''),
                    invoice_data.get('supplier_name', ''),
                    ntn,
                    invoice_data.get('invoice_number', ''),
                    item.get('description', ''),
                    quantity,
                    unit_price,
                    line_amount,
                    invoice_data.get('due_date', ''),
                    invoice_data.get('credit_terms', ''),
                    invoice_data.get('purchase_location', ''),
                    invoice_data.get('bill_to', ''),
                    invoice_data.get('gl_code_suggested', ''),
                    excl_amount,
                    gst_amount,
                    total_amount,
                    tax_pct,
                    invoice_data.get('currency', 'PKR'),
                    invoice_data.get('extraction_confidence', 'medium'),
                    "Pending Review",
                    "",
                    invoice_data.get('notes', ''),
                    "",
                    "",
                    invoice_data.get('currency', 'PKR'),
                ]
                rows_to_add.append(row)

            body = {'values': rows_to_add}

            self.sheet.values().append(
                spreadsheetId=self.spreadsheet_id,
                range="Invoices!A:A",
                valueInputOption="USER_ENTERED",
                body=body
            ).execute()

            return True
        except Exception as e:
            print(f"[Sheets] Error appending invoice: {e}")
            return False

    @staticmethod
    def _to_number(value) -> float:
        """Convert a value to float, returning 0.0 if conversion fails or input is None."""
        if value is None or value == "":
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    
    def _find_row_by_file_id(self, file_id: str) -> int:
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id, range="Invoices!B:B"
            ).execute()
            values = result.get('values', [])
            for index, row in enumerate(values):
                if row and row[0] == file_id:
                    return index + 1
            return -1
        except Exception as e:
            print(f"[Sheets] Error finding row: {e}")
            return -1

    def _find_all_rows_by_file_id(self, file_id: str) -> list:
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id, range="Invoices!B:B"
            ).execute()
            values = result.get('values', [])
            return [
                index + 1
                for index, row in enumerate(values)
                if row and row[0] == file_id
            ]
        except Exception as e:
            print(f"[Sheets] Error finding rows: {e}")
            return []

    def update_status(self, file_id: str, status: str, qb_transaction_id: str = None):
        """
        Update Status (col X) and QB Transaction ID (col Y) for the FIRST
        row matching the file_id. (For per-line tracking, use update_qbo_status.)
        """
        row_num = self._find_row_by_file_id(file_id)
        if row_num == -1:
            print(f"[Sheets] File ID {file_id} not found.")
            return False
        try:
            # Status is now Column X (24th column = index 23)
            range_name = f"Invoices!X{row_num}"
            self.sheet.values().update(
                spreadsheetId=self.spreadsheet_id, range=range_name,
                valueInputOption="RAW", body={'values': [[status]]}
            ).execute()
            
            if qb_transaction_id:
                # QB Transaction ID is Column Y (25th column = index 24)
                range_id = f"Invoices!Y{row_num}"
                self.sheet.values().update(
                    spreadsheetId=self.spreadsheet_id, range=range_id,
                    valueInputOption="RAW", body={'values': [[qb_transaction_id]]}
                ).execute()
            return True
        except Exception as e:
            print(f"[Sheets] Error updating status: {e}")
            return False

    def update_qbo_status(self, file_id: str, qbo_status: str, qbo_bill_id: str) -> bool:
        """
        Write QBO Status (col AA) and QBO Bill ID (col AB) for all rows
        belonging to this file_id (handles multi-line invoices).
        """
        row_nums = self._find_all_rows_by_file_id(file_id)
        if not row_nums:
            print(f"[Sheets] File ID '{file_id}' not found — cannot update QBO status.")
            return False
        try:
            for row_num in row_nums:
                # AA = QBO Status (27th column)
                self.sheet.values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"Invoices!AA{row_num}",
                    valueInputOption="RAW",
                    body={'values': [[qbo_status]]},
                ).execute()
                # AB = QBO Bill ID (28th column)
                self.sheet.values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"Invoices!AB{row_num}",
                    valueInputOption="RAW",
                    body={'values': [[qbo_bill_id]]},
                ).execute()
            print(f"[Sheets] QBO status updated for {len(row_nums)} row(s): {qbo_status}")
            return True
        except Exception as e:
            print(f"[Sheets] Error updating QBO status: {e}")
            return False
    
    def get_invoices(self, status_filter: str = None) -> List[Dict]:
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id, range="Invoices!A:AC"
            ).execute()
            values = result.get('values', [])
            if not values or len(values) < 2:
                return []
            headers = [h.lower().replace(" ", "_") for h in values[0]]
            invoices = []
            for row in values[1:]:
                invoice = {}
                for i, value in enumerate(row):
                    if i < len(headers):
                        invoice[headers[i]] = value
                current_status = invoice.get('status', '').lower()
                if status_filter and status_filter.lower() != current_status:
                    continue
                invoices.append(invoice)
            return invoices
        except Exception as e:
            print(f"[Sheets] Error fetching invoices: {e}")
            return []

    def check_duplicate(self, invoice_number: str, supplier_name: str) -> bool:
        try:
            # Read F (Supplier) and H (Invoice Number) — columns shifted slightly,
            # but Supplier is still F and Invoice Number is still H in the new schema.
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id, range="Invoices!F:H"
            ).execute()
            values = result.get('values', [])
            if not values:
                return False
            for row in values:
                if len(row) >= 3:
                    existing_supplier = row[0]
                    existing_invoice = row[2]
                    if (str(invoice_number).strip().lower() == str(existing_invoice).strip().lower() and 
                        str(supplier_name).strip().lower() in str(existing_supplier).strip().lower()):
                        return True
            return False
        except Exception as e:
            print(f"[Sheets] Error checking duplicate: {e}")
            return False
