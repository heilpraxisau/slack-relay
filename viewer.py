"""
Mini-Webansicht: zeigt die letzten Nachrichten des Relay-Kanals im Browser.

Läuft im selben Prozess wie das Relay (eigener Thread), damit nur ein Dienst
deployt werden muss. Geschützt per HTTP Basic Auth (RELAY_VIEW_USER /
RELAY_VIEW_PASSWORD). Ohne gesetztes Passwort ist die Seite deaktiviert.

Routen:
  GET /            -> HTML-Seite, lädt sich alle 10 s neu
  GET /api/messages -> JSON der letzten Nachrichten (vom Slack-Kanal gelesen)
  GET /health      -> 200 für Hosting-Healthchecks (ohne Auth)
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

VIEW_USER = os.environ.get("RELAY_VIEW_USER", "")
VIEW_PASSWORD = os.environ.get("RELAY_VIEW_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8080"))
TZ = ZoneInfo(os.environ.get("RELAY_TZ", "Europe/Berlin"))

_slack_client = None
_channel_id = None
_cache: dict = {"ts": 0.0, "data": []}
_CACHE_SECONDS = 8

_SLACK_LINK = re.compile(r"<(https?://[^|>]+)(?:\|([^>]+))?>")
_SLACK_USER = re.compile(r"<@([A-Z0-9]+)>")


def configure(slack_client, channel_id: str | None):
    global _slack_client, _channel_id
    _slack_client = slack_client
    _channel_id = channel_id


def _speaker(text: str) -> tuple[str, str]:
    if text.startswith("🟠 *Claude*"):
        return "claude", text[len("🟠 *Claude*"):].strip()
    if text.startswith("🟢 *Chatty*"):
        return "chatty", text[len("🟢 *Chatty*"):].strip()
    return "human", text


def _render_text(text: str) -> str:
    t = html.escape(text)
    t = _SLACK_LINK.sub(lambda m: f'<a href="{m.group(1)}" target="_blank">{m.group(2) or m.group(1)}</a>', t)
    t = _SLACK_USER.sub(r"@\1", t)
    t = re.sub(r"\*(.+?)\*", r"<b>\1</b>", t)
    t = re.sub(r"_(.+?)_", r"<i>\1</i>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    return t.replace("\n", "<br>")


def _fetch_messages(limit: int = 60) -> list[dict]:
    now = time.time()
    if now - _cache["ts"] < _CACHE_SECONDS:
        return _cache["data"]
    if not _slack_client or not _channel_id:
        return []
    try:
        res = _slack_client.conversations_history(channel=_channel_id, limit=limit)
        msgs = res.get("messages", [])
    except Exception:  # noqa: BLE001
        return _cache["data"]

    def to_item(m: dict, depth: int) -> dict | None:
        text = (m.get("text") or "").strip()
        if not text or m.get("subtype") not in (None, "bot_message", "thread_broadcast"):
            return None
        who, body = _speaker(text)
        ts = float(m.get("ts", "0"))
        return {
            "who": who,
            "text": body,
            "time": datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(TZ).strftime("%d.%m. %H:%M"),
            "thread": m.get("thread_ts") or m.get("ts"),
            "depth": depth,
        }

    out: list[dict] = []
    thread_fetches = 0
    for m in reversed(msgs):  # älteste zuerst
        item = to_item(m, 0)
        if not item:
            continue
        out.append(item)
        # Thread-Antworten nachladen (dort findet der Bot-Dialog statt)
        if m.get("reply_count", 0) and thread_fetches < 15:
            thread_fetches += 1
            try:
                rep = _slack_client.conversations_replies(channel=_channel_id, ts=m["ts"], limit=50)
                for r in rep.get("messages", [])[1:]:  # [0] ist die Elternnachricht
                    ri = to_item(r, 1)
                    if ri:
                        out.append(ri)
            except Exception:  # noqa: BLE001
                pass
    _cache.update(ts=now, data=out)
    return out


_PAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>Claude × Chatty</title>
<style>
  :root{--bg:#0f1115;--card:#171a21;--txt:#e8e8ea;--mut:#8b8f98;--claude:#e8853a;--chatty:#3fb57a;--human:#6f8cff}
  @media (prefers-color-scheme: light){:root{--bg:#f6f6f7;--card:#fff;--txt:#17181c;--mut:#6b6f78}}
  body{margin:0;background:var(--bg);color:var(--txt);font:16px/1.5 -apple-system,system-ui,sans-serif}
  header{position:sticky;top:0;background:var(--bg);padding:14px 16px;border-bottom:1px solid #2a2d35;display:flex;justify-content:space-between;align-items:baseline}
  header h1{margin:0;font-size:17px;font-weight:600}
  header span{color:var(--mut);font-size:13px}
  main{max-width:820px;margin:0 auto;padding:16px}
  .msg{background:var(--card);border-radius:12px;padding:12px 14px;margin:10px 0;border-left:4px solid var(--human)}
  .msg.claude{border-left-color:var(--claude)} .msg.chatty{border-left-color:var(--chatty)}
  .meta{font-size:12px;color:var(--mut);margin-bottom:4px;display:flex;gap:10px}
  .meta b{color:var(--txt)} .claude .meta b{color:var(--claude)} .chatty .meta b{color:var(--chatty)}
  code{background:#00000022;padding:1px 5px;border-radius:4px}
  a{color:inherit}
  .empty{color:var(--mut);text-align:center;padding:40px}
</style></head><body>
<header><h1>Claude × Chatty</h1><span>#neuer-channel · aktualisiert alle 10 s · %TIME%</span></header>
<main>%BODY%</main>
</body></html>"""


def _render_page() -> str:
    msgs = _fetch_messages()
    if not msgs:
        body = '<div class="empty">Noch keine Nachrichten, oder Kanal nicht erreichbar.</div>'
    else:
        parts = []
        for m in msgs:
            name = {"claude": "Claude", "chatty": "Chatty", "human": "Elias"}[m["who"]]
            cls = f'msg {m["who"]}' + (" reply" if m.get("depth") else "")
            parts.append(
                f'<div class="{cls}"><div class="meta"><b>{name}</b><span>{m["time"]}</span></div>'
                f'<div>{_render_text(m["text"])}</div></div>'
            )
        body = "\n".join(parts)
    return _PAGE.replace("%BODY%", body).replace("%TIME%", datetime.now(TZ).strftime("%H:%M:%S"))


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):  # leise
        pass

    def _authed(self) -> bool:
        if not VIEW_PASSWORD:
            return False
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Basic "):
            return False
        try:
            user, pw = base64.b64decode(hdr[6:]).decode().split(":", 1)
        except Exception:  # noqa: BLE001
            return False
        return user == VIEW_USER and pw == VIEW_PASSWORD

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._send(200, b"ok", "text/plain")
        if not VIEW_PASSWORD:
            return self._send(503, "Ansicht deaktiviert: RELAY_VIEW_PASSWORD fehlt.".encode(), "text/plain; charset=utf-8")
        if not self._authed():
            return self._send(401, b"Login", "text/plain", {"WWW-Authenticate": 'Basic realm="relay"'})
        if self.path.startswith("/api/messages"):
            return self._send(200, json.dumps(_fetch_messages(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
        return self._send(200, _render_page().encode(), "text/html; charset=utf-8")


def start_in_background():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="viewer")
    t.start()
    return srv
