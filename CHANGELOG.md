# Changelog

## 1.1.0 — 2026-10-18

### Added
- **Searchable PDF output.** Turn scans and photos into PDFs with an invisible, selectable text layer. Pages that already contain text are copied through untouched.
  - API: `POST /v1/searchable-pdf`
  - CLI: `freeocr pdf scan.pdf -o scan-searchable.pdf`
  - MCP tool: `make_searchable_pdf`
  - Web UI: new **Searchable PDF** button
- Demo GIF in the README, recorded from the real UI with the bundled sample documents (`scripts/record_demo.py`).

## 1.0.0 — 2026-10-17

First release: OCR (Tesseract or RapidOCR), 8 auto-detected document types, line items, bank transactions, entities, templates, PII redaction, JSON/CSV/Excel/Markdown export, folder watch mode, web UI, CLI, MCP server and Docker image.
