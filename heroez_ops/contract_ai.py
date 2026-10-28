"""AI reading of an approved contract template: which text is deal-specific?

Runs once per template, with a person reviewing the result before the template
is used. The AI only proposes field positions; it never writes contract text.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from . import doctemplate as dtp

log = logging.getLogger(__name__)

PROMPT_VERSION = "template_fields_v1"
PROMPT = (Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")
MODEL = "claude-opus-5-5"

FIELD_TYPES = ["text", "longtext", "date", "money", "money_words", "number", "contract_number"]
GROUPS = ["client", "contract", "campaign", "sow", "fee", "other"]

SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "label": {"type": "string"},
                "type": {"type": "string", "enum": FIELD_TYPES},
                "group": {"type": "string", "enum": GROUPS},
                "source": {"type": "string"},
                "occurrences": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"block": {"type": "string"}, "text": {"type": "string"},
                                   "whole": {"type": "boolean"}, "remove": {"type": "boolean"}},
                    "required": ["block", "text", "whole", "remove"], "additionalProperties": False}},
            },
            "required": ["key", "label", "type", "group", "source", "occurrences"],
            "additionalProperties": False}},
        "deliverables": {
            "type": "object",
            "properties": {
                "found": {"type": "boolean"},
                "table": {"type": "integer"},
                "template_row": {"type": "integer"},
                "sample_rows_after": {"type": "integer"},
                "columns": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"col": {"type": "integer"}, "key": {"type": "string"}, "label": {"type": "string"},
                                   "type": {"type": "string", "enum": ["text", "number", "money", "date"]}},
                    "required": ["col", "key", "label", "type"], "additionalProperties": False}},
            },
            "required": ["found", "table", "template_row", "sample_rows_after", "columns"],
            "additionalProperties": False},
    },
    "required": ["fields", "deliverables"],
    "additionalProperties": False,
}


class AnalysisError(Exception):
    pass


def analyze(original: bytes, api_key: str, client=None) -> tuple[dict, list[str]]:
    """Ask Claude for the deal-specific fields. Returns (spec, notes about dropped items)."""
    import anthropic
    client = client or anthropic.Anthropic(api_key=api_key, timeout=300.0, max_retries=2)
    text = dtp.outline(dtp.load(original))
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=PROMPT,
            messages=[{"role": "user", "content": f"<document>\n{text}\n</document>"}],
        )
    except anthropic.AuthenticationError as e:
        raise AnalysisError("Claude API key ditolak. Cek di halaman Pengaturan.") from e
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APIStatusError) as e:
        raise AnalysisError(f"Claude tidak bisa dihubungi ({type(e).__name__}). Coba lagi sebentar.") from e
    if resp.stop_reason != "end_turn":
        raise AnalysisError(f"AI berhenti tanpa hasil ({resp.stop_reason}). Coba lagi atau tandai field secara manual.")
    raw = json.loads(next(b.text for b in resp.content if b.type == "text"))
    return clean_spec(raw, original)


def clean_spec(raw: dict, original: bytes) -> tuple[dict, list[str]]:
    """Keep only what can really be placed in the document; learn each field's format."""
    doc = dtp.load(original)
    texts = {b.id: dtp.run_text(b.paragraph) for b in dtp.blocks(doc)}
    notes: list[str] = []
    fields, seen = [], set()
    for f in raw.get("fields", []):
        key = re.sub(r"[^a-z0-9_]", "_", f.get("key", "").lower()).strip("_") or "field"
        while key in seen:
            key += "_2"
        occs = []
        for o in f.get("occurrences", []):
            t = texts.get(o.get("block", ""))
            if t is None:
                notes.append(f"{f.get('label')}: blok {o.get('block')} tidak ada, diabaikan")
            elif o.get("remove") or o.get("whole") or (o.get("text") and o["text"] in t):
                occs.append({"block": o["block"], "text": o.get("text", "") if not o.get("whole") else t,
                             "whole": bool(o.get("whole")), "remove": bool(o.get("remove"))})
            else:
                notes.append(f"{f.get('label')}: “{o.get('text', '')[:40]}” tidak ditemukan persis di {o.get('block')}")
        if not occs:
            continue
        seen.add(key)
        example = next((o["text"] for o in occs if not o["remove"]), "")
        fields.append({"key": key, "label": f.get("label") or key, "type": f.get("type", "text"),
                       "group": f.get("group", "other"), "source": f.get("source", ""),
                       "example": example, "format": dtp.infer_format(f.get("type", "text"), example),
                       "occurrences": occs})
    keys = {f["key"] for f in fields}
    for f in fields:
        if f["type"] == "money_words" and f["source"] not in keys:
            money = [x["key"] for x in fields if x["type"] == "money"]
            f["source"] = money[0] if money else ""
            notes.append(f"{f['label']}: sumber angka tidak jelas, dipakai “{f['source'] or '-'}”")

    d = raw.get("deliverables") or {}
    deliverables = None
    if d.get("found"):
        try:
            table = doc.tables[d["table"]]
            row = table.rows[d["template_row"]]
            cells = [c.text for c in row.cells]
            cols = []
            for c in d.get("columns", []):
                if 0 <= c["col"] < len(cells):
                    cols.append({**c, "key": re.sub(r"[^a-z0-9_]", "_", c["key"].lower()),
                                 "example": cells[c["col"]], "format": dtp.infer_format(c["type"], cells[c["col"]])})
            deliverables = {"table": d["table"], "template_row": d["template_row"],
                            "sample_rows_after": max(0, min(d.get("sample_rows_after", 0),
                                                            len(table.rows) - d["template_row"] - 1)),
                            "columns": cols}
        except (IndexError, KeyError):
            notes.append("Tabel SOW yang ditunjuk AI tidak ada; SOW diisi sebagai teks.")
    return {"fields": fields, "deliverables": deliverables}, notes


