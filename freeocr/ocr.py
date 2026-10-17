"""Turn any input file (PDF, image, text) into pages of text.

Strategy:
  1. Digital PDFs: read the embedded text layer (fast, exact).
  2. Scanned PDFs / pages with no text layer: render the page and OCR it.
  3. Images: OCR directly.

OCR engines (picked automatically, or forced with ``engine=``):
  * ``tesseract``  - needs the ``tesseract`` binary (100+ languages)
  * ``rapidocr``   - pure pip install (``pip install rapidocr-onnxruntime``)
"""

from __future__ import annotations

import io
import os
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from PIL import Image, ImageOps

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
TEXT_EXTS = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".eml"}

MIN_TEXT_CHARS = 25  # below this a PDF page is treated as scanned
RENDER_DPI = int(os.getenv("FREEOCR_DPI", "300"))
MAX_PAGES = int(os.getenv("FREEOCR_MAX_PAGES", "200"))


class OCRError(RuntimeError):
    pass


@dataclass
class Page:
    number: int
    text: str
    method: str  # "text-layer" | "ocr:<engine>" | "plain-text"
    confidence: float | None = None
    width: int | None = None
    height: int | None = None
    tables: list[list[list[str]]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.number,
            "method": self.method,
            "confidence": self.confidence,
            "width": self.width,
            "height": self.height,
            "chars": len(self.text),
            "text": self.text,
            "tables": self.tables,
        }


# --------------------------------------------------------------------------- engines


def tesseract_available() -> bool:
    return shutil.which(os.getenv("FREEOCR_TESSERACT_CMD", "tesseract")) is not None


def rapidocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401

        return True
    except Exception:
        return False


def available_engines() -> list[str]:
    engines = []
    if tesseract_available():
        engines.append("tesseract")
    if rapidocr_available():
        engines.append("rapidocr")
    return engines


def pick_engine(engine: str = "auto") -> str:
    engines = available_engines()
    if engine in (None, "", "auto"):
        preferred = os.getenv("FREEOCR_ENGINE", "auto")
        if preferred != "auto" and preferred in engines:
            return preferred
        if engines:
            return engines[0]
        raise OCRError(
            "No OCR engine installed. Install Tesseract (apt install tesseract-ocr / "
            "brew install tesseract) or run: pip install rapidocr-onnxruntime"
        )
    if engine not in engines:
        raise OCRError(f"OCR engine '{engine}' is not installed. Available: {engines or 'none'}")
    return engine


