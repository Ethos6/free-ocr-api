"""Searchable PDF output: scans in, PDF with an invisible selectable text layer out.

Pages that already carry real text (digital PDFs) are copied through untouched;
scanned pages and images are OCR'd with Tesseract, which renders the page image
with an invisible text layer on top, so the result looks identical but can be
searched, selected and copied. Requires the Tesseract engine.
"""

from __future__ import annotations

import io
import os

from PIL import Image

from .ocr import (
    MAX_PAGES,
    MIN_TEXT_CHARS,
    RENDER_DPI,
    OCRError,
    sniff_kind,
    tesseract_available,
)


def _ocr_page_pdf(img: Image.Image, lang: str, dpi: int) -> bytes:
    import pytesseract

    cmd = os.getenv("FREEOCR_TESSERACT_CMD")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return pytesseract.image_to_pdf_or_hocr(img, lang=lang, extension="pdf", config=f"--dpi {dpi}")


def make_searchable_pdf(data: bytes, filename: str = "", lang: str = "eng",
                        force_ocr: bool = False) -> tuple[bytes, dict]:
    """Return (pdf_bytes, stats). stats: pages, ocr_pages, kept_pages."""
    import pypdfium2 as pdfium

    if not data:
        raise OCRError("Empty file")
    if not tesseract_available():
        raise OCRError("Searchable PDF output needs Tesseract (apt install tesseract-ocr / brew install tesseract)")

    kind = sniff_kind(data, filename)
    out = pdfium.PdfDocument.new()
    ocr_pages = kept = 0

    def append_pdf(pdf_bytes: bytes, index: int = 0) -> None:
        src = pdfium.PdfDocument(pdf_bytes)
        try:
            out.import_pages(src, [index])
        finally:
            src.close()

    try:
        if kind == "pdf":
            src = pdfium.PdfDocument(data)
            try:
                total = min(len(src), MAX_PAGES)
                for i in range(total):
                    page = src[i]
                    chars = page.get_textpage().count_chars()
                    if chars >= MIN_TEXT_CHARS and not force_ocr:
                        out.import_pages(src, [i])
                        kept += 1
                        continue
                    img = page.render(scale=RENDER_DPI / 72).to_pil()
                    append_pdf(_ocr_page_pdf(img, lang, RENDER_DPI))
                    ocr_pages += 1
            finally:
                src.close()
        elif kind == "image":
            img = Image.open(io.BytesIO(data))
            frames = getattr(img, "n_frames", 1)
            for i in range(min(frames, MAX_PAGES)):
                if frames > 1:
                    img.seek(i)
                frame = img.copy()
                dpi = int((frame.info.get("dpi") or (300, 300))[0]) or 300
                append_pdf(_ocr_page_pdf(frame, lang, max(dpi, 72)))
                ocr_pages += 1
        else:
            raise OCRError("Searchable PDF output needs a PDF or an image")

        buf = io.BytesIO()
        out.save(buf)
    finally:
        out.close()
    return buf.getvalue(), {"pages": ocr_pages + kept, "ocr_pages": ocr_pages, "kept_pages": kept}