def self_check(original: bytes, spec: dict) -> tuple[bool, str]:
    """Mark the template, refill it with the original values, and compare with the original.

    If they match, every field sits in the right place and nothing else changed.
    """
    template, problems = dtp.mark(original, spec)
    if problems:
        return False, "; ".join(problems)
    values = {}
    for f in spec["fields"]:
        ex = f.get("example", "")
        values[f["key"]] = example_value(f["type"], ex)
    rows = []
    d = spec.get("deliverables")
    if d:
        table = dtp.load(original).tables[d["table"]]
        for r in list(table.rows)[d["template_row"]: d["template_row"] + 1 + d.get("sample_rows_after", 0)]:
            cells = [c.text for c in r.cells]
            rows.append({c["key"]: example_value(c["type"], cells[c["col"]]) for c in d["columns"]})
    out = dtp.render(template, spec, values, rows)
    before, after = dtp.outline(dtp.load(original)).splitlines(), dtp.outline(dtp.load(out)).splitlines()
    if before == after:
        return True, "Template cocok: isi ulang dengan data contoh menghasilkan dokumen yang sama persis."
    diffs = [f"{a!r} → {b!r}" for a, b in zip(before, after) if a != b][:3]
    return False, "Beda setelah diisi ulang: " + " | ".join(diffs)


def marked_preview(original: bytes, spec: dict) -> str:
    """Template text with each field shown as [[Label]], for the human review screen."""
    template, _ = dtp.mark(original, spec)
    labels = {f["key"]: f["label"] for f in spec["fields"]}
    for c in (spec.get("deliverables") or {}).get("columns", []):
        labels["row." + c["key"]] = "SOW: " + c["label"]
    return dtp.PLACEHOLDER.sub(lambda m: f"[[{labels.get(m.group(1), m.group(1))}]]", dtp.outline(dtp.load(template)))


def example_value(ftype: str, text: str):
    """Turn example text from the template back into a raw value (for the self-check and form defaults)."""
    from .terbilang import parse_amount
    t = (text or "").strip()
    if ftype in ("money",):
        try:
            return parse_amount(re.sub(r"[.,]00$|,-$", "", t))
        except ValueError:
            return 0
    if ftype == "date":
        return parse_date(t)
    return t


def parse_date(t: str) -> str:
    import datetime as dt
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", t)
    if m:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    m = re.match(r"^(\d{1,2}) (\w+) (\d{4})$", t)
    if m:
        name = m.group(2).capitalize()
        months = dtp.BULAN if name in dtp.BULAN else dtp.MONTHS_EN
        if name in months:
            return dt.date(int(m.group(3)), months.index(name) + 1, int(m.group(1))).isoformat()
    return ""
