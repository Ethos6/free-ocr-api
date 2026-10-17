# Contributing

Thanks for helping! Bug reports with a sample document (remove personal data first) are the most useful contribution.

```bash
git clone https://github.com/Ethos6/free-ocr-api && cd free-ocr-api
pip install -e ".[dev]"          # needs tesseract installed (or: pip install -e ".[dev,rapidocr]")
pytest
freeocr serve                    # http://localhost:8000
```

Good first contributions:

- New document type extractors in `freeocr/extract.py` (utility bills, shipping labels, insurance claims ...)
- Country-specific IDs and tax numbers in `freeocr/parsers.py`
- Templates for common vendors in `templates/`
- Better line-item detection for unusual layouts
- Translations of the web UI

Please add a test for any new extractor (see `tests/test_freeocr.py` and `examples/make_samples.py`).
