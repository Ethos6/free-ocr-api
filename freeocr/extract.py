"""Turn raw document text into structured, spreadsheet-ready data.

Built-in document types: invoice, receipt, bank_statement, purchase_order, payslip,
resume, id_document, generic. Everything is rule based, runs offline and is fast.
"""

from __future__ import annotations

import re
from typing import Any

from .parsers import (
    amounts_in, detect_currency, find_dates, money_in, parse_amount, parse_date, snake,
)

# --------------------------------------------------------------------------- classify

DOC_KEYWORDS: dict[str, list[tuple[str, int]]] = {
    "invoice": [("invoice", 5), ("tax invoice", 4), ("bill to", 3), ("invoice number", 4),
                ("invoice no", 4), ("due date", 2), ("amount due", 3), ("balance due", 3),
                ("subtotal", 1), ("payment terms", 2), ("gstin", 1), ("vat", 1), ("remit", 1)],
    "receipt": [("receipt", 5), ("thank you", 2), ("cashier", 3), ("change", 1), ("cash", 1),
                ("visa", 1), ("mastercard", 1), ("store", 1), ("transaction", 1), ("pos", 1),
                ("tip", 1), ("table", 1), ("server", 1), ("upi", 1)],
    "bank_statement": [("statement", 3), ("opening balance", 4), ("closing balance", 4),
                       ("account number", 2), ("withdrawal", 2), ("deposit", 2), ("debit", 1),
                       ("credit", 1), ("balance", 1), ("ifsc", 2), ("statement period", 4)],
    "purchase_order": [("purchase order", 6), ("po number", 4), ("p.o.", 2), ("ship to", 2),
                       ("vendor", 2), ("delivery date", 2)],
    "payslip": [("payslip", 6), ("pay slip", 6), ("salary slip", 6), ("earnings", 2),
                ("deductions", 2), ("net pay", 4), ("gross pay", 3), ("basic", 1), ("employee id", 2)],
    "resume": [("experience", 2), ("education", 2), ("skills", 2), ("projects", 1),
               ("curriculum vitae", 5), ("resume", 3), ("linkedin", 2), ("github", 1),
               ("certifications", 1), ("objective", 1), ("summary", 1)],
    "id_document": [("passport", 4), ("date of birth", 3), ("nationality", 3), ("driver", 2),
                    ("licence", 2), ("license", 2), ("identity", 2), ("place of birth", 3),
                    ("expiry", 1), ("sex", 1)],
}
DOC_TYPES = list(DOC_KEYWORDS) + ["generic"]


def classify(text: str) -> tuple[str, float, dict[str, int]]:
    low = text.lower()
    scores = {}
    for kind, words in DOC_KEYWORDS.items():
        scores[kind] = sum(w * min(low.count(k), 3) for k, w in words)
    best = max(scores, key=scores.get)
    total = sum(scores.values()) or 1
    if scores[best] < 4:
        return "generic", 0.3, scores
    conf = round(min(0.99, 0.4 + 0.6 * scores[best] / total), 2)
    return best, conf, scores


# --------------------------------------------------------------------------- helpers


def _lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines()]


def _label_rx(labels: list[str]) -> re.Pattern:
    alt = "|".join(re.escape(lbl).replace(r"\ ", r"[\s._-]*") for lbl in labels)
    return re.compile(rf"(?i)(?<![a-z])(?:{alt})(?![a-z])\s*(?:no\.?|number|#|num)?\s*[:：#.\-]?\s*")


