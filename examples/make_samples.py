"""Generate sample documents (fictional companies) to try the extractor.

    python examples/make_samples.py            # writes examples/samples/*
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent / "samples"


def _font(size: int):
    for name in ("DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                 "Menlo.ttc", "consola.ttf", "Courier New.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


INVOICE_LINES = [
    ("Northwind Supplies Ltd", 16, True),
    ("221 Harbour Road, Bristol BS1 4XY", 10, False),
    ("Email: billing@northwind.example  Phone: +44 117 496 0123", 10, False),
    ("VAT No: GB123456789", 10, False),
    ("", 10, False),
    ("INVOICE", 20, True),
    ("Invoice No: INV-2026-0042", 11, False),
    ("Invoice Date: 2026-03-15", 11, False),
    ("Due Date: 2026-04-14", 11, False),
    ("PO Number: PO-7781", 11, False),
    ("", 10, False),
    ("Bill To:", 11, True),
    ("Blue Harbor Cafe", 11, False),
    ("14 Mill Street, Bath BA1 1AA", 11, False),
    ("", 10, False),
]
ITEMS = [("Espresso beans 1kg", 4, 18.50), ("Oat milk carton", 12, 2.25),
         ("Paper cups (box of 500)", 2, 41.00), ("Barista training (hours)", 3, 35.00)]


def invoice_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    _, h = A4
    y = h - 60
    for text, size, bold in INVOICE_LINES:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(50, y, text)
        y -= size + 8
    c.setFont("Helvetica-Bold", 11)
    for x, t in ((50, "Description"), (300, "Qty"), (370, "Unit Price"), (470, "Amount")):
        c.drawString(x, y, t)
    y -= 18
    c.setFont("Helvetica", 11)
    sub = 0.0
    for d, q, p in ITEMS:
        amt = q * p
        sub += amt
        c.drawString(50, y, d)
        c.drawString(300, y, str(q))
        c.drawString(370, y, f"{p:.2f}")
        c.drawString(470, y, f"{amt:.2f}")
        y -= 16
    y -= 10
    tax = round(sub * 0.2, 2)
    for label, val in (("Subtotal:", sub), ("VAT 20%:", tax), ("Total Due:", sub + tax)):
        c.setFont("Helvetica-Bold" if "Total" in label else "Helvetica", 11)
        c.drawString(370, y, label)
        c.drawString(470, y, f"GBP {val:,.2f}")
        y -= 16
    c.setFont("Helvetica", 10)
    c.drawString(50, y - 20, "Payment Terms: Net 30")
    c.drawString(50, y - 36, "IBAN: GB82 WEST 1234 5698 7654 32")
    c.save()


def scanned_png(pdf: Path, path: Path) -> None:
    """Rasterise the PDF and make it look like a slightly blurry scan (no text layer)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    img = doc[0].render(scale=200 / 72).to_pil().convert("L")
    doc.close()
    img = img.rotate(0.6, expand=False, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))
    img.save(path)


RECEIPT = """GREEN LEAF MARKET
42 Orchard Lane, Pune 411001
GSTIN: 27ABCDE1234F1Z5
Tel: +91 20 4567 8901

RECEIPT
Date: 12/09/2026   Time: 18:42
Receipt No: R-55120   Cashier: Asha

Basmati Rice 5kg        1     549.00
Organic Milk 1L         2      68.00
Bananas (dozen)         1      60.00
Masala Tea 250g         1     145.00

Subtotal                      890.00
GST 5%                         44.50
Total                     Rs. 934.50
Paid by UPI
Thank you for shopping!
"""

STATEMENT = """Sunrise Community Bank
Account Statement
Account Holder: Jordan Lee
Account Number: 0012345678
Statement Period: 01/09/2026 to 30/09/2026
Opening Balance: 2,450.00

Date        Description                      Debit      Credit     Balance
02/09/2026  Salary ACME Payroll                0.00    3,200.00    5,650.00
05/09/2026  Rent payment to Oak Estates    1,400.00        0.00    4,250.00
11/09/2026  Grocery store POS                 86.40        0.00    4,163.60
19/09/2026  Interest credit                    0.00        4.12    4,167.72
27/09/2026  ATM withdrawal                   200.00        0.00    3,967.72

Closing Balance: 3,967.72
"""


def text_png(text: str, path: Path, size: int = 26) -> None:
    font = _font(size)
    lines = text.splitlines()
    w = max(int(font.getlength(ln)) for ln in lines if ln) + 80
    h = (size + 12) * len(lines) + 80
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        d.text((40, 40 + i * (size + 12)), ln, fill=0, font=font)
    img.save(path)


def text_pdf(text: str, path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    _, h = A4
    c.setFont("Courier", 9)
    for i, ln in enumerate(text.splitlines()):
        c.drawString(40, h - 50 - i * 13, ln)
    c.save()


def make_all(out: Path = OUT) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "invoice_pdf": out / "invoice.pdf",
        "invoice_scan": out / "invoice_scan.png",
        "receipt": out / "receipt.png",
        "statement": out / "bank_statement.pdf",
    }
    invoice_pdf(files["invoice_pdf"])
    scanned_png(files["invoice_pdf"], files["invoice_scan"])
    text_png(RECEIPT, files["receipt"])
    text_pdf(STATEMENT, files["statement"])
    return files


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    for k, p in make_all(target).items():
        print(f"{k:14s} {p}")
