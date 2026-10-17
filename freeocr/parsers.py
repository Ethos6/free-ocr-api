"""Low-level parsers: amounts, dates, entities, key/value pairs, redaction."""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Any

# --------------------------------------------------------------------------- amounts

CURRENCY_SYMBOLS = {
    "$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY", "₩": "KRW", "₽": "RUB",
    "₺": "TRY", "₫": "VND", "₱": "PHP", "฿": "THB", "₦": "NGN", "R$": "BRL", "A$": "AUD",
    "C$": "CAD", "S$": "SGD", "HK$": "HKD", "Rs": "INR", "Rs.": "INR", "₨": "INR",
}
CURRENCY_CODES = {
    "USD", "EUR", "GBP", "INR", "JPY", "CNY", "CAD", "AUD", "CHF", "SGD", "HKD", "AED", "SAR",
    "BRL", "MXN", "ZAR", "SEK", "NOK", "DKK", "PLN", "NZD", "KRW", "TRY", "IDR", "MYR", "THB",
    "PHP", "VND", "NGN", "KES", "EGP", "PKR", "BDT", "LKR", "NPR", "RUB", "CZK", "HUF", "ILS",
}

_NUM = r"\(?-?(?:[$€£₹¥₩₽₺₫₱฿₦]|Rs\.?\s?)?\s?\d(?:[\d,.'’]*\d)?\)?"
NUM_RE = re.compile(_NUM)
MONEY_RE = re.compile(
    r"(?<![\w.])(?:(?P<pre>[A-Z]{3})\s?)?(?P<num>\(?-?(?:[$€£₹¥₩₽₺₫₱฿₦]|Rs\.?\s?)?\s?"
    r"\d{1,3}(?:[,.'’ ]\d{3})*(?:[.,]\d{1,2})?\)?|\(?-?(?:[$€£₹¥₩₽₺₫₱฿₦]|Rs\.?\s?)?\d+(?:[.,]\d{1,2})?\)?)"
    r"(?:\s?(?P<post>[A-Z]{3}))?(?![\w])"
)


def parse_amount(raw: str | None) -> float | None:
    """'$1,234.56' -> 1234.56, '1.234,56 €' -> 1234.56, '₹1,23,456' -> 123456, '(12.00)' -> -12."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    neg = (s.startswith("(") and s.endswith(")")) or s.startswith("-") or s.endswith("-")
    s = re.sub(r"[^\d,.'’]", "", s).replace("'", "").replace("’", "")
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif s.count(".") > 1:
        parts = s.split(".")
        if len(parts[-1]) in (1, 2):
            s = "".join(parts[:-1]) + "." + parts[-1]
        else:
            s = s.replace(".", "")
    elif "." in s and len(s.split(".")[1]) == 3 and len(s.split(".")[0]) <= 3 \
            and os.getenv("FREEOCR_DOT_THOUSANDS", "0") == "1":
        s = s.replace(".", "")
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def amounts_in(line: str) -> list[float]:
    out = []
    for m in MONEY_RE.finditer(line):
        num = m.group("num")
        if not re.search(r"\d", num):
            continue
        v = parse_amount(num)
        if v is not None:
            out.append(v)
    return out


def money_in(line: str) -> list[float]:
    """Amounts that look like money (have decimals, a currency marker, or thousands separators)."""
    out = []
    for m in MONEY_RE.finditer(line):
        num = m.group("num")
        marked = bool(re.search(r"[$€£₹¥₩₽₺₫₱฿₦]|Rs", num)) or bool(m.group("pre") in CURRENCY_CODES) \
            or bool(m.group("post") in CURRENCY_CODES)
        if marked or re.search(r"[.,]\d{2}\)?$", num) or re.search(r"\d[,]\d{3}", num):
            v = parse_amount(num)
            if v is not None:
                out.append(v)
    return out


def detect_currency(text: str) -> str | None:
    counts: dict[str, int] = {}
    for code in re.findall(r"\b([A-Z]{3})\b", text):
        if code in CURRENCY_CODES:
            counts[code] = counts.get(code, 0) + 3
    for sym in sorted(CURRENCY_SYMBOLS, key=len, reverse=True):
        if sym in ("Rs", "Rs."):
            n = len(re.findall(r"\bRs\.?\s?\d", text))
        else:
            n = text.count(sym)
        if n:
            code = CURRENCY_SYMBOLS[sym]
            counts[code] = counts.get(code, 0) + n
            if len(sym) > 1:  # A$ etc. also counted as $
                counts["USD"] = counts.get("USD", 0) - n
    counts = {k: v for k, v in counts.items() if v > 0}
    return max(counts, key=counts.get) if counts else None


# --------------------------------------------------------------------------- dates

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MONTHS.update({"sept": 9, "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
               "august": 8, "september": 9, "october": 10, "november": 11, "december": 12})
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

DATE_PATTERNS = [
    ("ymd", re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")),
    ("num", re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b")),
    ("dmy_txt", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-/.]+({_MON})\.?[\s\-/.,]+(\d{{4}}|\d{{2}})\b", re.I)),
    ("mdy_txt", re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)),
]


def _year(y: str) -> int:
    y_i = int(y)
    return y_i + (2000 if y_i < 70 else 1900) if len(y) == 2 else y_i


def _mk(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def dayfirst_default() -> bool:
    return os.getenv("FREEOCR_DAYFIRST", "auto").lower() in ("1", "true", "yes")


def infer_dayfirst(text: str) -> bool:
    """Guess the document's date order from unambiguous dates and the currency used."""
    env = os.getenv("FREEOCR_DAYFIRST", "auto").lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    day_first = month_first = 0
    for a, b in re.findall(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](?:\d{4}|\d{2})\b", text):
        a_i, b_i = int(a), int(b)
        if a_i > 12 >= b_i:
            day_first += 1
        elif b_i > 12 >= a_i:
            month_first += 1
    if day_first != month_first:
        return day_first > month_first
    return detect_currency(text) not in ("USD", "PHP", None)


