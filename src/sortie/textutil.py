"""Small text helpers shared by site adapters."""

from __future__ import annotations

import html
import re


def html_to_text(text: str) -> str:
    """Job-description HTML as plain text: one line per paragraph / list item."""
    text = re.sub(r"<\s*(br|/p|/div|/li|/h\d)\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<\s*li[^>]*>", "- ", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)
