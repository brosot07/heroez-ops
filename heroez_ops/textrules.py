"""Cheap text rules that run before (or instead of) any LLM call.

They decide three things about an inbound message:
  * is it a pure acknowledgement ("ok makasih kak 🙏", a sticker, a 👍)?
  * does it carry gratitude for something we sent ("makasih listnya kak")?
  * does it ask for something (question, request)?
"""
from __future__ import annotations

import re
import unicodedata

ACK_WORDS = {
    "ok", "oke", "okay", "okey", "okeh", "oc", "k", "sip", "siap", "ashiap", "noted", "note", "baik", "good",
    "great", "nice", "mantap", "mantab", "mantul", "alright", "done", "received", "diterima", "sudah", "udah",
    "dah", "ya", "iya", "y", "yes", "yup", "yep", "betul", "benar", "setuju", "deal", "makasih", "makasi", "mksh",
    "makasii", "trims", "trimakasih", "terimakasih", "terima", "kasih", "thanks", "thank", "thx", "tq", "ty",
    "tengkyu", "you", "u", "so", "much", "very", "banyak", "sama", "juga", "kak", "ka", "kakak", "kk", "bang",
    "mas", "mbak", "mba", "pak", "bu", "bro", "sis", "min", "nggih", "inggih", "wkwk", "hehe", "haha", "hihi",
    "aman", "beres", "cakep", "well", "noted.", "all", "the", "best", "ditunggu", "tunggu", "sekali", "lagi",
    # "the client will get back to us": the ball is in their court
    "nanti", "aku", "saya", "kami", "info", "infoin", "kabari", "kabarin", "dikabari", "update", "diskusi",
    "diskusikan", "dulu", "cek", "internal", "will", "i", "we", "let", "know", "later", "get", "back", "to",
}

GRATITUDE = re.compile(
    r"\b(makasi+h?|mksh|trims|terima ?kasih|thanks?|thank you|thx|tq|ty|tengkyu|noted|sudah (di)?terima|udah (di)?terima|received)\b",
    re.I,
)

QUESTION = re.compile(
    r"\?|\b(berapa|apakah|apa|gimana|bagaimana|kapan|dimana|di ?mana|kenapa|mengapa|bisa(kah)?|boleh(kah)?|"
    r"tanya|minta|tolong|mohon|info (dong|donk|ya kak|kak)|rate ?card|ratecard|rate|harga|price ?list|pricelist|budget|"
    r"how|what|when|where|why|which|can|could|would|please|pls|plz|kirim(in|kan)?|send|share|"
    r"ada(kah)?|sudahkah|udah belum|belum|kok|urgent|segera|asap)\b",
    re.I,
)

_LONG_DIGITS = re.compile(r"\d[\d\s\-.]{6,}\d")


def redact(text: str) -> str:
    """Hide long digit runs (bank accounts, NIK, card numbers) before alerts or AI calls."""
    return _LONG_DIGITS.sub("•••", text)


_WORD = re.compile(r"[a-z0-9]+")


def _squash(word: str) -> str:
    """'okeee' -> 'oke', 'yaaa' -> 'ya', 'makasiiih' -> 'makasih'."""
    return re.sub(r"(.)\1+", r"\1", word)


_ACK_SQUASHED = {_squash(w) for w in ACK_WORDS}


def is_emoji_only(text: str) -> bool:
    stripped = "".join(ch for ch in text if not ch.isspace())
    if not stripped:
        return False
    return all(unicodedata.category(ch) in ("So", "Sk", "Mn", "Cf", "Po") or ch in "‍️" for ch in stripped)


def is_pure_ack(msg_type: str, text: str) -> bool:
    """Needs no reply: sticker, reaction, emoji-only, or only acknowledgement words."""
    if msg_type in ("sticker", "reaction"):
        return True
    text = (text or "").strip()
    if not text:
        return False
    if is_emoji_only(text):
        return True
    if "?" in text:
        return False
    words = _WORD.findall(text.lower().replace("sama2", "sama sama"))
    if not words or len(words) > 8:
        return False
    return all(w.isdigit() and w == "2" or _squash(w) in _ACK_SQUASHED for w in words)


def asks_something(text: str) -> bool:
    return bool(text) and bool(QUESTION.search(text))


def has_gratitude(text: str) -> bool:
    return bool(text) and bool(GRATITUDE.search(text))


def classify_inbound(msg_type: str, text: str) -> str:
    """One of: 'ack' (no reply needed), 'thanks' (thanks for something), 'needs_reply'."""
    if is_pure_ack(msg_type, text):
        return "ack"
    if has_gratitude(text) and not asks_something(text):
        return "thanks"
    return "needs_reply"
