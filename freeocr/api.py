"""REST API + web UI.   Run:  freeocr serve   (or: uvicorn freeocr.api:app)"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import __version__, llm
from .export import FORMATS, MEDIA_TYPES, export
from .extract import DOC_TYPES
from .ocr import OCRError, available_engines
from .parsers import redact as redact_text
from .pipeline import extract_document, ocr_document
from .templates import TemplateError, load_templates, parse_template

MAX_MB = float(os.getenv("FREEOCR_MAX_MB", "25"))
MAX_FILES = int(os.getenv("FREEOCR_MAX_FILES", "100"))
API_KEY = os.getenv("FREEOCR_API_KEY", "")
WEB = Path(__file__).parent / "web" / "index.html"

app = FastAPI(
    title="free-ocr-api",
    version=__version__,
    description="Free, self-hosted OCR and document data extraction. Turn PDFs, scans, invoices, "
                "receipts, bank statements and forms into JSON, CSV or Excel. No API keys, no cloud.",
)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("FREEOCR_CORS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])


def auth(x_api_key: Optional[str] = Header(default=None), authorization: Optional[str] = Header(default=None)):
    if not API_KEY:
        return
    token = x_api_key or (authorization or "").removeprefix("Bearer ").strip()
    if token != API_KEY:
        raise HTTPException(401, "invalid or missing API key (send X-API-Key header)")


def parse_pages(spec: Optional[str]) -> Optional[list[int]]:
    if not spec:
        return None
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if re.fullmatch(r"\d+-\d+", part):
            a, b = map(int, part.split("-"))
            out += list(range(a, b + 1))
        elif part.isdigit():
            out.append(int(part))
        elif part:
            raise HTTPException(422, f"bad pages value '{spec}' (use e.g. 1-3,5)")
    return out


def parse_bool(v: Optional[str]) -> Optional[bool]:
    if v in (None, "", "auto"):
        return None
    return str(v).lower() in ("1", "true", "yes", "on")


def parse_fields(v: Optional[str]):
    if not v:
        return None
    v = v.strip()
    if v.startswith("{") or v.startswith("["):
        try:
            return json.loads(v)
        except json.JSONDecodeError as e:
            raise HTTPException(422, f"fields is not valid JSON: {e}") from e
    return [f.strip() for f in v.split(",") if f.strip()]


async def read_upload(f: UploadFile) -> bytes:
    data = await f.read()
    if len(data) > MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"{f.filename}: file larger than {MAX_MB:g} MB")
    if not data:
        raise HTTPException(422, f"{f.filename}: empty file")
    return data


def respond(result, fmt: str, name: str = "result"):
    fmt = (fmt or "json").lower()
    if fmt not in FORMATS:
        raise HTTPException(422, f"format must be one of {FORMATS}")
    if fmt == "json":
        return JSONResponse(result)
    body = export(result, fmt)
    return Response(body, media_type=MEDIA_TYPES[fmt],
                    headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'})


def _run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except (OCRError, TemplateError, ValueError) as e:
        raise HTTPException(422, str(e)) from e


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    return WEB.read_text(encoding="utf-8")


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__, "engines": available_engines()}


@app.get("/v1/info")
def info():
    return {
        "version": __version__,
        "engines": available_engines(),
        "doc_types": DOC_TYPES,
        "formats": list(FORMATS),
        "templates": sorted(load_templates()),
        "llm_assist": llm.enabled(),
        "max_upload_mb": MAX_MB,
        "auth_required": bool(API_KEY),
    }


@app.get("/v1/templates", dependencies=[Depends(auth)])
def templates():
    return load_templates()


@app.post("/v1/ocr", dependencies=[Depends(auth)])
async def ocr(file: UploadFile = File(...), engine: str = Form("auto"), lang: str = Form("eng"),
              force_ocr: bool = Form(False), pages: Optional[str] = Form(None), format: str = Form("json")):
    """Plain text from any PDF or image, page by page."""
    data = await read_upload(file)
    res = _run(ocr_document, data, file.filename or "", engine, lang, force_ocr, parse_pages(pages))
    if format == "txt":
        return Response(res["text"], media_type=MEDIA_TYPES["txt"])
    return JSONResponse(res)


@app.post("/v1/extract", dependencies=[Depends(auth)])
async def extract(
    file: UploadFile = File(...),
    doc_type: str = Form("auto"),
    engine: str = Form("auto"),
    lang: str = Form("eng"),
    template: Optional[str] = Form(None),
    fields: Optional[str] = Form(None),
    use_llm: bool = Form(False),
    redact: bool = Form(False),
    include_text: bool = Form(True),
    include_pages: bool = Form(False),
    force_ocr: bool = Form(False),
    pages: Optional[str] = Form(None),
    dayfirst: Optional[str] = Form(None),
    format: str = Form("json"),
):
    """Structured data (fields, line items, tables, entities) from one document."""
    data = await read_upload(file)
    res = _run(extract_document, data, file.filename or "", doc_type, engine, lang, template,
               parse_fields(fields), use_llm, redact, include_text, include_pages, force_ocr,
               parse_pages(pages), parse_bool(dayfirst))
    stem = Path(file.filename or "result").stem
    return respond(res, format, stem)


@app.post("/v1/batch", dependencies=[Depends(auth)])
async def batch(
    files: list[UploadFile] = File(...),
    doc_type: str = Form("auto"),
    engine: str = Form("auto"),
    lang: str = Form("eng"),
    template: Optional[str] = Form(None),
    fields: Optional[str] = Form(None),
    use_llm: bool = Form(False),
    redact: bool = Form(False),
    dayfirst: Optional[str] = Form(None),
    format: str = Form("xlsx"),
):
    """Many documents -> one spreadsheet (one row per document + a line-items sheet)."""
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"max {MAX_FILES} files per batch")
    results = []
    for f in files:
        try:
            data = await read_upload(f)
            results.append(extract_document(data, f.filename or "", doc_type, engine, lang, template,
                                            parse_fields(fields), use_llm, redact, False, False,
                                            False, None, parse_bool(dayfirst)))
        except HTTPException as e:
            results.append({"file": f.filename, "error": e.detail})
        except Exception as e:
            results.append({"file": f.filename, "error": str(e)})
    if (format or "").lower() == "json":
        return JSONResponse({"count": len(results), "results": results})
    return respond(results, format, "batch")


@app.post("/v1/export/{fmt}", dependencies=[Depends(auth)])
def export_results(fmt: str, results: list[dict]):
    """Convert (possibly hand-corrected) extraction results to xlsx / csv / md / txt / json."""
    if fmt not in FORMATS:
        raise HTTPException(422, f"format must be one of {FORMATS}")
    return Response(export(results, fmt), media_type=MEDIA_TYPES[fmt],
                    headers={"Content-Disposition": f'attachment; filename="extracted.{fmt}"'})


@app.post("/v1/redact", dependencies=[Depends(auth)])
async def redact_endpoint(file: UploadFile = File(...), engine: str = Form("auto"), lang: str = Form("eng")):
    """OCR a document and mask emails, phones, card numbers, IBANs and tax IDs."""
    data = await read_upload(file)
    res = _run(ocr_document, data, file.filename or "", engine, lang)
    return {"file": file.filename, "text": redact_text(res["text"])}


@app.post("/v1/templates/test", dependencies=[Depends(auth)])
async def template_test(file: UploadFile = File(...), template: str = Form(...), lang: str = Form("eng")):
    """Try a template (YAML or JSON in the 'template' field) against a document before saving it."""
    tpl = _run(parse_template, template)
    data = await read_upload(file)
    res = _run(extract_document, data, file.filename or "", "generic", "auto", lang, tpl, include_text=False)
    return {"template": tpl["name"], "fields": res["fields"], "field_confidence": res["field_confidence"],
            "warnings": res["warnings"]}