def find_after(text: str, labels: list[str], kind: str = "string", dayfirst: bool | None = None,
               exclude: list[str] | None = None, next_line: bool = True) -> tuple[Any, float] | None:
    """Value that follows a label on the same line (or the next line)."""
    rx = _label_rx(labels)
    ex = _label_rx(exclude) if exclude else None
    lines = _lines(text)
    for i, line in enumerate(lines):
        for m in rx.finditer(line):
            prefix = line[max(0, m.start() - 12): m.start()].lower()
            if ex and ex.search(line[max(0, m.start() - 12): m.end()]):
                continue
            if kind == "amount" and re.search(r"sub\s*-?$", prefix):
                continue
            rest = line[m.end():].strip()
            # stop at the next "Label:" on the same line
            rest = re.split(r"\s{3,}(?=[A-Za-z][\w .#/-]{1,30}[:：])", rest)[0].strip()
            candidates = [(rest, 0.9)]
            if next_line and i + 1 < len(lines):
                candidates.append((lines[i + 1].strip(), 0.7))
            for cand, conf in candidates:
                if not cand:
                    continue
                v = coerce(cand, kind, dayfirst)
                if v not in (None, ""):
                    return v, conf
    return None


def coerce(raw: str, kind: str, dayfirst: bool | None = None) -> Any:
    raw = raw.strip()
    if kind == "amount":
        vals = money_in(raw) or amounts_in(raw)
        return vals[-1] if vals else None
    if kind == "number":
        vals = amounts_in(raw)
        return vals[0] if vals else None
    if kind == "int":
        m = re.search(r"-?\d+", raw.replace(",", ""))
        return int(m.group(0)) if m else None
    if kind == "date":
        return parse_date(raw, dayfirst)
    if kind == "id":
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9\-/_.]{1,40})", raw)
        if not m:
            return None
        tok = m.group(1).rstrip(".")
        if not re.search(r"\d", tok) or tok.lower() in ("date", "number", "no"):
            return None
        return tok
    if kind == "email":
        m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", raw)
        return m.group(0) if m else None
    if kind == "phone":
        m = re.search(r"\+?[\d()][\d\s().-]{6,}\d", raw)
        return m.group(0).strip() if m else None
    if kind == "percent":
        m = re.search(r"\d+(?:[.,]\d+)?\s?%", raw)
        return m.group(0).replace(" ", "") if m else None
    # string: trim trailing junk
    return re.sub(r"\s{2,}.*$", "", raw).strip(" :-") or None


def _set(fields: dict, conf: dict, key: str, found):
    if found is not None and fields.get(key) in (None, ""):
        fields[key], conf[key] = found


SKIP_HEADER = re.compile(
    r"(?i)^(tax\s+)?(invoice|receipt|bill|statement|purchase order|quotation|estimate|payslip|"
    r"pay slip|salary slip|page \d|original|copy|duplicate|cash memo|credit note|debit note)\b")


def _party_name(text: str) -> str | None:
    for line in _lines(text)[:8]:
        s = line.strip()
        if len(s) < 3 or SKIP_HEADER.match(s) or not re.search(r"[A-Za-z]{2}", s):
            continue
        if re.search(r"@|www\.|https?://|^\+?\d", s) or find_dates(s):
            continue
        if re.match(r"(?i)^(invoice|date|no\.?|number|bill|ship|to|from)\b", s):
            continue
        return re.split(r"\s{3,}", s)[0].strip()
    return None


def _block_after(text: str, labels: list[str], max_lines: int = 4) -> str | None:
    rx = _label_rx(labels)
    lines = _lines(text)
    for i, line in enumerate(lines):
        m = rx.search(line)
        if not m:
            continue
        rest = re.split(r"\s{3,}", line[m.end():].strip())[0].strip()
        block = [rest] if rest else []
        for nxt in lines[i + 1: i + 1 + max_lines]:
            s = re.split(r"\s{3,}", nxt.strip())[0].strip()
            if not s or re.match(r"(?i)^(ship to|invoice|date|due|description|item|qty|po\b|terms)", s):
                break
            block.append(s)
            if len(block) >= max_lines:
                break
        if block:
            return ", ".join(block)
    return None


# --------------------------------------------------------------------------- line items

