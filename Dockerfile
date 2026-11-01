FROM python:3.15-slim

# Extra OCR languages (Tesseract codes). English is always included.
# e.g. docker build --build-arg OCR_LANGS="hin deu fra spa por ita ara chi-sim jpn" .
ARG OCR_LANGS="hin deu fra spa por ita"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 PORT=8000 FREEOCR_TEMPLATES=/app/templates

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    $(for l in $OCR_LANGS; do echo "tesseract-ocr-$l"; done) \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY freeocr ./freeocr
RUN pip install .
COPY templates ./templates
COPY examples ./examples

RUN useradd -m -u 1000 app && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health')" || exit 1
CMD ["sh", "-c", "freeocr serve --host $HOST --port $PORT --workers ${WORKERS:-2}"]
