"""Optional: let a local or self-hosted language model fill in fields the rules missed.

Off by default. Point it at any server that speaks the common
``/v1/chat/completions`` format (Ollama, LM Studio, vLLM, LocalAI ...):

    FREEOCR_LLM_URL=http://localhost:11434/v1
    FREEOCR_LLM_MODEL=<your-model>
    FREEOCR_LLM_KEY=           # only if your server needs one

The document text never leaves your machine unless you point this at a remote server.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

MAX_CHARS = int(os.getenv("FREEOCR_LLM_MAX_CHARS", "12000"))


def enabled() -> bool:
    return bool(os.getenv("FREEOCR_LLM_URL") and os.getenv("FREEOCR_LLM_MODEL"))


def _json_from(reply: str) -> dict:
    m = re.search(r"\{.*\}", reply, re.S)
    if not m:
        raise ValueError("model did not return JSON")
    return json.loads(m.group(0))


def ask_fields(text: str, fields: dict[str, str] | list[str], doc_type: str = "document",
               timeout: float = 60.0) -> dict[str, Any]:
    if not enabled():
        raise RuntimeError("LLM assist is not configured (set FREEOCR_LLM_URL and FREEOCR_LLM_MODEL)")
    if isinstance(fields, list):
        fields = {f: "" for f in fields}
    schema = "\n".join(f"- {k}: {v}" if v else f"- {k}" for k, v in fields.items())
    prompt = (
        f"Extract these fields from the {doc_type} below. Reply with ONE JSON object only, using exactly "
        f"these keys. Use null when a value is not present. Dates as YYYY-MM-DD, amounts as numbers.\n\n"
        f"Fields:\n{schema}\n\nDocument:\n\"\"\"\n{text[:MAX_CHARS]}\n\"\"\""
    )
    url = os.environ["FREEOCR_LLM_URL"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if os.getenv("FREEOCR_LLM_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['FREEOCR_LLM_KEY']}"
    body = {
        "model": os.environ["FREEOCR_LLM_MODEL"],
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "You are a precise data-entry assistant. Output JSON only."},
            {"role": "user", "content": prompt},
        ],
    }
    r = httpx.post(url, json=body, headers=headers, timeout=timeout)
    r.raise_for_status()
    reply = r.json()["choices"][0]["message"]["content"]
    data = _json_from(reply)
    return {k: data.get(k) for k in fields}