ITEM_HEADER = re.compile(r"(?i)\b(description|item|product|service|particulars|details)\b")
TOTALS_LINE = re.compile(
    r"(?i)\b(sub\s*-?total|total|tax|vat|gst|cgst|sgst|igst|hst|pst|discount|shipping|delivery|"
    r"freight|balance|amount due|paid|change|tip|gratuity|rounding|round off|cash|card|visa|"
    r"mastercard|amex|upi|tender|net pay|gross)\b")
ITEM_LINE = re.compile(
    r"^\s*(?:(?P<sku>[A-Z0-9][A-Z0-9-]{2,})\s+)?(?P<desc>.*?[A-Za-z].*?)\s+"
    r"(?:(?P<qty>\d+(?:[.,]\d+)?)(?:\s?x)?\s+)?"
    r"(?:(?P<price>\(?-?[$€£₹¥]?\s?\d[\d,]*[.,]\d{2}\)?)\s+)?"
    r"(?P<amount>\(?-?[$€£₹¥]?\s?\d[\d,.]*\d\)?)\s*$")


def table_items(tables: list[list[list[str]]]) -> list[dict[str, Any]]:
    """Line items from real table structures (digital PDFs)."""
    items = []
    for table in tables:
        header = [snake(c) for c in table[0]]
        if not any(re.search(r"desc|item|product|service|particular", h) for h in header):
            continue
        col = {}
        for i, h in enumerate(header):
            if re.search(r"desc|item|product|service|particular", h) and "description" not in col:
                col["description"] = i
            elif re.search(r"qty|quantity|units|hours|hrs", h):
                col["quantity"] = i
            elif re.search(r"unit|rate|price", h) and "unit_price" not in col:
                col["unit_price"] = i
            elif re.search(r"amount|total|line|ext", h):
                col["amount"] = i
            elif re.search(r"sku|code|hsn|sac|part", h):
                col["sku"] = i
            elif re.search(r"tax|vat|gst", h):
                col["tax"] = i
        for row in table[1:]:
            if "description" not in col or col["description"] >= len(row):
                continue
            desc = row[col["description"]].strip()
            if not desc or TOTALS_LINE.match(desc):
                continue
            it: dict[str, Any] = {"description": desc}
            for k in ("quantity", "unit_price", "amount", "tax"):
                if k in col and col[k] < len(row):
                    it[k] = parse_amount(row[col[k]])
            if "sku" in col and col["sku"] < len(row):
                it["sku"] = row[col["sku"]].strip() or None
            items.append(it)
    return items


def text_items(text: str) -> list[dict[str, Any]]:
    """Line items from plain text rows like 'Widget  2  10.00  20.00'."""
    lines = _lines(text)
    start = 0
    for i, ln in enumerate(lines):
        if ITEM_HEADER.search(ln) and re.search(r"(?i)\b(qty|quantity|amount|price|rate|total|hrs)\b", ln):
            start = i + 1
            break
    items = []
    for ln in lines[start:]:
        s = ln.strip()
        if not s:
            continue
        if TOTALS_LINE.search(s) and items:
            break
        if TOTALS_LINE.search(s) or find_dates(s) and len(money_in(s)) < 1:
            continue
        m = ITEM_LINE.match(s)
        if not m:
            continue
        desc = re.sub(r"\s{2,}", " ", m.group("desc")).strip(" .:-")
        amount = parse_amount(m.group("amount"))
        if amount is None or len(desc) < 2 or re.fullmatch(r"[\d\W]+", desc):
            continue
        if not start and not money_in(m.group("amount")):
            continue  # without a table header be strict: amount must look like money
        it: dict[str, Any] = {"description": desc}
        if m.group("sku"):
            it["sku"] = m.group("sku")
        qty = parse_amount(m.group("qty")) if m.group("qty") else None
        price = parse_amount(m.group("price")) if m.group("price") else None
        if qty is not None and price is None and re.fullmatch(r"\d+[.,]\d{2}", m.group("qty")):
            # "Widget 18.50 74.00": the first number is a unit price, not a quantity
            price, qty = qty, None
        if price and qty is None:
            guess = amount / price
            if abs(guess - round(guess)) < 0.01 and round(guess) > 0:
                qty = float(round(guess))
        if qty is not None:
            it["quantity"] = qty
        if price is not None:
            it["unit_price"] = price
        it["amount"] = amount
        items.append(it)
        if len(items) >= 500:
            break
    return items