def find_dates(text: str, dayfirst: bool | None = None) -> list[tuple[int, str, str]]:
    """Return [(position, raw, iso)] for every date found in text."""
    if dayfirst is None:
        dayfirst = dayfirst_default()
    found: list[tuple[int, str, str]] = []
    taken: list[tuple[int, int]] = []
    for kind, rx in DATE_PATTERNS:
        for m in rx.finditer(text):
            if any(a <= m.start() < b for a, b in taken):
                continue
            g = m.groups()
            iso = None
            if kind == "ymd":
                iso = _mk(int(g[0]), int(g[1]), int(g[2]))
            elif kind == "num":
                a, b, y = int(g[0]), int(g[1]), _year(g[2])
                if a > 12:
                    iso = _mk(y, b, a)
                elif b > 12:
                    iso = _mk(y, a, b)
                else:
                    iso = _mk(y, b, a) if dayfirst else _mk(y, a, b)
            elif kind == "dmy_txt":
                iso = _mk(_year(g[2]), MONTHS[g[1].lower()[:3]], int(g[0]))
            elif kind == "mdy_txt":
                iso = _mk(int(g[2]), MONTHS[g[0].lower()[:3]], int(g[1]))
            if iso:
                found.append((m.start(), m.group(0), iso))
                taken.append((m.start(), m.end()))
    found.sort()
    return found


def parse_date(raw: str | None, dayfirst: bool | None = None) -> str | None:
    if not raw:
        return None
    d = find_dates(raw, dayfirst)
    return d[0][2] if d else None


# --------------------------------------------------------------------------- entities

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>\"')]+", re.I)
PHONE_RE = re.compile(r"(?<![\w/.-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{2,5}(?:[\s.-]\d{2,5}){1,4}(?![\w/-])")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b")
SWIFT_RE = re.compile(r"(?i:\b(?:swift|bic)(?:\s*code)?\s*[:#]?\s*)([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b")
GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
PAN_RE = re.compile(r"\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b")
IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
EU_VAT_RE = re.compile(r"\b(?:ATU\d{8}|BE0?\d{9}|DE\d{9}|DK\d{8}|ES[A-Z0-9]\d{7}[A-Z0-9]|FR[A-HJ-NP-Z0-9]{2}\d{9}|GB\d{9}(?:\d{3})?|IE\d{7}[A-W][A-I]?|IT\d{11}|NL\d{9}B\d{2}|PL\d{10}|PT\d{9}|SE\d{12})\b")
EIN_RE = re.compile(r"(?i:\b(?:EIN|TIN|Tax\s*ID)\s*[:#]?\s*)(\d{2}-\d{7})\b")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
UPI_RE = re.compile(r"\b[\w.\-]{2,}@(?:ok\w+|ybl|ibl|axl|upi|paytm|apl|icici|sbi|hdfcbank|axisbank|kotak)\b", re.I)
PERCENT_RE = re.compile(r"\b\d{1,3}(?:[.,]\d+)?\s?%")


