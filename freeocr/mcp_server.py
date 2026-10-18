"""Minimal MCP (Model Context Protocol) server over stdio - no extra dependencies.

Lets any MCP-capable assistant or editor read documents on your machine:

    {"mcpServers": {"freeocr": {"command": "freeocr", "args": ["mcp"]}}}
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from . import __version__
from .extract import DOC_TYPES

PROTOCOL = "2024-11-05"

TOOLS = [
    {
        "name": "extract_document",
        "description": "Extract structured data (fields, line items, tables, entities) from a PDF, scan, "
                       "photo or text file: invoices, receipts, bank statements, payslips, purchase orders, "
                       "resumes, IDs and generic forms.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to a local file"},
                "base64": {"type": "string", "description": "File content as base64 (instead of path)"},
                "filename": {"type": "string", "description": "Filename hint when using base64"},
                "doc_type": {"type": "string", "enum": DOC_TYPES + ["auto"], "default": "auto"},
                "lang": {"type": "string", "default": "eng"},
                "template": {"type": "string", "description": "Template name or inline YAML/JSON"},
                "redact": {"type": "boolean", "default": False},
                "include_text": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "ocr_document",
        "description": "Return the plain text of a PDF or image (OCR when needed).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "base64": {"type": "string"},
                "filename": {"type": "string"},
                "lang": {"type": "string", "default": "eng"},
            },
        },
    },
    {
        "name": "make_searchable_pdf",
        "description": "Turn a scanned PDF or image into a PDF with an invisible, selectable text layer "
                       "and save it next to the input (or to 'output').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to a local PDF or image"},
                "output": {"type": "string", "description": "Output PDF path"},
                "lang": {"type": "string", "default": "eng"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "extract_folder",
        "description": "Extract every supported document in a folder and save one spreadsheet "
                       "(xlsx/csv/json) with a row per document. Returns a summary.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string"},
                "output": {"type": "string", "description": "Output file, e.g. ./invoices.xlsx"},
                "doc_type": {"type": "string", "default": "auto"},
                "recursive": {"type": "boolean", "default": False},
            },
            "required": ["folder", "output"],
        },
    },
]


def _read(args: dict) -> tuple[bytes, str]:
    if args.get("path"):
        p = Path(args["path"]).expanduser()
        return p.read_bytes(), p.name
    if args.get("base64"):
        return base64.b64decode(args["base64"]), args.get("filename", "")
    raise ValueError("give 'path' or 'base64'")


def call_tool(name: str, args: dict) -> dict:
    from .pipeline import extract_document, ocr_document

    if name == "extract_document":
        data, fname = _read(args)
        res = extract_document(data, fname, args.get("doc_type", "auto"), "auto", args.get("lang", "eng"),
                               args.get("template"), redact=bool(args.get("redact")),
                               include_text=bool(args.get("include_text")))
        if len(json.dumps(res, default=str)) > 60000:
            res.pop("key_values", None)
        return res
    if name == "ocr_document":
        data, fname = _read(args)
        res = ocr_document(data, fname, "auto", args.get("lang", "eng"))
        return {"file": fname, "page_count": res["page_count"], "text": res["text"]}
    if name == "make_searchable_pdf":
        from .searchable import make_searchable_pdf

        src = Path(args["path"]).expanduser()
        out = Path(args.get("output") or src.with_name(src.stem + "-searchable.pdf")).expanduser()
        pdf, stats = make_searchable_pdf(src.read_bytes(), src.name, args.get("lang", "eng"))
        out.write_bytes(pdf)
        return {"output": str(out), **stats}
    if name == "extract_folder":
        from .cli import SUPPORTED
        from .export import export

        folder = Path(args["folder"]).expanduser()
        it = folder.rglob("*") if args.get("recursive") else folder.glob("*")
        files = sorted(f for f in it if f.is_file() and f.suffix.lower() in SUPPORTED)
        results = []
        for f in files:
            try:
                results.append(extract_document(f.read_bytes(), f.name, args.get("doc_type", "auto"),
                                                include_text=False))
            except Exception as e:
                results.append({"file": f.name, "error": str(e)})
        out = Path(args["output"]).expanduser()
        out.write_bytes(export(results, out.suffix.lstrip(".") or "xlsx"))
        return {"output": str(out), "documents": len(results),
                "errors": [r for r in results if r.get("error")],
                "doc_types": {t: sum(r.get("doc_type") == t for r in results) for t in {r.get("doc_type") for r in results}}}
    raise ValueError(f"unknown tool {name}")


def handle(msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "free-ocr-api", "version": __version__}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params", {})
        try:
            out = call_tool(p.get("name"), p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}]}
        except Exception as e:
            result = {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
    elif method == "ping":
        result = {}
    elif mid is None:  # notification
        return None
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
