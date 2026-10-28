"""Knowledge base: FAQ, rate card, company info, tone guide and the bot's fixed texts.

Edited by supervisors on the "Pengetahuan" page. The bot sends only these approved
texts word for word; AI drafts may only use what is written here.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata

from .db import Store

# The intake bot's messages. Editable; {sla} is filled in from the settings.
DEFAULT_TEXTS = {
    "greeting": "Halo kak, terima kasih sudah menghubungi Heroez 🙌\nBoleh tahu kakak menghubungi kami sebagai apa?",
    "brand_ask": ("Halo kak, terima kasih sudah menghubungi Heroez 🙌\nBoleh info brand/agency-nya, campaign seperti apa "
                  "yang direncanakan, dan talent incaran (kalau ada)? Sebutkan juga platform dan tanggal campaign-nya ya kak."),
    "brand_ask_dates": "Siap kak! Campaign-nya rencananya tanggal berapa? Supaya kami bisa cek ketersediaan talent-nya 🙏",
    "talent_list_intro": "Berikut info talent yang kakak tanyakan:",
    "recommend_intro": "Berikut beberapa talent kami yang cocok:",
    "not_in_list": "Untuk {names}, admin kami akan cek dulu ya kak.",
    "alternatives_intro": "Sebagai alternatif, talent berikut tersedia di tanggal tersebut:",
    "price_note": "Harga per konten, belum termasuk pajak.",
    "ratecard_caption": "Ini rate card paket kami ya kak 🙏",
    "handover": "Admin kami akan melanjutkan diskusinya dalam {sla} 🙏",
    "talent_prompt": "Halo kak! Boleh kirim handle (IG/TikTok/YouTube), platform utama, dan jumlah followers-nya? 🙏",
    "talent_done": "Terima kasih kak! Tim talent kami akan menghubungi kakak 🙏",
    "other_prompt": "Siap kak, boleh tulis singkat keperluannya? Tim kami akan membalas secepatnya 🙏",
    "other_done": "Terima kasih kak, pesan sudah kami terima. Tim kami akan membalas dalam {sla} 🙏",
}
TEXT_LABELS = {
    "greeting": "Sapaan jika pesan pertama belum jelas (dengan tombol Brand / Talent / Lainnya)",
    "brand_ask": "Brand: tanya kebutuhan, talent incaran, platform, tanggal",
    "brand_ask_dates": "Brand: tanya tanggal campaign (jika talent sudah disebut)",
    "talent_list_intro": "Brand: pembuka daftar talent yang ditanyakan",
    "recommend_intro": "Brand: pembuka rekomendasi talent",
    "not_in_list": "Brand: talent tidak ada di daftar ({names})",
    "alternatives_intro": "Brand: pembuka alternatif jika talent incaran tidak tersedia",
    "price_note": "Catatan harga di bawah daftar",
    "ratecard_caption": "Keterangan saat kirim file rate card",
    "handover": "Penutup: admin akan melanjutkan",
    "talent_prompt": "Talent/KOL: minta data", "talent_done": "Talent/KOL: penutup",
    "other_prompt": "Lainnya: minta keperluan", "other_done": "Lainnya: penutup",
}
INFO_SECTIONS = {
    "ratecard_text": "Rate card (paket & harga “mulai dari”, untuk AI. Jangan tulis rate per talent)",
    "process": "Alur kerja campaign (inquiry → brief → kontrak → konten → posting → report → pembayaran)",
    "payment_terms": "Ketentuan pembayaran",
    "company": "Tentang Heroez (layanan, platform, kategori talent)",
    "tone": "Gaya bahasa (sapaan kak/bang, emoji, formalitas)",
}

CATEGORIES = [("beauty", "Beauty & skincare"), ("fnb", "F&B"), ("gaming", "Gaming"), ("fashion", "Fashion"),
              ("tech", "Tech & gadget"), ("other", "Lainnya")]
BUDGETS = [("b1", "< Rp25 juta", 0), ("b2", "Rp25–100 juta", 25_000_000), ("b3", "Rp100–500 juta", 100_000_000),
           ("b4", "> Rp500 juta", 500_000_000), ("b0", "Belum tahu", 0)]
TIMELINES = [("t1", "≤ 2 minggu"), ("t2", "Dalam 1–2 bulan"), ("t3", "Masih eksplor")]


def normalize(text: str) -> str:
    """Lowercase, strip emoji/punctuation, collapse spaces: for exact FAQ matching."""
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in t)
    return re.sub(r"\s+", " ", t).strip()


class KB:
    def __init__(self, store: Store):
        self.store = store

    # -- generic --------------------------------------------------------------
    def get(self, name: str, default=None):
        row = self.store.one("SELECT value FROM kb WHERE name=?", (name,))
        return json.loads(row["value"]) if row else default

    def set(self, name: str, value, who: str) -> None:
        self.store.x("INSERT INTO kb(name,value,updated_at,updated_by) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                     "value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                     (name, json.dumps(value, ensure_ascii=False), int(time.time()), who))
        self.store.audit(who, "kb_edit", None, {"item": name})

    # -- texts ----------------------------------------------------------------
    def text(self, key: str, sla: str | None = None) -> str:
        """An approved bot text; {sla} is filled only when sla is given."""
        t = (self.get("texts", {}) or {}).get(key) or DEFAULT_TEXTS[key]
        return t if sla is None else t.replace("{sla}", sla)

    def info(self, key: str) -> str:
        return (self.get("info", {}) or {}).get(key, "")

    # -- FAQ ------------------------------------------------------------------
    def faqs(self) -> list[dict]:
        """[{id, questions: [...], answer_id, answer_en, auto_send}]"""
        return self.get("faq", []) or []

    def match_faq(self, text: str) -> dict | None:
        """An auto-send FAQ whose question matches the message exactly (after normalising)."""
        n = normalize(text)
        if not n:
            return None
        for f in self.faqs():
            if f.get("auto_send") and any(normalize(q) == n for q in f.get("questions", [])):
                return f
        return None

    def context_for_ai(self) -> str:
        """Everything the AI may use when drafting a reply."""
        parts = []
        for key, label in INFO_SECTIONS.items():
            if self.info(key).strip():
                parts.append(f"## {label}\n{self.info(key).strip()}")
        faq = self.faqs()
        if faq:
            parts.append("## FAQ (jawaban yang sudah disetujui)\n" + "\n\n".join(
                f"T: {' / '.join(f.get('questions', []))}\nJ: {f.get('answer_id', '')}"
                + (f"\nJ (EN): {f['answer_en']}" if f.get("answer_en") else "") for f in faq))
        return "\n\n".join(parts)
