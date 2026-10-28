"""InboxSource adapter interface and the normalised events it produces.

Business logic only sees these events, so another inbox (e.g. Chatwoot) can be
added later as a second adapter without touching the pipeline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MessageEvent:
    direction: str          # "in" (customer -> Heroez) or "out" (Heroez -> customer)
    source: str             # messages | echo | history
    wa_id: str              # customer number, digits only with country code
    msg_id: str             # provider message id, used for dedupe
    ts: int                 # UTC epoch seconds
    type: str               # text | image | sticker | reaction | audio | ...
    text: str = ""          # body / caption / button title; "" for media without caption
    contact_name: str | None = None
    raw: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class ContactEvent:
    wa_id: str
    name: str | None
    removed: bool = False


Event = MessageEvent | ContactEvent


class InboxSource(ABC):
    name: str

    @abstractmethod
    def verify_signature(self, body: bytes, headers: dict[str, str]) -> bool:
        """True if the webhook really comes from the provider."""

    @abstractmethod
    def parse(self, payload: dict) -> list[Event]:
        """Turn one webhook payload into normalised events (may be empty)."""