def _prepare(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    # upscale tiny images (phone thumbnails, cropped receipts) for better accuracy
    if max(img.size) < 1200:
        scale = 1200 / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return img


def _ocr_tesseract(img: Image.Image, lang: str) -> tuple[str, float | None]:
    import pytesseract

    cmd = os.getenv("FREEOCR_TESSERACT_CMD")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    gray = ImageOps.grayscale(img)
    # psm 6 keeps table rows on one line; psm 3 (auto) handles free layouts.
    text = pytesseract.image_to_string(gray, lang=lang, config="--oem 1 --psm 4")
    conf = None
    try:
        data = pytesseract.image_to_data(gray, lang=lang, config="--oem 1 --psm 4",
                                         output_type=pytesseract.Output.DICT)
        vals = [float(c) for c in data.get("conf", []) if str(c) not in ("-1", "-1.0")]
        conf = round(sum(vals) / len(vals) / 100, 3) if vals else None
    except Exception:
        pass
    return text, conf


@lru_cache(maxsize=1)
def _rapid():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _ocr_rapid(img: Image.Image) -> tuple[str, float | None]:
    import numpy as np

    result, _ = _rapid()(np.array(img.convert("RGB")))
    if not result:
        return "", None
    # group boxes into lines by vertical centre
    items = []
    for box, txt, score in result:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append(((min(ys) + max(ys)) / 2, min(xs), max(ys) - min(ys), txt, float(score)))
    items.sort(key=lambda t: (t[0], t[1]))
    lines: list[list[tuple]] = []
    for it in items:
        if lines and abs(lines[-1][0][0] - it[0]) < max(8, it[2] * 0.5):
            lines[-1].append(it)
        else:
            lines.append([it])
    text = "\n".join("   ".join(t[3] for t in sorted(line, key=lambda t: t[1])) for line in lines)
    conf = round(sum(t[4] for t in items) / len(items), 3)
    return text, conf


def ocr_image(img: Image.Image, engine: str = "auto", lang: str = "eng") -> tuple[str, float | None, str]:
    engine = pick_engine(engine)
    img = _prepare(img)
    if engine == "tesseract":
        text, conf = _ocr_tesseract(img, lang)
    else:
        text, conf = _ocr_rapid(img)
    return text.strip(), conf, engine


# --------------------------------------------------------------------------- loaders


def sniff_kind(data: bytes, filename: str = "") -> str:
    name = filename.lower()
    ext = os.path.splitext(name)[1]
    if data[:5] == b"%PDF-" or ext == ".pdf":
        return "pdf"
    if ext in IMAGE_EXTS or data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" \
            or data[:4] in (b"II*\x00", b"MM\x00*") or data[:4] == b"RIFF" or data[:2] == b"BM":
        return "image"
    if ext in TEXT_EXTS:
        return "text"
    try:
        data[:4096].decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "image"


def _pdf_pages(data: bytes, engine: str, lang: str, force_ocr: bool, pages: list[int] | None) -> list[Page]:
    import pdfplumber

    out: list[Page] = []
    need_ocr: list[int] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        total = len(pdf.pages)
        wanted = [p for p in (pages or range(1, total + 1)) if 1 <= p <= total][:MAX_PAGES]
        for n in wanted:
            pg = pdf.pages[n - 1]
            text = "" if force_ocr else (pg.extract_text(layout=False) or "")
            tables = []
            try:
                for t in pg.extract_tables():
                    rows = [[(c or "").strip() for c in row] for row in t if row and any(row)]
                    if len(rows) >= 2:
                        tables.append(rows)
            except Exception:
                pass
            page = Page(n, text.strip(), "text-layer", 1.0 if text.strip() else None,
                        int(pg.width), int(pg.height), tables)
            out.append(page)
            if force_ocr or len(text.strip()) < MIN_TEXT_CHARS:
                need_ocr.append(len(out) - 1)

    if need_ocr:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(data)
        try:
            for idx in need_ocr:
                page = out[idx]
                bitmap = doc[page.number - 1].render(scale=RENDER_DPI / 72)
                img = bitmap.to_pil()
                text, conf, used = ocr_image(img, engine, lang)
                page.text, page.confidence, page.method = text, conf, f"ocr:{used}"
                page.width, page.height = img.size
        finally:
            doc.close()
    return out


def _image_pages(data: bytes, engine: str, lang: str) -> list[Page]:
    img = Image.open(io.BytesIO(data))
    out = []
    n_frames = getattr(img, "n_frames", 1)
    for i in range(min(n_frames, MAX_PAGES)):
        if n_frames > 1:
            img.seek(i)
        frame = img.copy()
        text, conf, used = ocr_image(frame, engine, lang)
        out.append(Page(i + 1, text, f"ocr:{used}", conf, frame.width, frame.height))
    return out


def load_pages(data: bytes, filename: str = "", engine: str = "auto", lang: str = "eng",
               force_ocr: bool = False, pages: list[int] | None = None) -> list[Page]:
    if not data:
        raise OCRError("Empty file")
    kind = sniff_kind(data, filename)
    if kind == "pdf":
        return _pdf_pages(data, engine, lang, force_ocr, pages)
    if kind == "image":
        try:
            return _image_pages(data, engine, lang)
        except OCRError:
            raise
        except Exception as e:  # not a readable image
            raise OCRError(f"Unsupported or corrupt file: {e}") from e
    text = data.decode("utf-8", errors="replace")
    return [Page(1, text.strip(), "plain-text", 1.0)]