# --------------------------------------------------------------------------- doc types


def extract_invoice(text: str, tables: list, dayfirst: bool | None = None, po: bool = False) -> tuple[dict, dict, list]:
    f: dict[str, Any] = {}
    c: dict[str, float] = {}
    num_labels = ["purchase order", "po", "p.o."] if po else ["invoice", "inv", "bill", "receipt", "document"]
    _set(f, c, "po_number" if po else "invoice_number", find_after(text, num_labels, "id", dayfirst))
    if not po:
        _set(f, c, "po_number", find_after(text, ["purchase order", "po", "p.o.", "order"], "id", dayfirst))
    _set(f, c, "invoice_date" if not po else "order_date",
         find_after(text, ["invoice date", "date of issue", "issue date", "bill date", "order date",
                           "dated", "date"], "date", dayfirst, exclude=["due date", "delivery date"]))
    _set(f, c, "due_date", find_after(text, ["due date", "payment due", "due by", "due on", "pay by"], "date", dayfirst))
    if po:
        _set(f, c, "delivery_date", find_after(text, ["delivery date", "ship date", "required by"], "date", dayfirst))
    if f.get("invoice_date") is None and f.get("order_date") is None:
        ds = find_dates(text, dayfirst)
        if ds:
            f["invoice_date" if not po else "order_date"], c["invoice_date" if not po else "order_date"] = ds[0][2], 0.5
    name = _party_name(text)
    if name:
        f["vendor_name"], c["vendor_name"] = name, 0.6
    _set(f, c, "customer", (lambda v: (v, 0.7) if v else None)(
        _block_after(text, ["bill to", "billed to", "invoice to", "sold to", "customer", "client", "buyer"])))
    _set(f, c, "ship_to", (lambda v: (v, 0.7) if v else None)(_block_after(text, ["ship to", "deliver to", "shipping address"])))
    _set(f, c, "payment_terms", find_after(text, ["payment terms", "terms"], "string", dayfirst, next_line=False))
    _set(f, c, "currency", (lambda v: (v, 0.8) if v else None)(detect_currency(text)))
    _set(f, c, "subtotal", find_after(text, ["subtotal", "sub total", "sub-total", "net amount", "taxable value",
                                             "total before tax"], "amount", dayfirst, next_line=False))
    _set(f, c, "tax", find_after(text, ["total tax", "tax amount", "vat", "gst", "igst", "sales tax", "tax", "hst"],
                                 "amount", dayfirst, exclude=["tax invoice", "tax id", "vat no", "vat number",
                                                              "gstin", "tax rate", "vat id", "gst no"], next_line=False))
    _set(f, c, "discount", find_after(text, ["discount"], "amount", dayfirst, next_line=False))
    _set(f, c, "shipping", find_after(text, ["shipping", "freight", "delivery charges", "postage"], "amount", dayfirst, next_line=False))
    total = find_after(text, ["grand total", "total amount due", "amount due", "balance due", "total due",
                              "invoice total", "total payable", "net payable", "amount payable"], "amount", dayfirst, next_line=False) \
        or find_after(text, ["total amount", "total"], "amount", dayfirst, next_line=False,
                      exclude=["subtotal", "sub total", "total tax", "total qty", "total items", "total quantity"])
    _set(f, c, "total", total)
    if f.get("total") is None:
        all_money = money_in(text)
        if all_money:
            f["total"], c["total"] = max(all_money), 0.4
    _set(f, c, "amount_paid", find_after(text, ["amount paid", "paid", "payments"], "amount", dayfirst, next_line=False))
    _set(f, c, "vendor_tax_id", find_after(text, ["gstin", "vat no", "vat number", "vat id", "tax id", "abn", "ein",
                                                  "trn", "tin", "gst no", "gst number"], "id", dayfirst, next_line=False))
    _set(f, c, "email", find_after(text, ["email", "e-mail"], "email", dayfirst, next_line=False))
    _set(f, c, "phone", find_after(text, ["phone", "tel", "mobile", "ph", "contact"], "phone", dayfirst, next_line=False))
    _set(f, c, "iban", find_after(text, ["iban"], "string", dayfirst, next_line=False))
    _set(f, c, "account_number", find_after(text, ["account number", "account no", "a/c no", "acct"], "id", dayfirst, next_line=False))

    items = table_items(tables) or text_items(text)
    # sanity: fill subtotal from items, check total consistency
    if items and f.get("subtotal") is None:
        s = round(sum(i.get("amount") or 0 for i in items), 2)
        if s:
            f["subtotal"], c["subtotal"] = s, 0.5
    if f.get("total") is not None and f.get("subtotal") is not None:
        expected = round((f["subtotal"] or 0) + (f.get("tax") or 0) + (f.get("shipping") or 0)
                         - abs(f.get("discount") or 0), 2)
        f["totals_check"] = "ok" if abs(expected - f["total"]) < 0.02 else "mismatch"
    return f, c, items


