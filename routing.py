"""
Routing-Logik: Wer soll auf eine Nachricht antworten?

Bewusst getrennt von relay.py, damit sie ohne Slack und ohne API-Keys
testbar ist (siehe test_routing.py).
"""

from __future__ import annotations

import re
from enum import Enum


class Target(Enum):
    CLAUDE = "claude"
    CHATTY = "chatty"


# Präfixe, mit denen ein Mensch (oder ein Bot) jemanden anspricht.
# Groß-/Kleinschreibung egal. Slack-Mentions (<@U...>) werden vorher entfernt.
_CLAUDE_PREFIX = re.compile(r"^\s*@?claude\s*[:,\-]?\s*", re.IGNORECASE)
_CHATTY_PREFIX = re.compile(r"^\s*@?(chatty|chatgpt|gpt)\s*[:,\-]?\s*", re.IGNORECASE)
_BOTH_PREFIX = re.compile(r"^\s*@?(beide|both|all)\s*[:,\-]?\s*", re.IGNORECASE)

# Am Ende einer Bot-Antwort: "… @chatty" oder "… @claude" übergibt das Wort.
_HANDOFF_CLAUDE = re.compile(r"@claude\s*[\.\!\?]?\s*$", re.IGNORECASE)
_HANDOFF_CHATTY = re.compile(r"@(chatty|chatgpt|gpt)\s*[\.\!\?]?\s*$", re.IGNORECASE)

_SLACK_MENTION = re.compile(r"<@[A-Z0-9]+>")
_RELAY_TAGS = ("🟠 *Claude*", "🟢 *Chatty*")


# Der Slack-Connector hängt an Nachrichten "*Gesendet mit* Claude" an; das darf
# weder eine Übergabe am Textende verdecken noch in den Verlauf der Bots geraten.
_FOOTER = re.compile(r"(\s*\*?_?Gesendet mit_?\*?\s*(Claude|ChatGPT|Chatty)\s*)+$", re.IGNORECASE)


def strip_footer(text: str) -> str:
    return _FOOTER.sub("", text).rstrip()


def _clean(text: str) -> str:
    text = strip_footer(text)
    text = _SLACK_MENTION.sub("", text)
    for tag in _RELAY_TAGS:
        if text.startswith(tag):
            text = text[len(tag):]
    return text.strip()


def decide(text: str, *, from_relay: bool) -> list[Target]:
    """
    Gibt die Liste der Ziele zurück, die antworten sollen (leer = niemand).

    from_relay=True  -> Nachricht kommt vom Bot selbst. Dann zählt nur eine
                        explizite Übergabe am Ende ("… @chatty"). So entsteht
                        Bot-zu-Bot-Dialog nur, wenn einer den anderen anspricht.
    from_relay=False -> Nachricht von einem Menschen. Präfix entscheidet.
    """
    t = _clean(text)
    if not t:
        return []

    if from_relay:
        # Fehlermeldungen des Relays dürfen keine Runde auslösen
        if t.startswith("(Claude konnte") or t.startswith("(Chatty konnte"):
            return []
        if _HANDOFF_CLAUDE.search(t):
            return [Target.CLAUDE]
        if _HANDOFF_CHATTY.search(t):
            return [Target.CHATTY]
        return []

    if _BOTH_PREFIX.match(t):
        return [Target.CLAUDE, Target.CHATTY]
    if _CLAUDE_PREFIX.match(t):
        return [Target.CLAUDE]
    if _CHATTY_PREFIX.match(t):
        return [Target.CHATTY]
    return []


def strip_prefix(text: str) -> str:
    """Entfernt Anrede-Präfix und Bot-Tag, lässt den eigentlichen Inhalt übrig."""
    t = _clean(text)
    for rx in (_BOTH_PREFIX, _CLAUDE_PREFIX, _CHATTY_PREFIX):
        m = rx.match(t)
        if m:
            t = t[m.end():]
            break
    return t.strip()
