"""
QuickBooks Online integration for Pakistani deployment.

Differences vs the UAE version:
  - No Reverse-Charge Mechanism (RCM) journal entries
  - No tax-inclusive distribution / no foreign-tax grossing-up
  - No supplier-location categorisation (UAE / GCC / Foreign)
  - Home currency configurable via QBO_HOME_CURRENCY env var (default: PKR)
  - Exchange-rate logic recognises PKR (or whatever the home currency is)
    and only fetches a rate when the invoice is in a different currency

Handles:
  - OAuth 2.0 token management with automatic refresh on 401
  - Fuzzy vendor search + auto-creation
  - Bill posting via POST /v3/company/{realm_id}/bill
"""

import os
import json
import base64
from datetime import date
from typing import Optional, Tuple, Dict
from pathlib import Path

import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder
from thefuzz import fuzz
from dotenv import load_dotenv, set_key, find_dotenv

load_dotenv()

# ── Constants ────────────────────────────────────────────────────────────────

SANDBOX_BASE    = "https://quickbooks.api.intuit.com"
PRODUCTION_BASE = "https://quickbooks.api.intuit.com"
TOKEN_URL       = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

FUZZY_MATCH_THRESHOLD = 80


# ── Service Class ────────────────────────────────────────────────────────────

class QuickBooksService:
    """
    Integrates with QuickBooks Online API (Pakistan deployment).

    Usage:
        qbo = QuickBooksService()
        status, bill_id = qbo.sync(invoice_data_dict)
    """

    def __init__(self):
        self.client_id     = os.getenv("QBO_CLIENT_ID", "")
        self.client_secret = os.getenv("QBO_CLIENT_SECRET", "")
        self.realm_id      = os.getenv("QBO_REALM_ID", "")
        self.access_token  = os.getenv("QBO_ACCESS_TOKEN", "")
        self.refresh_token = os.getenv("QBO_REFRESH_TOKEN", "")

        environment  = os.getenv("QBO_ENVIRONMENT", "sandbox").lower()
        self.base_url = SANDBOX_BASE if environment == "sandbox" else PRODUCTION_BASE

        # Home currency (defaults to PKR for Pakistan deployments).
        # Used by exchange-rate logic and as the fallback when an invoice
        # currency cannot be determined.
        self.home_currency = os.getenv("QBO_HOME_CURRENCY", "PKR").upper()

        _project_root = Path(__file__).resolve().parent.parent
        _explicit_env = _project_root / ".env"
        self._env_path = str(_explicit_env) if _explicit_env.exists() else (find_dotenv() or ".env")
        print(f"[QBO] Token store: {self._env_path}")
        print(f"[QBO] Home currency: {self.home_currency}")

        if not self.realm_id:
            raise ValueError("QBO_REALM_ID is not set in .env")
        if not self.client_id or not self.client_secret:
            raise ValueError("QBO_CLIENT_ID / QBO_CLIENT_SECRET not set in .env")

        self.gl_cache = {}
        self.default_expense_account = None
        self._tax_rate_map = None
        self.gl_classifier = None

        self.vendor_cache = self._build_vendor_cache()

        print(f"[QBO] Initialized ({environment}) — realm: {self.realm_id} — cached vendors: {len(self.vendor_cache)}")

    # ── Vendor Cache ─────────────────────────────────────────────────────────

    def _build_vendor_cache(self) -> dict:
        cache = {}
        if not self.access_token:
            return cache
        try:
            query = "SELECT * FROM Vendor WHERE Active = true MAXRESULTS 1000"
            resp = self._request("GET", "query", params={"query": query})
            if resp.status_code == 200:
                vendors = resp.json().get("QueryResponse", {}).get("Vendor", [])
                for v in vendors:
                    name_clean = v.get("DisplayName", "").lower().strip()
                    if name_clean:
                        cache[name_clean] = v.get("Id")
                print(f"[QBO] Built in-memory vendor cache with {len(cache)} vendors.")
            else:
                print(f"[QBO] Failed to build vendor cache: {resp.status_code} - {resp.text[:200]}")
        except Exception as e:
            print(f"[QBO] Exception building vendor cache: {e}")
        return cache

    def _save_vendor_cache(self) -> None:
        pass

    # ── Token Management ─────────────────────────────────────────────────────

    def _save_tokens(self, access_token: str, refresh_token: str, realm_id: str = None) -> None:
        self.access_token  = access_token
        self.refresh_token = refresh_token
        if realm_id:
            self.realm_id = realm_id

        os.environ["QBO_ACCESS_TOKEN"]  = access_token
        os.environ["QBO_REFRESH_TOKEN"] = refresh_token
        if realm_id:
            os.environ["QBO_REALM_ID"] = realm_id

        railway_token = os.getenv("RAILWAY_API_TOKEN")
        service_id    = os.getenv("RAILWAY_SERVICE_ID")

        if railway_token and service_id:
            project_id = os.getenv("RAILWAY_PROJECT_ID")
            environment_id = os.getenv("RAILWAY_ENVIRONMENT_ID")
            headers = {
                "Authorization": f"Bearer {railway_token}",
                "Content-Type": "application/json"
            }
            variables = {
                "QBO_ACCESS_TOKEN": access_token,
                "QBO_REFRESH_TOKEN": refresh_token
            }
            current_realm = realm_id or getattr(self, "realm_id", None)
            if current_realm:
                variables["QBO_REALM_ID"] = current_realm

            payload = {
                "query": """
                mutation variableCollectionUpsert($input: VariableCollectionUpsertInput!) {
                  variableCollectionUpsert(input: $input)
                }
                """,
                "variables": {
                    "input": {
                        "projectId": project_id,
                        "environmentId": environment_id,
                        "serviceId": service_id,
                        "variables": variables
                    }
                }
            }
            try:
                url = "https://backboard.railway.app/graphql/v2"
                resp = requests.post(url, headers=headers, json=payload, timeout=15)
                if resp.status_code == 405:
                    resp = requests.patch(url, headers=headers, json=payload, timeout=15)
                if resp.ok:
                    print("[QBO] Tokens refreshed and saved to Railway variables.")
                else:
                    print(f"[QBO] Railway variables update failed: {resp.text}")
            except Exception as e:
                print(f"[QBO] Exception updating Railway vars: {e}")
        else:
            try:
                set_key(self._env_path, "QBO_ACCESS_TOKEN",  access_token)
                set_key(self._env_path, "QBO_REFRESH_TOKEN", refresh_token)
                if realm_id:
                    set_key(self._env_path, "QBO_REALM_ID", realm_id)
                print("[QBO] Tokens refreshed and saved to .env")
            except Exception as e:
                print(f"[QBO] Warning: could not write tokens to .env: {e}")

    def _do_refresh(self) -> bool:
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded     = base64.b64encode(credentials.encode()).decode()
        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type":  "application/x-www-form-urlencoded",
            "Accept":        "application/json",
        }
        data = {
            "grant_type":    "refresh_token",
            "refresh_token": self.refresh_token,
        }
        try:
            resp = requests.post(TOKEN_URL, headers=headers, data=data, timeout=15)
            resp.raise_for_status()
            token_data = resp.json()
            self._save_tokens(token_data["access_token"], token_data["refresh_token"])
            return True
        except Exception as e:
            print(f"[QBO] Token refresh failed: {e}")
            return False

    # ── Authenticated Request ────────────────────────────────────────────────

    def _request(self, method: str, endpoint: str, retry: bool = True, **kwargs) -> requests.Response:
        url = f"{self.base_url}/v3/company/{self.realm_id}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }
        headers.update(kwargs.pop("extra_headers", {}))

        resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)

        if resp.status_code == 401 and retry:
            print("[QBO] 401 received — refreshing token and retrying...")
            if self._do_refresh():
                headers["Authorization"] = f"Bearer {self.access_token}"
                resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)

        return resp

    # ── Tax Code Management ──────────────────────────────────────────────────

    def _get_tax_rate_map(self) -> dict:
        """
        Query QBO for active TaxCode objects and build a name → ID map.
        """
        if self._tax_rate_map is not None:
            return self._tax_rate_map

        self._tax_rate_map = {}
        try:
            query = "SELECT * FROM TaxCode WHERE Active = true MAXRESULTS 100"
            resp = self._request("GET", "query", params={"query": query})

            if resp.status_code != 200:
                print(f"[QBO] TaxCode query failed: {resp.status_code} — {resp.text[:200]}")
                return self._tax_rate_map

            tax_codes = resp.json().get("QueryResponse", {}).get("TaxCode", [])
            print(f"[QBO] Found {len(tax_codes)} active TaxCode(s):")

            for tc in tax_codes:
                tc_id   = str(tc.get("Id", ""))
                tc_name = tc.get("Name", "")
                self._tax_rate_map[tc_name] = tc_id
                print(f"[QBO]   TaxCode '{tc_name}' -> ID {tc_id}")

        except Exception as e:
            print(f"[QBO] _get_tax_rate_map error: {e}")

        return self._tax_rate_map

    def _resolve_tax_code_by_name(self, name: str) -> dict:
        """
        Resolve a tax-code display name to a QBO TaxCodeRef.
        Uses exact match → substring match → fallback to NON.
        """
        if not name:
            return {"value": "NON"}

        rate_map = self._get_tax_rate_map()

        # Exact match
        if name in rate_map:
            return {"value": rate_map[name]}

        # Case-insensitive exact match
        name_lower = name.lower()
        for tc_name, tc_id in rate_map.items():
            if tc_name.lower() == name_lower:
                return {"value": tc_id}

        # Substring match in either direction
        for tc_name, tc_id in rate_map.items():
            tc_lower = tc_name.lower()
            if name_lower in tc_lower or tc_lower in name_lower:
                print(f"[QBO] Tax code '{name}' matched to '{tc_name}' (substring)")
                return {"value": tc_id}

        print(f"[QBO] Warning: Tax code '{name}' not found in QBO, falling back to NON")
        return {"value": "NON"}

    # ── Location & Terms Mapping ─────────────────────────────────────────────

    def _get_location_map(self) -> dict:
        if getattr(self, '_loc_map', None) is not None:
            return self._loc_map

        self._loc_map = {}
        try:
            for entity, ref_type in [("Department", "DepartmentRef"), ("Location", "LocationRef")]:
                resp = self._request("GET", "query", params={"query": f"SELECT * FROM {entity} WHERE Active = true MAXRESULTS 100"})
                if resp.status_code == 200:
                    items = resp.json().get("QueryResponse", {}).get(entity, [])
                    for item in items:
                        self._loc_map[item.get("Name", "")] = {"value": str(item.get("Id", "")), "type": ref_type}
        except Exception as e:
            print(f"[QBO] _get_location_map error: {e}")
        return self._loc_map

    def _resolve_location_by_name(self, name: str) -> Optional[dict]:
        if not name:
            return None
        loc_map = self._get_location_map()
        name_clean = name.lower().strip()
        best_match, best_score = None, 0
        for loc_name, loc_data in loc_map.items():
            score = max(
                fuzz.ratio(name_clean, loc_name.lower().strip()),
                fuzz.partial_ratio(name_clean, loc_name.lower().strip()),
            )
            if score > best_score:
                best_score, best_match = score, loc_data
        if best_score >= FUZZY_MATCH_THRESHOLD and best_match:
            print(f"[QBO] Mapped Location '{name}' to {best_match['type']} ID {best_match['value']} (score={best_score})")
            return best_match
        return None

    def _get_term_map(self) -> dict:
        if getattr(self, '_term_map', None) is not None:
            return self._term_map
        self._term_map = {}
        try:
            resp = self._request("GET", "query", params={"query": "SELECT * FROM Term WHERE Active = true MAXRESULTS 100"})
            if resp.status_code == 200:
                items = resp.json().get("QueryResponse", {}).get("Term", [])
                for item in items:
                    self._term_map[item.get("Name", "")] = {"value": str(item.get("Id", ""))}
        except Exception as e:
            print(f"[QBO] _get_term_map error: {e}")
        return self._term_map

    def _resolve_term_by_name(self, name: str) -> Optional[dict]:
        if not name:
            return None
        term_map = self._get_term_map()
        name_clean = name.lower().strip()
        best_match, best_score = None, 0
        for term_name, term_data in term_map.items():
            score = max(
                fuzz.ratio(name_clean, term_name.lower().strip()),
                fuzz.partial_ratio(name_clean, term_name.lower().strip()),
            )
            if score > best_score:
                best_score, best_match = score, term_data
        if best_score >= FUZZY_MATCH_THRESHOLD and best_match:
            print(f"[QBO] Mapped Term '{name}' to ID {best_match['value']} (score={best_score})")
            return best_match
        return None

    # ── Accounts Management ──────────────────────────────────────────────────

    def _get_default_expense_account(self) -> dict:
        if self.default_expense_account:
            return self.default_expense_account
        try:
            query = "SELECT * FROM Account WHERE AccountType = 'Expense' AND SubAccount = false MAXRESULTS 1"
            resp = self._request("GET", "query", params={"query": query})
            if resp.status_code == 200:
                accounts = resp.json().get("QueryResponse", {}).get("Account", [])
                if accounts:
                    acc = accounts[0]
                    self.default_expense_account = {
                        "value": str(acc.get("Id")),
                        "name": str(acc.get("Name"))
                    }
                    print(f"[QBO] Found default expense account: {self.default_expense_account}")
                    return self.default_expense_account
            return {"value": "1", "name": "Uncategorized Expense"}
        except Exception as e:
            print(f"[QBO] _get_default_expense_account error: {e}")
            return {"value": "1", "name": "Uncategorized Expense"}

    def _get_expense_account_by_name(self, account_name: str) -> dict:
        if not account_name or not account_name.strip():
            return self._get_default_expense_account()
        name_clean = account_name.lower().strip()
        if name_clean in self.gl_cache:
            return self.gl_cache[name_clean]
        try:
            query = "SELECT * FROM Account WHERE AccountType = 'Expense' AND SubAccount = false MAXRESULTS 100"
            resp = self._request("GET", "query", params={"query": query})
            if resp.status_code == 200:
                accounts = resp.json().get("QueryResponse", {}).get("Account", [])
                best_account, best_score = None, 0
                for acc in accounts:
                    display_name = acc.get("Name", "")
                    score = max(
                        fuzz.ratio(name_clean, display_name.lower().strip()),
                        fuzz.partial_ratio(name_clean, display_name.lower().strip()),
                    )
                    if score > best_score:
                        best_score, best_account = score, acc
                if best_score >= FUZZY_MATCH_THRESHOLD and best_account:
                    matched_ref = {
                        "value": str(best_account.get("Id")),
                        "name": str(best_account.get("Name"))
                    }
                    print(f"[QBO] GL Code '{account_name}' matched to QBO Account: '{matched_ref['name']}' (score={best_score})")
                    self.gl_cache[name_clean] = matched_ref
                    return matched_ref
                else:
                    print(f"[QBO] No GL Code match for '{account_name}' (best score={best_score}). Using fallback.")
        except Exception as e:
            print(f"[QBO] _get_expense_account_by_name error: {e}")
        fallback = self._get_default_expense_account()
        self.gl_cache[name_clean] = fallback
        return fallback

    def _resolve_gl_account(self, gl_name: str) -> Tuple[dict, bool]:
        if not gl_name or not gl_name.strip():
            return self._get_default_expense_account(), False
        name_clean = gl_name.lower().strip()
        if name_clean in self.gl_cache:
            return self.gl_cache[name_clean], True
        all_accounts = []
        for acct_type in ["Expense", "Cost of Goods Sold"]:
            try:
                query = f"SELECT * FROM Account WHERE AccountType = '{acct_type}' AND SubAccount = false MAXRESULTS 100"
                resp = self._request("GET", "query", params={"query": query})
                if resp.status_code == 200:
                    accounts = resp.json().get("QueryResponse", {}).get("Account", [])
                    all_accounts.extend(accounts)
            except Exception as e:
                print(f"[QBO] Error querying {acct_type} accounts: {e}")
        best_account, best_score = None, 0
        for acc in all_accounts:
            display_name = acc.get("Name", "")
            score = max(
                fuzz.ratio(name_clean, display_name.lower().strip()),
                fuzz.partial_ratio(name_clean, display_name.lower().strip()),
            )
            if score > best_score:
                best_score, best_account = score, acc
        if best_score >= FUZZY_MATCH_THRESHOLD and best_account:
            matched_ref = {
                "value": str(best_account.get("Id")),
                "name": str(best_account.get("Name"))
            }
            print(f"[QBO] GL '{gl_name}' → '{matched_ref['name']}' (score={best_score})")
            self.gl_cache[name_clean] = matched_ref
            return matched_ref, True
        print(f"[QBO] GL '{gl_name}' not found in QBO (best={best_score}). Falling back.")
        fallback = self._get_default_expense_account()
        self.gl_cache[name_clean] = fallback
        return fallback, False

    def get_all_account_names(self) -> list:
        if getattr(self, '_all_account_names', None) is not None:
            return self._all_account_names
        self._all_account_names = []
        try:
            for acct_type in ["Expense", "Cost of Goods Sold", "Other Expense"]:
                query = f"SELECT * FROM Account WHERE AccountType = '{acct_type}' AND Active = true MAXRESULTS 200"
                resp = self._request("GET", "query", params={"query": query})
                if resp.status_code == 200:
                    accounts = resp.json().get("QueryResponse", {}).get("Account", [])
                    for acc in accounts:
                        name = acc.get("Name", "").strip()
                        if name:
                            self._all_account_names.append(name)
            print(f"[QBO] Fetched {len(self._all_account_names)} account names for GPT-4o prompt")
        except Exception as e:
            print(f"[QBO] get_all_account_names error: {e}")
        return self._all_account_names

    def get_all_accounts_map(self) -> dict:
        if getattr(self, "_accounts_map", None) is not None:
            return self._accounts_map
        self._accounts_map: dict = {}
        try:
            for acct_type in ["Expense", "Cost of Goods Sold", "Other Expense"]:
                query = f"SELECT * FROM Account WHERE AccountType = '{acct_type}' AND Active = true MAXRESULTS 200"
                resp = self._request("GET", "query", params={"query": query})
                if resp.status_code == 200:
                    accounts = resp.json().get("QueryResponse", {}).get("Account", [])
                    for acc in accounts:
                        name = acc.get("Name", "").strip()
                        if name:
                            self._accounts_map[name.lower()] = {
                                "value": str(acc["Id"]),
                                "name":  name,
                            }
            print(f"[QBO] Built accounts map with {len(self._accounts_map)} entries.")
        except Exception as exc:
            print(f"[QBO] get_all_accounts_map error: {exc}")
        return self._accounts_map

    # ── Vendor Management ────────────────────────────────────────────────────

    def _validate_vendor(self, vendor_id: str) -> Optional[Dict]:
        try:
            resp = self._request("GET", f"vendor/{vendor_id}")
            if resp.status_code == 200:
                vendor = resp.json().get("Vendor", {})
                if vendor.get("Active", True):
                    return vendor
                return None
            return None
        except Exception as e:
            print(f"[QBO] _validate_vendor error: {e}")
            return None

    def _vendor_currency(self, vendor: Optional[Dict]) -> str:
        """Extract currency code from a QBO Vendor dict, defaulting to home currency."""
        if vendor and isinstance(vendor.get("CurrencyRef"), dict):
            return vendor["CurrencyRef"].get("value", self.home_currency)
        return self.home_currency

    def find_vendor(self, name: str) -> Optional[dict]:
        try:
            query = "SELECT * FROM Vendor WHERE Active = true MAXRESULTS 100"
            resp  = self._request("GET", "query", params={"query": query})
            if resp.status_code != 200:
                return None
            vendors = resp.json().get("QueryResponse", {}).get("Vendor", [])
            best_vendor, best_score = None, 0
            name_clean  = name.lower().strip()
            for vendor in vendors:
                display_name = vendor.get("DisplayName", "")
                score = max(
                    fuzz.ratio(name_clean, display_name.lower().strip()),
                    fuzz.partial_ratio(name_clean, display_name.lower().strip()),
                )
                if score > best_score:
                    best_score, best_vendor = score, vendor
            if best_score >= FUZZY_MATCH_THRESHOLD:
                print(f"[QBO] Vendor matched via API: '{best_vendor['DisplayName']}' (score={best_score})")
                self.vendor_cache[name_clean] = best_vendor.get("Id")
                return best_vendor
            print(f"[QBO] No vendor match for '{name}' (best score={best_score})")
            return None
        except Exception as e:
            print(f"[QBO] find_vendor error: {e}")
            return None

    def create_vendor(self, name: str, currency_code: str = None) -> Optional[dict]:
        currency_code = currency_code or self.home_currency
        try:
            payload = {
                "DisplayName":      name,
                "PrintOnCheckName": name,
                "CurrencyRef": {"value": currency_code}
            }
            resp = self._request("POST", "vendor", json=payload)
            if resp.status_code in (200, 201):
                vendor = resp.json().get("Vendor", {})
                vendor_id = vendor.get("Id")
                print(f"[QBO] Created vendor: '{vendor.get('DisplayName')}' (ID={vendor_id}, currency={currency_code})")
                name_clean = name.lower().strip()
                self.vendor_cache[name_clean] = vendor_id
                return vendor
            else:
                print(f"[QBO] create_vendor failed: {resp.status_code} — {resp.text[:300]}")
                return None
        except Exception as e:
            print(f"[QBO] create_vendor error: {e}")
            return None

    def get_or_create_vendor(self, name: str, currency_code: str = None) -> Tuple[Optional[str], str]:
        currency_code = currency_code or self.home_currency
        if not name or not name.strip():
            return None, currency_code

        name_clean = name.lower().strip()

        if name_clean in self.vendor_cache:
            cached_id = self.vendor_cache[name_clean]
            print(f"[QBO] Vendor '{name}' found in local cache (ID={cached_id}) — validating...")
            vendor = self._validate_vendor(cached_id)
            if vendor:
                vcur = self._vendor_currency(vendor)
                print(f"[QBO] Cached vendor validated (ID={cached_id}, currency={vcur})")
                return cached_id, vcur
            print(f"[QBO] Cached vendor ID={cached_id} is invalid/deleted — evicting from cache.")
            del self.vendor_cache[name_clean]

        vendor = self.find_vendor(name)
        if not vendor:
            print(f"[QBO] Creating new vendor: '{name}' with currency {currency_code}")
            vendor = self.create_vendor(name, currency_code=currency_code)

        if vendor:
            return vendor.get("Id"), self._vendor_currency(vendor)
        return None, currency_code

    # ── Bill Verification ────────────────────────────────────────────────────

    def check_duplicate_bill(self, vendor_id: str, total_amount: float, txn_date: str) -> bool:
        try:
            query = f"SELECT * FROM Bill WHERE VendorRef = '{vendor_id}' AND TxnDate = '{txn_date}' MAXRESULTS 50"
            resp = self._request("GET", "query", params={"query": query})
            if resp.status_code != 200:
                return False
            bills = resp.json().get("QueryResponse", {}).get("Bill", [])
            for bill in bills:
                bill_amount = float(bill.get("TotalAmt", 0.0))
                if abs(bill_amount - total_amount) < 0.01:
                    print(f"[QBO] Duplicate bill found in QBO: ID={bill.get('Id')} for Amount={total_amount}")
                    return True
            return False
        except Exception as e:
            print(f"[QBO] check_duplicate_bill error: {e}")
            return False

    # ── Exchange Rates ──────────────────────────────────────────────────────

    def get_exchange_rate(self, currency_code: str, as_of_date: str) -> float:
        """
        Fetch the exchange rate to convert FROM currency_code TO home_currency.
        Returns 1.0 if currency matches the home currency.
        """
        if currency_code == self.home_currency:
            return 1.0

        try:
            query = f"sourcecurrencycode={currency_code}&asofdate={as_of_date}"
            resp = self._request("GET", f"exchangerate?{query}")
            if resp.status_code == 200:
                rate = resp.json().get("ExchangeRate", {}).get("Rate")
                if rate:
                    print(f"[QBO] Fetched Exchange Rate: 1 {currency_code} = {rate} {self.home_currency} as of {as_of_date}")
                    return float(rate)
            else:
                print(f"[QBO] Warning: Failed to fetch exchange rate ({resp.status_code}): {resp.text[:200]}")
        except Exception as e:
            print(f"[QBO] get_exchange_rate error: {e}")

        # Sensible fallback rates so the bill can still post even if QBO's
        # exchangerate endpoint is unresponsive. Adjust as needed.
        if self.home_currency == "PKR":
            fallback_rates = {"USD": 278.0, "AED": 75.5, "EUR": 300.0, "GBP": 350.0, "SAR": 74.0}
            if currency_code in fallback_rates:
                rate = fallback_rates[currency_code]
                print(f"[QBO] Using fallback exchange rate: 1 {currency_code} = {rate} PKR")
                return rate

        print(f"[QBO] Warning: No fallback rate for {currency_code}. Defaulting to 1.0.")
        return 1.0

    # ── Bill Posting ─────────────────────────────────────────────────────────

    def post_bill(self, invoice_data: dict, vendor_id: str, vendor_currency: str = None) -> Tuple[str, str]:
        """
        Post a Bill to QBO for the given vendor.
        Returns (status, bill_id) where status is 'posted' or 'failed'.
        """
        vendor_currency = vendor_currency or self.home_currency

        try:
            # ── Dates ─────────────────────────────────────────────
            raw_date = str(invoice_data.get("date", "") or "").strip()
            txn_date = raw_date if len(raw_date) >= 10 else date.today().isoformat()

            raw_due  = str(invoice_data.get("due_date", "") or "").strip()
            due_date = raw_due if len(raw_due) >= 10 else txn_date

            # ── Amounts ───────────────────────────────────────────
            total_amount = float(invoice_data.get("total_amount", 0.0) or 0.0)

            # ── Line Items ────────────────────────────────────────
            line_items = invoice_data.get("line_items", []) or []
            if not line_items:
                line_items = [{
                    "description": invoice_data.get("description", "Invoice Items"),
                    "amount": total_amount,
                }]

            # ── Fallback GL account ───────────────────────────────
            fallback_gl_ref = invoice_data.get("gl_account_ref")
            if not fallback_gl_ref:
                fallback_gl_ref = self._get_expense_account_by_name(
                    invoice_data.get("gl_code_suggested", "")
                )

            # ── Per-line tax codes & GL accounts ──────────────────
            gl_mismatch_notes = []

            qbo_lines = []
            for i, item in enumerate(line_items, start=1):
                item_amount = float(item.get("amount", 0.0) or 0.0)
                if item_amount <= 0:
                    continue

                # Per-line tax code (Pakistan: GST / EX / ZR via gst_processor)
                line_tax_name = item.get("qbo_tax_code", "Exempt")
                line_tax_ref = self._resolve_tax_code_by_name(line_tax_name)

                description = str(item.get("description", "") or "")

                if self.gl_classifier is not None:
                    accounts_map = self.get_all_accounts_map()
                    gl_name, matched_kw = self.gl_classifier.classify_line(description)

                    if gl_name:
                        gl_key = gl_name.lower().strip()
                        if gl_key in accounts_map:
                            line_gl_ref = accounts_map[gl_key]
                            print(f"[QBO] Line {i}: GL='{gl_name}' (keyword='{matched_kw}') → ID={line_gl_ref['value']}")
                        else:
                            gl_mismatch_notes.append(
                                f"Line {i}: GL '{gl_name}' matched in sheet but not in QBO — used Uncategorised Expense"
                            )
                            line_gl_ref, _ = self._resolve_gl_account("Uncategorized Expense")
                    else:
                        self.gl_classifier.log_pending_review_line(item, invoice_data)
                        line_gl_ref, _ = self._resolve_gl_account("Uncategorized Expense")
                        gl_mismatch_notes.append(
                            f"Line {i}: '{description[:40]}' — no GL rule matched, logged to Pending Review"
                        )
                else:
                    line_gl_name = item.get("gl_code", "") or ""
                    if line_gl_name:
                        line_gl_ref, matched = self._resolve_gl_account(line_gl_name)
                        if not matched:
                            gl_mismatch_notes.append(
                                f"Line {i}: GL '{line_gl_name}' not found in QBO, used '{line_gl_ref.get('name', 'fallback')}'"
                            )
                    else:
                        line_gl_ref = fallback_gl_ref

                print(f"[QBO] Line {i}: tax='{line_tax_name}' → {line_tax_ref}, GL='{line_gl_ref.get('name', '?')}'")

                qbo_lines.append({
                    "Id":         str(i),
                    "Amount":     round(item_amount, 2),
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef":    line_gl_ref,
                        "BillableStatus": "NotBillable",
                        "TaxCodeRef":     line_tax_ref,
                    },
                    "Description": str(item.get("description", "") or ""),
                })

            if not qbo_lines:
                fallback_tax_ref = self._resolve_tax_code_by_name("Exempt")
                qbo_lines = [{
                    "Id":         "1",
                    "Amount":     max(round(total_amount, 2), 0.01),
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef":    fallback_gl_ref,
                        "BillableStatus": "NotBillable",
                        "TaxCodeRef":     fallback_tax_ref,
                    },
                    "Description": "Invoice",
                }]

            # ── Currency & Exchange Rate ──────────────────────────
            invoice_currency = str(invoice_data.get("currency", self.home_currency) or self.home_currency).upper()
            currency_code = vendor_currency

            if invoice_currency != currency_code:
                print(f"[QBO] Currency mismatch: invoice says '{invoice_currency}' but vendor is '{currency_code}'. Using vendor currency.")

            exchange_rate = self.get_exchange_rate(currency_code, txn_date)

            # ── Memo ───────────────────────────────────────────────
            memo_text = ""
            if invoice_data.get("manual_review_memo"):
                memo_text = f" | {invoice_data.get('manual_review_memo')}"
            if gl_mismatch_notes:
                memo_text += " | GL: " + "; ".join(gl_mismatch_notes)

            # ── Terms / Location ──────────────────────────────────
            credit_terms = str(invoice_data.get("credit_terms", "") or "").strip()
            purchase_loc = str(invoice_data.get("purchase_location", "") or "").strip()
            term_ref = self._resolve_term_by_name(credit_terms)
            loc_ref = self._resolve_location_by_name(purchase_loc)

            payload = {
                "VendorRef": {"value": vendor_id},
                "Line":      qbo_lines,
                "TxnDate":   txn_date,
                "DueDate":   due_date,
                "DocNumber": str(invoice_data.get("invoice_number", "") or "")[:21],
                "CurrencyRef": {"value": currency_code},
                "ExchangeRate": exchange_rate,
                "GlobalTaxCalculation": "TaxExcluded",  # Pakistan: never tax-inclusive
                "PrivateNote": (
                    f"Auto-imported{memo_text} | "
                    f"File: {invoice_data.get('file_id', '')} | "
                    f"Supplier: {invoice_data.get('supplier_name', '')} | "
                    f"NTN: {invoice_data.get('supplier_ntn', '') or invoice_data.get('supplier_trn', '')}"
                )[:4000],
            }

            if term_ref:
                payload["SalesTermRef"] = term_ref
            if loc_ref:
                loc_ref_copy = loc_ref.copy()
                ref_type = loc_ref_copy.pop("type", "LocationRef")
                payload[ref_type] = loc_ref_copy

            print(f"[QBO] Sending Bill payload: {json.dumps(payload, indent=2)}")

            resp = self._request("POST", "bill", json=payload)

            if resp.status_code in (200, 201):
                bill    = resp.json().get("Bill", {})
                bill_id = str(bill.get("Id", ""))
                print(f"[QBO] Success: Bill posted — ID: {bill_id}")
                return "posted", bill_id
            else:
                print(f"[QBO] post_bill failed: {resp.status_code} — {resp.text}")
                return "failed", ""

        except Exception as e:
            print(f"[QBO] post_bill error: {e}")
            import traceback; traceback.print_exc()
            return "failed", ""

    # ── Document Attachment ──────────────────────────────────────────────────

    def attach_document(self, bill_id: str, file_path: str) -> bool:
        if not os.path.exists(file_path):
            print(f"[QBO] Cannot attach document: file not found at {file_path}")
            return False
        try:
            filename = os.path.basename(file_path)
            ext = filename.lower()
            if ext.endswith(".pdf"): mime_type = "application/pdf"
            elif ext.endswith(".png"): mime_type = "image/png"
            elif ext.endswith(".jpg") or ext.endswith(".jpeg"): mime_type = "image/jpeg"
            else: mime_type = "application/octet-stream"

            request_metadata = {
                "AttachableRef": [{"EntityRef": {"type": "Bill", "value": str(bill_id)}}],
                "FileName": filename,
                "ContentType": mime_type
            }
            with open(file_path, "rb") as f:
                file_content = f.read()

            m = MultipartEncoder(fields={
                'file_metadata_01': ('', json.dumps(request_metadata), 'application/json'),
                'file_content_01': (filename, file_content, mime_type)
            })

            url = f"{self.base_url}/v3/company/{self.realm_id}/upload"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": m.content_type,
                "Accept": "application/json"
            }
            resp = requests.post(url, headers=headers, data=m, timeout=45)
            if resp.status_code == 401:
                if self._do_refresh():
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    resp = requests.post(url, headers=headers, data=m, timeout=45)
            if resp.status_code in (200, 201):
                print(f"[QBO] Success: Document attached to Bill {bill_id}.")
                return True
            else:
                print(f"[QBO] Document attachment failed: {resp.status_code} — {resp.text[:400]}")
                return False
        except Exception as e:
            print(f"[QBO] attach_document error: {e}")
            return False

    # ── Public Entry Point ──────────────────────────────────────────────────

    def sync(self, invoice_data: dict, file_path: str = None) -> Tuple[str, str]:
        """
        Main entry point.
        Steps:
          1. Pre-posting validation
          2. Resolve vendor
          3. Duplicate check
          4. Post Bill
          5. Attach document
        """
        supplier = str(invoice_data.get("supplier_name", "") or "").strip()
        total_amount = float(invoice_data.get("total_amount", 0.0) or 0.0)
        raw_date = str(invoice_data.get("date", "") or "").strip()
        txn_date = raw_date if len(raw_date) >= 10 else date.today().isoformat()

        if not supplier or total_amount <= 0 or not raw_date:
            print("[QBO] Sync skipped: validation failed (missing vendor / positive amount / date).")
            return "needs_review", ""

        print(f"[QBO] sync() — vendor: '{supplier}' | Amount: {total_amount} | Date: {txn_date}")
        
        currency_code = str(invoice_data.get("currency", self.home_currency) or self.home_currency).upper()

        vendor_id, vendor_currency = self.get_or_create_vendor(supplier, currency_code=currency_code)
        if not vendor_id:
            return "failed", ""

        if self.check_duplicate_bill(vendor_id, total_amount, txn_date):
            print("[QBO] Duplicate detected. Skipping post.")
            return "duplicate_skipped", ""

        status, bill_id = self.post_bill(invoice_data, vendor_id, vendor_currency=vendor_currency)
        
        if status == "posted" and bill_id and file_path:
            self.attach_document(bill_id, file_path)

        return status, bill_id