def _luhn(num: str) -> bool:
    digits = [int(c) for c in num if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    s = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        s += d
    return s % 10 == 0


def _iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    r = s[4:] + s[:4]
    n = "".join(str(int(c, 36)) for c in r)
    return int(n) % 97 == 1


def _phone_ok(raw: str) -> bool:
    digits = re.sub(r"\D", "", raw)
    if not 8 <= len(digits) <= 15:
        return False
    if re.fullmatch(r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}", raw.strip()):
        return False
    if re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", raw.strip()):
        return False
    if re.fullmatch(r"\d{1,3}(?:[ .]\d{3})+", raw.strip()) and not raw.strip().startswith("+"):
        return False  # thousands-separated amount
    return True


def _uniq(seq):
    seen, out = set(), []
    for x in seq:
        k = x.lower() if isinstance(x, str) else x
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def find_entities(text: str, dayfirst: bool | None = None) -> dict[str, list[Any]]:
    emails = _uniq(EMAIL_RE.findall(text))
    email_spans = " ".join(emails)
    urls = _uniq(u.rstrip(".,;") for u in URL_RE.findall(text) if u not in email_spans)
    no_dates = text
    for _, raw, _ in find_dates(text, dayfirst):
        no_dates = no_dates.replace(raw, " ")
    phones = _uniq(p.strip() for p in PHONE_RE.findall(no_dates) if _phone_ok(p))
    ibans = _uniq(i.replace(" ", "") for i in IBAN_RE.findall(text) if _iban_ok(i))
    cards = _uniq(re.sub(r"\D", "", c) for c in CARD_RE.findall(text) if _luhn(c))
    cards = [c for c in cards if c not in {re.sub(r"\D", "", p) for p in phones}]
    ent = {
        "emails": emails,
        "phones": [p for p in phones if re.sub(r"\D", "", p) not in cards],
        "urls": urls,
        "dates": _uniq(iso for _, _, iso in find_dates(text, dayfirst)),
        "amounts": _uniq(money_in(text))[:200],
        "percentages": _uniq(p.replace(" ", "") for p in PERCENT_RE.findall(text)),
        "ibans": ibans,
        "swift_bic": _uniq(SWIFT_RE.findall(text)),
        "gstin": _uniq(GSTIN_RE.findall(text)),
        "pan": _uniq(p for p in PAN_RE.findall(text) if not any(p in g for g in GSTIN_RE.findall(text))),
        "ifsc": _uniq(IFSC_RE.findall(text)),
        "eu_vat": _uniq(EU_VAT_RE.findall(text)),
        "us_ein": _uniq(EIN_RE.findall(text)),
        "upi_ids": _uniq(UPI_RE.findall(text)),
        "card_numbers_masked": ["**** **** **** " + c[-4:] for c in cards],
    }
    return {k: v for k, v in ent.items() if v}


# --------------------------------------------------------------------------- redaction

REDACTORS = [
    ("EMAIL", EMAIL_RE),
    ("IBAN", IBAN_RE),
    ("GSTIN", GSTIN_RE),
    ("PAN", PAN_RE),
    ("UPI", UPI_RE),
    ("VAT", EU_VAT_RE),
]


def redact(text: str) -> str:
    """Mask personal / financial identifiers (emails, phones, cards, IBANs, tax IDs)."""
    out = text
    for label, rx in REDACTORS:
        out = rx.sub(f"[{label}]", out)
    out = CARD_RE.sub(lambda m: "[CARD]" if _luhn(m.group(0)) else m.group(0), out)
    out = PHONE_RE.sub(lambda m: "[PHONE]" if _phone_ok(m.group(0)) and not find_dates(m.group(0))
                       else m.group(0), out)
    out = re.sub(r"(?i)\b(ssn|social security(?: number)?)\s*[:#]?\s*\d{3}-?\d{2}-?\d{4}", r"\1 [SSN]", out)
    out = re.sub(r"\b\d{4}\s\d{4}\s\d{4}\b", "[AADHAAR]", out)
    return out


# --------------------------------------------------------------------------- key / value

KV_COLON = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /&()#.'_-]{1,40}?)\s*[:：]\s*(.+?)\s*$")
KV_GAP = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /&()#.'_-]{1,40}?)\s{3,}(\S.*?)\s*$")


def key_values(text: str, max_pairs: int = 300) -> list[dict[str, str]]:
    """'Invoice No: 123' / 'Due Date    2026-01-01' -> [{'key': 'Invoice No', 'value': '123'}]."""
    pairs: list[dict[str, str]] = []
    seen = set()
    for line in text.splitlines():
        # a line can hold several 'A: x   B: y' pairs
        chunks = re.split(r"\s{3,}(?=[A-Za-z][A-Za-z0-9 /&()#.'_-]{1,40}[:：])", line)
        for chunk in chunks:
            m = KV_COLON.match(chunk)
            if not m and len(chunks) == 1:
                m = KV_GAP.match(chunk)
                if m and (len(m.group(1).split()) > 4 or not re.search(r"[\d@]", m.group(2))):
                    m = None
            if not m:
                continue
            k, v = m.group(1).strip(" .#"), m.group(2).strip()
            if not v or k.lower() in ("http", "https") or v.startswith("//"):
                continue
            sig = (k.lower(), v)
            if sig not in seen:
                seen.add(sig)
                pairs.append({"key": k, "value": v})
            if len(pairs) >= max_pairs:
                return pairs
    return pairs


def snake(s: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s.strip().lower()).strip("_")
    return s or "field"
