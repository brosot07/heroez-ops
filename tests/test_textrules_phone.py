import pytest

from heroez_ops.phone import normalize
from heroez_ops.textrules import classify_inbound


@pytest.mark.parametrize("text", [
    "ok makasih kak 🙏", "Oke kak", "siap", "noted", "👍", "🙏🙏", "thank you so much!", "okeee", "sip mantap",
    "baik kak, terima kasih", "sama2 kak", "iya kak", "ok kak nanti aku info ya 🙏", "siap kak, kami diskusi internal dulu",
    "will let you know",
])
def test_acks_need_no_reply(text):
    assert classify_inbound("text", text) == "ack"


@pytest.mark.parametrize("msg_type", ["sticker", "reaction"])
def test_stickers_and_reactions_need_no_reply(msg_type):
    assert classify_inbound(msg_type, "") == "ack"


@pytest.mark.parametrize("text", ["makasih listnya kak", "thanks for the deck", "noted kak, sudah diterima filenya"])
def test_thanks_for_something(text):
    assert classify_inbound("text", text) == "thanks"


@pytest.mark.parametrize("text", [
    "Halo kak, mau tanya rate card untuk TikTok", "ok makasih, tapi kapan bisa kirim?", "Halo",
    "Kak, invoice bulan lalu belum dibayar", "Can you share talent list for beauty?", "ok?",
    "info dong kak rate untuk YouTube", "nanti aku info budget, tapi talentnya siapa aja?",
])
def test_needs_reply(text):
    assert classify_inbound("text", text) == "needs_reply"


def test_media_without_caption_needs_reply():
    assert classify_inbound("image", "") == "needs_reply"
    assert classify_inbound("audio", "") == "needs_reply"


def test_phone_normalize():
    assert normalize("0812-3456-789", local=True) == "628123456789"
    assert normalize("+62 812 3456 789", local=True) == "628123456789"
    assert normalize("812 3456 789", local=True) == "628123456789"
    assert normalize("819012345678") == "819012345678"  # Meta wa_id (Japan) untouched
    assert normalize("") == ""
