"""Command line:  freeocr --help"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from .export import FORMATS, export
from .ocr import IMAGE_EXTS, TEXT_EXTS, available_engines

SUPPORTED = IMAGE_EXTS | TEXT_EXTS | {".pdf"}


def collect(paths: list[str], recursive: bool) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            out += sorted(f for f in it if f.is_file() and f.suffix.lower() in SUPPORTED)
        elif p.is_file():
            out.append(p)
        else:
            print(f"skip: {p} not found", file=sys.stderr)
    return out


def _fields(v):
    if not v:
        return None
    return json.loads(v) if v.strip().startswith(("{", "[")) else [x.strip() for x in v.split(",") if x.strip()]


def _dayfirst(v):
    return None if v in (None, "auto") else v in ("1", "true", "yes")


def run_one(path: Path, a) -> dict:
    from .pipeline import extract_document

    try:
        return extract_document(path.read_bytes(), path.name, a.type, a.engine, a.lang, a.template,
                                _fields(a.fields), a.llm, a.redact, include_text=not a.no_text,
                                force_ocr=a.force_ocr, dayfirst=_dayfirst(a.dayfirst))
    except Exception as e:
        return {"file": path.name, "error": str(e)}


def write_out(results: list[dict], fmt: str, out: str | None):
    body = export(results, fmt)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(body)
        print(f"wrote {out}  ({len(results)} document(s))", file=sys.stderr)
    elif fmt == "xlsx":
        sys.exit("xlsx needs -o/--output FILE")
    else:
        sys.stdout.write(body.decode("utf-8"))
        sys.stdout.write("\n")


def cmd_extract(a):
    files = collect(a.paths, a.recursive)
    if not files:
        sys.exit("no supported files found")
    fmt = a.format or (Path(a.output).suffix.lstrip(".").lower() if a.output else "json")
    if fmt not in FORMATS:
        sys.exit(f"format must be one of {FORMATS}")
    results = []
    for i, f in enumerate(files, 1):
        if len(files) > 1:
            print(f"[{i}/{len(files)}] {f}", file=sys.stderr)
        results.append(run_one(f, a))
    write_out(results, fmt, a.output)
    if any(r.get("error") for r in results):
        sys.exit(1)


def cmd_ocr(a):
    from .pipeline import ocr_document

    for f in collect(a.paths, a.recursive):
        res = ocr_document(f.read_bytes(), f.name, a.engine, a.lang, a.force_ocr)
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            if len(a.paths) > 1:
                print(f"===== {f} =====")
            print(res["text"])


def cmd_pdf(a):
    from .searchable import make_searchable_pdf

    src = Path(a.input)
    out = Path(a.output) if a.output else src.with_name(src.stem + "-searchable.pdf")
    pdf, stats = make_searchable_pdf(src.read_bytes(), src.name, a.lang, a.force_ocr)
    out.write_bytes(pdf)
    print(f"wrote {out}  ({stats['pages']} page(s), {stats['ocr_pages']} OCR'd, {stats['kept_pages']} kept)",
          file=sys.stderr)


def cmd_watch(a):
    """Data-entry autopilot: drop files in a folder, rows appear in a spreadsheet."""
    inbox = Path(a.folder)
    inbox.mkdir(parents=True, exist_ok=True)
    done = Path(a.done) if a.done else None
    if done:
        done.mkdir(parents=True, exist_ok=True)
    out = Path(a.output)
    fmt = out.suffix.lstrip(".").lower() or "xlsx"
    store = out.with_suffix(out.suffix + ".jsonl")
    results = []
    seen: set[str] = set()
    if store.exists():
        for line in store.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                results.append(r)
                seen.add(r.get("sha256") or r.get("file"))
    print(f"watching {inbox}  ->  {out}   (Ctrl+C to stop)", file=sys.stderr)
    while True:
        new = 0
        for f in collect([str(inbox)], a.recursive):
            if done and done in f.parents:
                continue
            try:
                if time.time() - f.stat().st_mtime < 1.5:  # still being copied
                    continue
                import hashlib

                digest = hashlib.sha256(f.read_bytes()).hexdigest()
            except OSError:
                continue
            if digest in seen:
                continue
            a.no_text = True
            r = run_one(f, a)
            r.setdefault("sha256", digest)
            r["processed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            seen.add(digest)
            results.append(r)
            with store.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            status = r.get("error") or f"{r.get('doc_type')} · {len(r.get('fields') or {})} fields"
            print(f"  + {f.name}: {status}", file=sys.stderr)
            if done:
                shutil.move(str(f), str(done / f.name))
            new += 1
        if new:
            out.write_bytes(export(results, fmt))
            print(f"  updated {out} ({len(results)} rows)", file=sys.stderr)
        if a.once:
            if not new and not out.exists():
                out.write_bytes(export(results, fmt))
            return
        time.sleep(a.interval)


def cmd_serve(a):
    import uvicorn

    uvicorn.run("freeocr.api:app", host=a.host, port=a.port, workers=a.workers, reload=False)


def cmd_mcp(a):
    from .mcp_server import main

    main()


def cmd_info(a):
    from .extract import DOC_TYPES
    from .llm import enabled
    from .templates import load_templates

    print(json.dumps({"version": __version__, "engines": available_engines(), "doc_types": DOC_TYPES,
                      "formats": list(FORMATS), "templates": sorted(load_templates()),
                      "llm_assist": enabled()}, indent=2))


def cmd_samples(a):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))
    try:
        from make_samples import make_all
    except ImportError:
        sys.exit("samples generator ships with the git repo (examples/make_samples.py)")
    for k, p in make_all(Path(a.folder)).items():
        print(f"{k:14s} {p}")


def add_common(p):
    p.add_argument("--type", default="auto", help="auto | invoice | receipt | bank_statement | purchase_order | payslip | resume | id_document | generic")
    p.add_argument("--lang", default=os.getenv("FREEOCR_LANG", "eng"), help="OCR language(s), e.g. eng, hin, deu, eng+fra")
    p.add_argument("--engine", default="auto", help="auto | tesseract | rapidocr")
    p.add_argument("--template", help="template name (from ./templates) or inline YAML/JSON")
    p.add_argument("--fields", help="extra fields to fill, e.g. 'order_id,store' (with --llm uses a local model)")
    p.add_argument("--llm", action="store_true", help="use the configured local model for missing fields")
    p.add_argument("--redact", action="store_true", help="mask emails, phones, cards, IBANs, tax IDs")
    p.add_argument("--dayfirst", default="auto", help="auto | true | false  (31/12 vs 12/31)")
    p.add_argument("--force-ocr", action="store_true", help="OCR even when a PDF has a text layer")
    p.add_argument("-r", "--recursive", action="store_true", help="include sub-folders")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="freeocr", description="Free self-hosted OCR + document data extraction")
    ap.add_argument("--version", action="version", version=f"freeocr {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extract", help="extract structured data from files or folders")
    p.add_argument("paths", nargs="+")
    p.add_argument("-f", "--format", choices=FORMATS, help="json (default) | csv | xlsx | md | txt")
    p.add_argument("-o", "--output", help="output file (format taken from extension)")
    p.add_argument("--no-text", action="store_true", help="omit raw text from JSON")
    add_common(p)
    p.set_defaults(fn=cmd_extract)

    p = sub.add_parser("ocr", help="plain text from PDFs / images")
    p.add_argument("paths", nargs="+")
    p.add_argument("--json", action="store_true")
    p.add_argument("--lang", default=os.getenv("FREEOCR_LANG", "eng"))
    p.add_argument("--engine", default="auto")
    p.add_argument("--force-ocr", action="store_true")
    p.add_argument("-r", "--recursive", action="store_true")
    p.set_defaults(fn=cmd_ocr)

    p = sub.add_parser("pdf", help="make a searchable PDF (invisible text layer) from a scan or image")
    p.add_argument("input")
    p.add_argument("-o", "--output", help="output PDF (default: <name>-searchable.pdf)")
    p.add_argument("--lang", default=os.getenv("FREEOCR_LANG", "eng"))
    p.add_argument("--force-ocr", action="store_true", help="OCR pages even if they already have text")
    p.set_defaults(fn=cmd_pdf)

    p = sub.add_parser("watch", help="watch a folder and keep a spreadsheet up to date")
    p.add_argument("folder")
    p.add_argument("-o", "--output", default="extracted.xlsx")
    p.add_argument("--done", help="move processed files here")
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--once", action="store_true", help="process what is there and exit")
    p.add_argument("--no-text", action="store_true", default=True, help=argparse.SUPPRESS)
    add_common(p)
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("serve", help="start the REST API + web UI")
    p.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    p.add_argument("--workers", type=int, default=int(os.getenv("WORKERS", "1")))
    p.set_defaults(fn=cmd_serve)

    sub.add_parser("mcp", help="run as an MCP server (stdio) for AI assistants").set_defaults(fn=cmd_mcp)
    sub.add_parser("info", help="show engines, doc types and templates").set_defaults(fn=cmd_info)
    p = sub.add_parser("samples", help="generate sample documents to try")
    p.add_argument("folder", nargs="?", default="samples")
    p.set_defaults(fn=cmd_samples)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
