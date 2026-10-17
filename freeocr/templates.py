"""Custom extraction templates: describe the fields you need in a small YAML/JSON file.

Example (templates/acme-invoice.yaml):

    name: acme-invoice
    description: Invoices from ACME Corp
    match: ["ACME Corp"]          # auto-apply when any of these appear in the text
    fields:
      invoice_number: {label: ["Invoice #", "Invoice No"], type: id}
      order_date:     {label: "Order Date", type: date}
      total:          {regex: "Grand Total\\s*:?\\s*([\\d,.]+)", type: amount}
      customer_code:  {regex: "CUST-(\\d{5})"}
      approved:       {regex: "APPROVED", type: bool}

Field options: label (str | list), regex (first group or whole match), type
(string | id | amount | number | int | date | email | phone | percent | bool),
required (bool), default.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .extract import coerce, find_after

TYPES = {"string", "id", "amount", "number", "int", "date", "email", "phone", "percent", "bool"}


class TemplateError(ValueError):
    pass


def _load_file(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as e:  # pragma: no cover
            raise TemplateError("pip install pyyaml to use YAML templates") from e
        return yaml.safe_load(raw) or {}
    return json.loads(raw)


def validate(tpl: dict) -> dict:
    if not isinstance(tpl, dict) or not isinstance(tpl.get("fields"), dict) or not tpl["fields"]:
        raise TemplateError("template needs a non-empty 'fields' mapping")
    tpl.setdefault("name", "custom")
    for name, spec in list(tpl["fields"].items()):
        if isinstance(spec, str):  # shorthand: field: "Label text"
            spec = {"label": spec}
            tpl["fields"][name] = spec
        if not isinstance(spec, dict) or not (spec.get("label") or spec.get("regex")):
            raise TemplateError(f"field '{name}' needs a 'label' or 'regex'")
        t = spec.get("type", "string")
        if t not in TYPES:
            raise TemplateError(f"field '{name}': unknown type '{t}' (use one of {sorted(TYPES)})")
        if spec.get("regex"):
            try:
                re.compile(spec["regex"])
            except re.error as e:
                raise TemplateError(f"field '{name}': bad regex: {e}") from e
    return tpl


def template_dirs() -> list[Path]:
    dirs = [Path(d) for d in os.getenv("FREEOCR_TEMPLATES", "templates").split(os.pathsep) if d]
    return [d for d in dirs if d.is_dir()]


def load_templates() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for d in template_dirs():
        for p in sorted(d.glob("*")):
            if p.suffix.lower() not in (".yaml", ".yml", ".json"):
                continue
            try:
                tpl = validate(_load_file(p))
                out[tpl["name"]] = tpl
            except Exception:
                continue
    return out


def parse_template(raw: str | dict) -> dict:
    if isinstance(raw, dict):
        return validate(raw)
    raw = raw.strip()
    try:
        return validate(json.loads(raw))
    except json.JSONDecodeError:
        import yaml

        return validate(yaml.safe_load(raw))


def auto_match(text: str, templates: dict[str, dict]) -> dict | None:
    low = text.lower()
    for tpl in templates.values():
        keys = tpl.get("match") or []
        if isinstance(keys, str):
            keys = [keys]
        if keys and any(k.lower() in low for k in keys):
            return tpl
    return None


def apply_template(text: str, tpl: dict, dayfirst: bool | None = None) -> tuple[dict, dict, list[str]]:
    values: dict[str, Any] = {}
    conf: dict[str, float] = {}
    missing: list[str] = []
    for name, spec in tpl["fields"].items():
        kind = spec.get("type", "string")
        val = None
        if spec.get("regex"):
            flags = re.I if spec.get("ignore_case", True) else 0
            m = re.search(spec["regex"], text, flags | re.M)
            if kind == "bool":
                val = bool(m)
            elif m:
                raw = m.group(1) if m.groups() else m.group(0)
                val = coerce(raw, "string" if kind == "id" else kind, dayfirst) if kind != "string" else raw.strip()
            if val is not None:
                conf[name] = 0.95
        if val is None and spec.get("label"):
            labels = spec["label"] if isinstance(spec["label"], list) else [spec["label"]]
            found = find_after(text, labels, "string" if kind == "bool" else kind, dayfirst)
            if found:
                val, conf[name] = found
                if kind == "bool":
                    val = True
            elif kind == "bool":
                val = False
        if val is None:
            val = spec.get("default")
            if spec.get("required"):
                missing.append(name)
        values[name] = val
    return values, conf, missing
