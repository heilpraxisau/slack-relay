"""
Slack Relay: Claude <-> ChatGPT

Ein kleiner Dienst, der in einem Slack-Kanal lauscht und Nachrichten an
Claude (Anthropic) oder ChatGPT (OpenAI) weiterreicht. Antworten landen
im selben Thread. Schutz gegen Endlosschleifen eingebaut.

Routing (siehe routing.py):
  - Nachricht beginnt mit "@claude" / "claude:" / "claude,"  -> Claude antwortet
  - Nachricht beginnt mit "@chatty" / "chatty:" / "gpt:"     -> ChatGPT antwortet
  - Nachricht beginnt mit "@beide" / "beide:"                -> beide antworten
  - Antwort eines Bots endet mit "@chatty"/"@claude"         -> der andere antwortet
    (das ist der Bot-zu-Bot-Pingpong, begrenzt durch MAX_BOT_TURNS)
  - sonst passiert nichts

Env-Variablen (siehe .env.example):
  SLACK_BOT_TOKEN, SLACK_APP_TOKEN, ANTHROPIC_API_KEY, OPENAI_API_KEY,
  optional: RELAY_CHANNEL_ID, CLAUDE_MODEL, OPENAI_MODEL, MAX_BOT_TURNS,
            HISTORY_LIMIT, OWNER_SLACK_USER_ID
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from threading import Lock

from anthropic import Anthropic
from openai import OpenAI
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from prompts import CLAUDE_SYSTEM, CHATTY_SYSTEM, ESCALATION_TEXT
from routing import decide, strip_prefix, Target

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("relay")

# --- Konfiguration -----------------------------------------------------------

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]
RELAY_CHANNEL_ID = os.environ.get("RELAY_CHANNEL_ID")  # leer = alle Kanäle, in denen der Bot ist
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
# Chattys Empfehlung (30.10.2026): gpt-5.6-luna mit reasoning effort "low",
# gpt-5 gilt als deprecated. Bei Qualitätsbedarf gpt-6-astra.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "low")
MAX_OUTPUT_TOKENS = int(os.environ.get("MAX_OUTPUT_TOKENS", "800"))
MAX_BOT_TURNS = int(os.environ.get("MAX_BOT_TURNS", "6"))
HISTORY_LIMIT = int(os.environ.get("HISTORY_LIMIT", "20"))
OWNER_SLACK_USER_ID = os.environ.get("OWNER_SLACK_USER_ID", "")
STATE_FILE = os.environ.get("RELAY_STATE_FILE", "/tmp/relay_state.json")

CLAUDE_TAG = "🟠 *Claude*"
CHATTY_TAG = "🟢 *Chatty*"

anthropic_client = Anthropic()
openai_client = OpenAI()
# token_verification_enabled=False: Token wird nicht beim Start, sondern bei
# der ersten echten Anfrage geprüft. Erlaubt Import-Tests ohne Live-Token.
app = App(token=SLACK_BOT_TOKEN, token_verification_enabled=False)

# Eigene Bot-User-ID, wird beim Start per auth.test geholt. Damit erkennen wir
# eigene Nachrichten sicher über die ID, nicht nur über den Textpräfix.
_own_bot_user_id: str | None = None

# Zähler für Bot-zu-Bot-Runden pro Thread, damit die beiden nicht endlos
# miteinander reden. Wird zurückgesetzt, sobald ein Mensch im Thread schreibt.
# Wird in STATE_FILE gesichert, damit ein Neustart den Zähler nicht löscht.
_bot_turns: dict[str, int] = defaultdict(int)
# Bereits verarbeitete Slack-Event-IDs (Slack liefert Events bei Retries doppelt)
_seen_events: dict[str, float] = {}
_SEEN_TTL = 3600
_lock = Lock()


def _load_state() -> None:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        _bot_turns.update({k: int(v) for k, v in data.get("bot_turns", {}).items()})
        log.info("Zustand geladen: %d Threads", len(_bot_turns))
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        log.warning("Zustand konnte nicht geladen werden: %s", e)


def _save_state() -> None:
    try:
        # nur Threads mit Zähler > 0 sichern, Datei bleibt klein
        data = {"bot_turns": {k: v for k, v in _bot_turns.items() if v > 0}}
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:  # noqa: BLE001
        log.warning("Zustand konnte nicht gesichert werden: %s", e)


def _already_seen(event_id: str | None) -> bool:
    """True, wenn dieses Event schon verarbeitet wurde (Slack-Retry)."""
    if not event_id:
        return False
    now = time.time()
    # alte Einträge wegräumen
    for k in [k for k, t in _seen_events.items() if now - t > _SEEN_TTL]:
        _seen_events.pop(k, None)
    if event_id in _seen_events:
        return True
    _seen_events[event_id] = now
    return False


# --- Hilfsfunktionen ---------------------------------------------------------

def _thread_key(channel: str, thread_ts: str) -> str:
    return f"{channel}:{thread_ts}"


def _is_from_relay(msg: dict) -> bool:
    """Nachricht stammt von diesem Bot (egal ob Claude- oder Chatty-Teil).

    Primär über die eigene Bot-User-ID, Fallback über bot_id oder Textpräfix.
    """
    if _own_bot_user_id and msg.get("user") == _own_bot_user_id:
        return True
    text = msg.get("text", "")
    return msg.get("bot_id") is not None or text.startswith(CLAUDE_TAG) or text.startswith(CHATTY_TAG)


def _fetch_thread(client, channel: str, thread_ts: str) -> list[dict]:
    """Holt den Thread-Verlauf (ältester zuerst) als Rollen-Liste für die Modelle."""
    try:
        res = client.conversations_replies(channel=channel, ts=thread_ts, limit=HISTORY_LIMIT)
        messages = res.get("messages", [])
    except Exception as e:  # noqa: BLE001
        log.warning("Thread konnte nicht geladen werden: %s", e)
        return []

    history: list[dict] = []
    for m in messages:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        if text.startswith(CLAUDE_TAG):
            speaker = "Claude"
            text = text[len(CLAUDE_TAG):].strip()
        elif text.startswith(CHATTY_TAG):
            speaker = "Chatty"
            text = text[len(CHATTY_TAG):].strip()
        else:
            speaker = "Elias"
        history.append({"speaker": speaker, "text": text})
    return history


def _history_as_transcript(history: list[dict]) -> str:
    return "\n\n".join(f"{h['speaker']}: {h['text']}" for h in history)


def _clip(text: str, limit: int = 3900) -> str:
    return text if len(text) <= limit else text[: limit - 20] + "\n\n[… gekürzt]"


# --- Modell-Aufrufe ----------------------------------------------------------

def ask_claude(history: list[dict], prompt: str) -> str:
    transcript = _history_as_transcript(history)
    user_content = (
        f"Bisheriger Thread:\n\n{transcript}\n\n---\n\nNeue Nachricht an dich:\n{prompt}"
        if transcript else prompt
    )
    try:
        res = anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=CLAUDE_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        return "".join(block.text for block in res.content if getattr(block, "type", "") == "text").strip()
    except Exception as e:  # noqa: BLE001
        log.exception("Anthropic-Fehler")
        return f"(Claude konnte nicht antworten: {type(e).__name__})"


def ask_chatty(history: list[dict], prompt: str) -> str:
    transcript = _history_as_transcript(history)
    user_content = (
        f"Bisheriger Thread:\n\n{transcript}\n\n---\n\nNeue Nachricht an dich:\n{prompt}"
        if transcript else prompt
    )
    try:
        kwargs = dict(
            model=OPENAI_MODEL,
            instructions=CHATTY_SYSTEM,
            input=user_content,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        if OPENAI_REASONING_EFFORT:
            kwargs["reasoning"] = {"effort": OPENAI_REASONING_EFFORT}
        try:
            res = openai_client.responses.create(**kwargs)
        except TypeError:
            # ältere SDK ohne reasoning-Parameter
            kwargs.pop("reasoning", None)
            res = openai_client.responses.create(**kwargs)
        return (res.output_text or "").strip()
    except Exception as e:  # noqa: BLE001
        log.exception("OpenAI-Fehler")
        return f"(Chatty konnte nicht antworten: {type(e).__name__})"


# --- Slack-Handler -----------------------------------------------------------

@app.event("message")
def on_message(event: dict, client, logger, body: dict | None = None):  # noqa: D401
    # Nur echte neue Nachrichten, keine Edits/Deletes/Joins
    if event.get("subtype") not in (None, "bot_message"):
        return

    # Slack-Retries abfangen: gleiche event_id nie zweimal verarbeiten
    event_id = (body or {}).get("event_id") or event.get("event_ts")
    with _lock:
        if _already_seen(event_id):
            log.info("Event %s bereits verarbeitet, überspringe.", event_id)
            return

    channel = event.get("channel", "")
    if RELAY_CHANNEL_ID and channel != RELAY_CHANNEL_ID:
        return

    text = (event.get("text") or "").strip()
    if not text:
        return

    thread_ts = event.get("thread_ts") or event.get("ts")
    key = _thread_key(channel, thread_ts)
    from_relay = _is_from_relay(event)

    targets = decide(text, from_relay=from_relay)
    if not targets:
        return

    # Loop-Schutz: Bot-zu-Bot-Runden zählen, Mensch setzt den Zähler zurück
    with _lock:
        if from_relay:
            _bot_turns[key] += 1
            if _bot_turns[key] > MAX_BOT_TURNS:
                log.info("Max. Bot-Runden erreicht in %s, eskaliere.", key)
                mention = f"<@{OWNER_SLACK_USER_ID}> " if OWNER_SLACK_USER_ID else ""
                client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts,
                    text=f"{mention}{ESCALATION_TEXT}",
                )
                _bot_turns[key] = 0
                _save_state()
                return
        else:
            _bot_turns[key] = 0
        _save_state()

    history = _fetch_thread(client, channel, thread_ts)
    # Die auslösende Nachricht selbst nicht doppelt in den Kontext geben
    if history and history[-1]["text"] == text.replace(CLAUDE_TAG, "").replace(CHATTY_TAG, "").strip():
        history = history[:-1]

    prompt = strip_prefix(text)

    for target in targets:
        if target is Target.CLAUDE:
            answer = ask_claude(history, prompt)
            tag = CLAUDE_TAG
        else:
            answer = ask_chatty(history, prompt)
            tag = CHATTY_TAG

        if not answer:
            answer = "(leere Antwort)"
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f"{tag}\n{_clip(answer)}",
        )
        # Kurze Pause, damit die Antworten in der richtigen Reihenfolge ankommen
        time.sleep(0.4)


if __name__ == "__main__":
    import viewer

    log.info("Relay startet. Kanal: %s | Claude: %s | Chatty: %s | max Bot-Runden: %d",
             RELAY_CHANNEL_ID or "alle", CLAUDE_MODEL, OPENAI_MODEL, MAX_BOT_TURNS)
    try:
        _own_bot_user_id = app.client.auth_test().get("user_id")
        log.info("Eigene Bot-User-ID: %s", _own_bot_user_id)
    except Exception as e:  # noqa: BLE001
        log.warning("auth.test fehlgeschlagen, erkenne eigene Nachrichten nur per Präfix: %s", e)
    _load_state()
    viewer.configure(app.client, RELAY_CHANNEL_ID)
    viewer.start_in_background()
    log.info("Webansicht läuft auf Port %d", viewer.PORT)
    SocketModeHandler(app, SLACK_APP_TOKEN).start()