def extract_receipt(text: str, tables: list, dayfirst: bool | None = None) -> tuple[dict, dict, list]:
    f, c, items = extract_invoice(text, tables, dayfirst)
    f.pop("invoice_number", None)
    f.pop("due_date", None)
    f["merchant"] = f.pop("vendor_name", None)
    c["merchant"] = c.pop("vendor_name", 0.5)
    if "invoice_date" in f:
        f["date"] = f.pop("invoice_date")
        c["date"] = c.pop("invoice_date", 0.5)
    m = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)(?:[:.]([0-5]\d))?(?: ?([AaPp][Mm]))?\b", text)
    if m:
        f["time"], c["time"] = m.group(0), 0.7
    _set(f, c, "receipt_number", find_after(text, ["receipt", "transaction", "trans", "txn", "order", "bill", "ref"], "id", dayfirst, next_line=False))
    low = text.lower()
    for method in ("visa", "mastercard", "amex", "american express", "rupay", "discover", "upi", "paypal",
                   "apple pay", "google pay", "gpay", "phonepe", "paytm", "debit card", "credit card", "cash"):
        if method in low:
            nice = {"upi": "UPI", "amex": "Amex", "gpay": "Google Pay", "phonepe": "PhonePe", "rupay": "RuPay"}
            f["payment_method"], c["payment_method"] = nice.get(method, method.title()), 0.7
            break
    _set(f, c, "tip", find_after(text, ["tip", "gratuity"], "amount", dayfirst, next_line=False))
    _set(f, c, "change", find_after(text, ["change"], "amount", dayfirst, next_line=False))
    return f, c, items


TXN_LINE = re.compile(r"^\s*(?P<date>\d{1,4}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}[\s-][A-Za-z]{3}[\s-]?\d{0,4})\s+(?P<rest>.+)$")


