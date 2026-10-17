"""One call from file bytes to structured data."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from . import llm
from .extract import DOC_TYPES, classify, extract_fields
from .ocr import load_pages
from .parsers import find_entities, infer_dayfirst, key_values, redact as redact_text
from .templates import apply_template, auto_match, load_templates, parse_template


def ocr_document(data: bytes, filename: str = "", engine: str = "auto", lang: str = "eng",
                 force_ocr: bool = False, pages: list[int] | None = None) -> dict[str, Any]:
    t0 = time.perf_counter()
    pgs = load_pages(data, filename, engine, lang, force_ocr, pages)
    confs = [p.confidence for p in pgs if p.confidence is not None]
    return {
        "file": filename or None,
        "page_count": len(pgs),
        "ocr_confidence": round(sum(confs) / len(confs), 3) if confs else None,
        "methods": sorted({p.method for p in pgs}),
        "text": "\n\n".join(p.text for p in pgs).strip(),
        "pages": [p.to_dict() for p in pgs],
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }


def extract_document(
    data: bytes,
    filename: str = "",
    doc_type: str = "auto",
    engine: str = "auto",
    lang: str = "eng",
    template: str | dict | None = None,
    fields: list[str] | dict[str, str] | None = None,
    use_llm: bool = False,
    redact: bool = False,
    include_text: bool = True,
    include_pages: bool = False,
    force_ocr: bool = False,
    pages: list[int] | None = None,
    dayfirst: bool | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    ocr = ocr_document(data, filename, engine, lang, force_ocr, pages)
    text = ocr["text"]
    tables = [t for p in ocr["pages"] for t in p["tables"]]
    if dayfirst is None:
        dayfirst = infer_dayfirst(text)

    if doc_type in (None, "", "auto"):
        doc_type, type_conf, _ = classify(text)
    elif doc_type not in DOC_TYPES:
        raise ValueError(f"unknown doc_type '{doc_type}', use one of {DOC_TYPES + ['auto']}")
    else:
        type_conf = 1.0

    flds, conf, items = extract_fields(text, doc_type, tables, dayfirst)
    warnings: list[str] = []

    # custom template (explicit, by name, or auto-matched by keywords)
    tpl = None
    tpls = load_templates()
    if isinstance(template, dict) or (isinstance(template, str) and template.strip().startswith(("{", "name:", "fields:"))):
        tpl = parse_template(template)
    elif isinstance(template, str) and template:
        if template not in tpls:
            raise ValueError(f"template '{template}' not found. Available: {sorted(tpls)}")
        tpl = tpls[template]
    elif tpls:
        tpl = auto_match(text, tpls)
    if tpl:
        tv, tc, missing = apply_template(text, tpl, dayfirst)
        flds.update(tv)
        conf.update(tc)
        if missing:
            warnings.append(f"required fields not found: {', '.join(missing)}")

    # optional language-model assist for requested fields still empty
    if fields:
        wanted = fields if isinstance(fields, dict) else {f: "" for f in fields}
        empty = {k: v for k, v in wanted.items() if flds.get(k) in (None, "", [])}
        if empty and use_llm:
            try:
                got = llm.ask_fields(text, empty, doc_type)
                for k, v in got.items():
                    if v not in (None, ""):
                        flds[k], conf[k] = v, 0.75
            except Exception as e:
                warnings.append(f"llm assist failed: {e}")
        for k in wanted:
            flds.setdefault(k, None)
    elif use_llm:
        warnings.append("use_llm needs a 'fields' list")

    if doc_type in ("invoice", "receipt", "purchase_order") and not items:
        warnings.append("no line items detected")
    if ocr["ocr_confidence"] is not None and ocr["ocr_confidence"] < 0.6:
        warnings.append("low OCR confidence - try a sharper scan or a higher DPI")

    out_text = redact_text(text) if redact else text
    result: dict[str, Any] = {
        "file": filename or None,
        "sha256": hashlib.sha256(data).hexdigest(),
        "doc_type": doc_type,
        "doc_type_confidence": type_conf,
        "template": tpl["name"] if tpl else None,
        "page_count": ocr["page_count"],
        "ocr_confidence": ocr["ocr_confidence"],
        "methods": ocr["methods"],
        "fields": flds,
        "field_confidence": conf,
        "line_items": items,
        "key_values": key_values(out_text),
        "entities": find_entities(text, dayfirst) if not redact else {},
        "tables": tables,
        "warnings": warnings,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }
    if redact:
        result["redacted"] = True
        for k in ("email", "phone", "iban", "account_number", "vendor_tax_id"):
            if result["fields"].get(k):
                result["fields"][k] = "[REDACTED]"
    if include_text:
        result["text"] = out_text
    if include_pages:
        result["pages"] = ocr["pages"]
    return result
