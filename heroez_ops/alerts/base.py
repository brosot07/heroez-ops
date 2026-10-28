"""AlertChannel interface. Telegram today; another channel can be added later."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Button:
    text: str
    data: str   # callback payload, max 64 bytes


class AlertChannel(ABC):
    @abstractmethod
    def send(self, chat_id: int, text: str, buttons: list[list[Button]] | None = None) -> int | None:
        """Send a message; return the channel's message id (None if not sent)."""

    @abstractmethod
    def edit(self, chat_id: int, message_id: int, text: str, buttons: list[list[Button]] | None = None) -> None:
        """Replace an earlier message's text and buttons."""

    @abstractmethod
    def edit_buttons(self, chat_id: int, message_id: int, buttons: list[list[Button]] | None) -> None:
        """Replace only the buttons of an earlier message."""

    @abstractmethod
    def answer_callback(self, callback_id: str, text: str) -> None:
        """Acknowledge a button press with a short toast."""