def extract_bank_statement(text: str, tables: list, dayfirst: bool | None = None) -> tuple[dict, dict, list]:
    f: dict[str, Any] = {}
    c: dict[str, float] = {}
    _set(f, c, "bank_name", (lambda v: (v, 0.5) if v else None)(_party_name(text)))
    _set(f, c, "account_holder", find_after(text, ["account holder", "account name", "customer name", "name"], "string", dayfirst, next_line=False))
    _set(f, c, "account_number", find_after(text, ["account number", "account no", "a/c no", "acct no", "account"], "id", dayfirst, next_line=False))
    _set(f, c, "ifsc", find_after(text, ["ifsc"], "id", dayfirst, next_line=False))
    _set(f, c, "iban", find_after(text, ["iban"], "string", dayfirst, next_line=False))
    period = re.search(r"(?i)(?:statement period|period|from)\s*[:\-]?\s*(.+?)\s+(?:to|-|–)\s+(.+)$", text, re.M)
    if period:
        f["period_start"], f["period_end"] = parse_date(period.group(1), dayfirst), parse_date(period.group(2), dayfirst)
    _set(f, c, "opening_balance", find_after(text, ["opening balance", "balance brought forward", "previous balance",
                                                    "beginning balance"], "amount", dayfirst, next_line=False))
    _set(f, c, "closing_balance", find_after(text, ["closing balance", "ending balance", "balance carried forward",
                                                    "available balance"], "amount", dayfirst, next_line=False))
    _set(f, c, "currency", (lambda v: (v, 0.8) if v else None)(detect_currency(text)))
    txns = []
    for ln in _lines(text):
        m = TXN_LINE.match(ln)
        if not m:
            continue
        d = parse_date(m.group("date"), dayfirst)
        if not d:
            continue
        rest = m.group("rest")
        nums = list(re.finditer(r"\(?-?[$€£₹]?\d[\d,]*\.\d{2}\)?(?:\s?(?:CR|DR|Cr|Dr))?", rest))
        if not nums:
            continue
        desc = rest[: nums[0].start()].strip(" -|")
        vals = [parse_amount(n.group(0)) for n in nums]
        t: dict[str, Any] = {"date": d, "description": re.sub(r"\s{2,}", " ", desc)}
        low = desc.lower()
        if len(vals) >= 3:
            t["debit"], t["credit"], t["balance"] = vals[-3] or None, vals[-2] or None, vals[-1]
        elif len(vals) == 2:
            t["amount"], t["balance"] = vals[0], vals[1]
        else:
            t["amount"] = vals[0]
        tag = nums[0].group(0).upper()
        if "amount" in t:
            if "DR" in tag or re.search(r"\b(withdrawal|debit|purchase|payment to|atm|pos|fee|charge)\b", low):
                t["type"] = "debit"
            elif "CR" in tag or re.search(r"\b(deposit|credit|salary|refund|interest|received|from)\b", low):
                t["type"] = "credit"
        txns.append(t)
    if txns:
        f["transaction_count"] = len(txns)
        f["first_transaction"], f["last_transaction"] = txns[0]["date"], txns[-1]["date"]
    return f, c, txns


def extract_payslip(text: str, tables: list, dayfirst: bool | None = None) -> tuple[dict, dict, list]:
    f: dict[str, Any] = {}
    c: dict[str, float] = {}
    _set(f, c, "employer", (lambda v: (v, 0.5) if v else None)(_party_name(text)))
    _set(f, c, "employee_name", find_after(text, ["employee name", "name"], "string", dayfirst, next_line=False))
    _set(f, c, "employee_id", find_after(text, ["employee id", "emp id", "employee no", "emp code", "staff id"], "id", dayfirst, next_line=False))
    _set(f, c, "pay_period", find_after(text, ["pay period", "salary month", "month", "period"], "string", dayfirst, next_line=False))
    _set(f, c, "pay_date", find_after(text, ["pay date", "payment date", "date"], "date", dayfirst, next_line=False))
    _set(f, c, "gross_pay", find_after(text, ["gross pay", "gross earnings", "total earnings", "gross salary", "gross"], "amount", dayfirst, next_line=False))
    _set(f, c, "total_deductions", find_after(text, ["total deductions", "deductions"], "amount", dayfirst, next_line=False))
    _set(f, c, "net_pay", find_after(text, ["net pay", "net salary", "take home", "net amount", "net payable"], "amount", dayfirst, next_line=False))
    _set(f, c, "currency", (lambda v: (v, 0.8) if v else None)(detect_currency(text)))
    return f, c, []


