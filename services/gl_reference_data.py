from typing import List

# ── Pakistan-localised General Ledger Keyword Mapping ─────────────────────────
# Tuned for typical Pakistani SME / accounting-firm client invoices. Includes
# IT services, networking, training, cloud, and Pakistan-specific categories.

GL_KEYWORD_MAPPING = [
    # ── IT & Software Services ──────────────────────────────────────────────
    {
        "keywords": [
            "erp", "enterprise resource planning", "implementation",
            "software development", "software project", "custom software",
            "module", "integration", "api integration", "system integration",
        ],
        "gl_account": "Software & Technology",
        "priority": 1,
        "notes": "ERP / custom software / system implementation",
    },
    {
        "keywords": [
            "subscription", "saas", "license", "annual fee", "renewal",
            "office 365", "google workspace", "adobe", "zoom subscription",
            "slack", "github", "monday.com", "asana subscription",
        ],
        "gl_account": "Dues & Subscriptions",
        "priority": 1,
        "notes": "Software subscriptions and SaaS",
    },
    {
        "keywords": [
            "cloud hosting", "aws", "azure", "google cloud", "gcp",
            "vps", "dedicated server", "shared hosting", "vmware", "ec2",
            "s3 storage", "cloud storage", "ssl certificate", "domain",
            "cdn", "cloudflare",
        ],
        "gl_account": "Software & Technology",
        "priority": 1,
        "notes": "Cloud infrastructure / hosting / domains",
    },
    {
        "keywords": [
            "network infrastructure", "cabling", "switches", "router",
            "firewall", "access point", "wifi", "structured cabling",
            "patch panel", "rack", "ups",
        ],
        "gl_account": "Office Equipment",
        "priority": 1,
        "notes": "Network infrastructure and IT equipment",
    },
    {
        "keywords": [
            "amc", "annual maintenance", "software maintenance",
            "hardware maintenance", "support contract", "maintenance contract",
            "maintenance", "repair", "servicing", "fixing", "technical support",
        ],
        "gl_account": "Maintenance and Repair",
        "priority": 1,
        "notes": "Ongoing maintenance and repairs",
    },

    # ── Training & Development ──────────────────────────────────────────────
    {
        "keywords": [
            "training", "workshop", "seminar", "course", "certification",
            "end user training", "staff training", "on-site training",
            "webinar",
        ],
        "gl_account": "Training & Development",
        "priority": 1,
        "notes": "Staff training and development",
    },

    # ── Marketing & Advertising ─────────────────────────────────────────────
    {
        "keywords": [
            "facebook", "instagram", "meta", "campaign", "advertising",
            "ad spend", "sponsored", "boosted", "tiktok", "youtube ad",
            "social media ad", "digital ad", "google ads", "linkedin ad",
        ],
        "gl_account": "Advertising",
        "priority": 1,
        "notes": "Digital advertising spend",
    },
    {
        "keywords": [
            "marketing", "promotion", "expo", "exhibition", "branding",
            "billboard", "banner", "display", "lead generation",
            "media group", "press release",
        ],
        "gl_account": "Marketing",
        "priority": 2,
        "notes": "Marketing and promotional spend",
    },

    # ── Professional Services ──────────────────────────────────────────────
    {
        "keywords": [
            "accounting", "bookkeeping", "auditing", "audit", "tax filing",
            "lawyer", "legal", "compliance", "tax advisory",
            "professional fee", "consultation", "consultancy", "advisory",
            "secp", "fbr filing", "tax return",
        ],
        "gl_account": "Legal & Professional Fees",
        "priority": 1,
        "notes": "Accounting, legal, and professional advisory",
    },

    # ── Goods / COGS ───────────────────────────────────────────────────────
    {
        "keywords": [
            "raw material", "raw materials", "wholesale", "import",
            "stock", "inventory", "trading goods", "finished goods",
            "industrial motor", "conveyor belt", "stainless steel pipe",
            "lubricant", "drum",
        ],
        "gl_account": "Cost of Goods Sold",
        "priority": 1,
        "notes": "Goods bought for resale or manufacturing",
    },
    {
        "keywords": [
            "safety helmet", "safety equipment", "ppe",
            "work gloves", "safety boots", "high visibility",
        ],
        "gl_account": "Safety Equipment",
        "priority": 1,
        "notes": "PPE and safety equipment",
    },

    # ── Office & Admin ──────────────────────────────────────────────────────
    {
        "keywords": [
            "stationery", "printing", "office supplies", "supplies",
            "paper", "toner", "ink cartridge", "ream", "envelope",
            "binder", "file", "folder",
        ],
        "gl_account": "Office Supplies",
        "priority": 1,
        "notes": "Office and stationery supplies",
    },
    {
        "keywords": [
            "furniture", "sofa", "chair", "table", "cabinet", "shelf",
            "desk", "reception", "interior", "decor", "fit-out", "fitout",
        ],
        "gl_account": "Office Furniture",
        "priority": 1,
        "notes": "Furniture and interior fit-out",
    },

    # ── Utilities & Rent ────────────────────────────────────────────────────
    {
        "keywords": [
            "electricity", "k-electric", "lesco", "fesco", "iesco",
            "wapda", "gas bill", "ssgc", "sngpl", "water bill",
            "ptcl", "internet bill", "broadband", "fiber",
            "mobile bill", "telenor", "jazz", "ufone", "zong",
        ],
        "gl_account": "Utilities",
        "priority": 1,
        "notes": "Pakistan utility providers",
    },
    {
        "keywords": [
            "rent", "office rent", "shop rent", "warehouse rent",
            "monthly rent", "lease", "rental",
        ],
        "gl_account": "Rent",
        "priority": 1,
        "notes": "Premises rent",
    },

    # ── Travel & Vehicle ───────────────────────────────────────────────────
    {
        "keywords": [
            "fuel", "petrol", "diesel", "cng", "gasoline", "psg",
            "shell", "total parco", "go fuel", "pso",
        ],
        "gl_account": "Fuel & Vehicle",
        "priority": 1,
        "notes": "Vehicle fuel",
    },
    {
        "keywords": [
            "uber", "careem", "indrive", "bykea", "rickshaw",
            "taxi", "airfare", "pia", "airblue", "serene air",
            "hotel booking", "lodging",
        ],
        "gl_account": "Travel",
        "priority": 1,
        "notes": "Travel and transport",
    },

    # ── Banking ────────────────────────────────────────────────────────────
    {
        "keywords": [
            "bank charge", "bank fee", "transfer fee", "transaction fee",
            "wire fee", "service charge", "swift charges", "lc charges",
        ],
        "gl_account": "Bank Charges",
        "priority": 1,
        "notes": "Bank and remittance charges",
    },

    # ── HR / Pakistan-specific ─────────────────────────────────────────────
    {
        "keywords": [
            "eobi", "social security", "essi", "punjab essi",
            "pf contribution", "provident fund",
        ],
        "gl_account": "Employee Benefits",
        "priority": 1,
        "notes": "Pakistan statutory HR contributions",
    },
    {
        "keywords": [
            "insurance", "health insurance", "medical insurance",
            "vehicle insurance", "fire insurance", "policy premium",
            "takaful",
        ],
        "gl_account": "Insurance",
        "priority": 1,
        "notes": "Insurance premiums",
    },

    # ── Freight / Logistics ────────────────────────────────────────────────
    {
        "keywords": [
            "freight", "shipping", "courier", "tcs", "leopard courier",
            "m&p", "fedex", "dhl", "logistics", "transportation",
        ],
        "gl_account": "Freight & Delivery",
        "priority": 1,
        "notes": "Freight and courier",
    },
]