SECTION_RE = re.compile(r"(?im)^\s*(summary|profile|objective|experience|work experience|employment|education|"
                        r"skills|technical skills|projects|certifications|languages|awards|publications|interests)\s*:?\s*$")


def extract_resume(text: str, tables: list, dayfirst: bool | None = None) -> tuple[dict, dict, list]:
    f: dict[str, Any] = {}
    c: dict[str, float] = {}
    for line in _lines(text)[:5]:
        s = line.strip()
        if 2 <= len(s.split()) <= 4 and re.fullmatch(r"[A-Za-z .'\-]+", s):
            f["name"], c["name"] = s.title() if s.isupper() else s, 0.6
            break
    em = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    if em:
        f["email"], c["email"] = em.group(0), 0.95
    ph = re.search(r"\+?\d[\d\s().-]{8,}\d", text)
    if ph:
        f["phone"], c["phone"] = ph.group(0).strip(), 0.7
    for site in ("linkedin", "github"):
        m = re.search(rf"(?i)(?:https?://)?(?:www\.)?{site}\.com/[\w\-/]+", text)
        if m:
            f[site], c[site] = m.group(0), 0.9
    sections = {}
    matches = list(SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[snake(m.group(1))] = text[m.end():end].strip()[:3000]
    skills = sections.get("skills") or sections.get("technical_skills")
    if skills:
        f["skills"] = [s.strip() for s in re.split(r"[,•·|\n;]", skills) if 1 < len(s.strip()) < 40][:60]
        c["skills"] = 0.7
    years = [int(y) for y in re.findall(r"\b(19[6-9]\d|20[0-4]\d)\b", text)]
    if years:
        f["career_span_years"] = max(years) - min(years)
    f["sections"] = list(sections)
    return f, c, [{"section": k, "text": v} for k, v in sections.items()]


def extract_id_document(text: str, tables: list, dayfirst: bool | None = None) -> tuple[dict, dict, list]:
    f: dict[str, Any] = {}
    c: dict[str, float] = {}
    _set(f, c, "full_name", find_after(text, ["name", "full name", "surname"], "string", dayfirst))
    _set(f, c, "document_number", find_after(text, ["passport no", "document no", "licence no", "license no",
                                                    "id no", "card no", "number"], "id", dayfirst))
    _set(f, c, "date_of_birth", find_after(text, ["date of birth", "dob", "birth date"], "date", dayfirst))
    _set(f, c, "expiry_date", find_after(text, ["date of expiry", "expiry date", "expiry", "expires", "valid until"], "date", dayfirst))
    _set(f, c, "issue_date", find_after(text, ["date of issue", "issue date", "issued"], "date", dayfirst))
    _set(f, c, "nationality", find_after(text, ["nationality"], "string", dayfirst))
    _set(f, c, "sex", find_after(text, ["sex", "gender"], "string", dayfirst))
    mrz = re.findall(r"^[A-Z0-9<]{30,44}$", text.replace(" ", ""), re.M)
    if mrz:
        f["mrz"] = mrz[:3]
    return f, c, []


EXTRACTORS = {
    "invoice": extract_invoice,
    "receipt": extract_receipt,
    "bank_statement": extract_bank_statement,
    "purchase_order": lambda t, tb, d=None: extract_invoice(t, tb, d, po=True),
    "payslip": extract_payslip,
    "resume": extract_resume,
    "id_document": extract_id_document,
}


def extract_fields(text: str, doc_type: str, tables: list, dayfirst: bool | None = None) -> tuple[dict, dict, list]:
    fn = EXTRACTORS.get(doc_type)
    if not fn:
        return {}, {}, []
    return fn(text, tables, dayfirst)