def build_gl_prompt_section(chart_of_accounts: List[str] = None) -> str:
    """
    Format the keyword mapping rules (and optionally the exact QBO Chart of
    Accounts) into a structured prompt section for GPT-4o.
    """
    prompt = "GL CODE CLASSIFICATION:\n"
    prompt += "Classify EACH line item's GL category by scanning the description against these keyword mapping rules.\n"
    prompt += "Use Priority 1 matches first. If multiple match, prefer the one with most overlap.\n"
    prompt += "If no keywords match, use general accounting knowledge to classify by expense nature.\n\n"
    prompt += "KEYWORD MAPPING (Format: GL Name: keywords...):\n"
    
    for rule in sorted(GL_KEYWORD_MAPPING, key=lambda x: x["priority"]):
        keywords_joined = ", ".join(rule["keywords"])
        prompt += f"- {rule['gl_account']}: {keywords_joined}\n"
    
    if chart_of_accounts and len(chart_of_accounts) > 0:
        prompt += "\nVALID QBO ACCOUNTS AVAILABLE (Must fall back to one of these if mapping rule fails):\n"
        prompt += ", ".join(chart_of_accounts) + "\n"
    else:
        prompt += "\nNOTE: If the matched GL account doesn't perfectly match a known QBO account, the system will fall back to Uncategorised Expense.\n"

    return prompt
