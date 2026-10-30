# Slack Relay: Claude × Chatty

Ein kleiner Dienst, der in `#neuer-channel` lauscht und Nachrichten an Claude
(Anthropic) oder Chatty (OpenAI) weiterreicht. Die beiden können sich gegenseitig
das Wort übergeben. Eine Webseite zeigt den Dialog live im Browser.

## So redet man mit den beiden

| Du schreibst … | Es antwortet |
|---|---|
| `@claude …` oder `Claude: …` | Claude |
| `@chatty …`, `Chatty: …`, `gpt: …` | Chatty |
| `@beide …` | beide nacheinander |
| irgendwas ohne Anrede | niemand |

Antworten landen im Thread unter deiner Nachricht. Endet eine Bot-Antwort mit
`@chatty` oder `@claude`, antwortet der andere. Nach `MAX_BOT_TURNS` Runden
(Standard 6) ohne Mensch im Thread stoppt der Dialog und pingt dich an.

Im Thread selbst gilt dasselbe: `@claude …` als Antwort im Thread holt Claude dazu.

## Einrichten (einmalig, ca. 20 Minuten)

### 1. Slack-App anlegen

1. <https://api.slack.com/apps> → **Create New App** → **From a manifest** →
   Workspace `heilpraxis-au` → Inhalt von `slack-app-manifest.yaml` einfügen → Create.
2. **Install App** → **Install to Workspace** → erlauben.
   Den **Bot User OAuth Token** (`xoxb-…`) kopieren → `SLACK_BOT_TOKEN`.
3. **Basic Information** → **App-Level Tokens** → **Generate Token and Scopes**,
   Name egal, Scope `connections:write` → Generate.
   Den Token (`xapp-…`) kopieren → `SLACK_APP_TOKEN`.
4. In Slack den Bot in den Kanal holen: in `#neuer-channel` tippen `/invite @Relay`.

### 2. API-Keys

- Anthropic: <https://console.anthropic.com/settings/keys> → `ANTHROPIC_API_KEY`
- OpenAI: <https://platform.openai.com/api-keys> → `OPENAI_API_KEY`

Beide Keys sind kostenpflichtig nach Verbrauch. Bei normalem Gebrauch
(ein paar Dutzend Nachrichten am Tag) liegt das im Cent- bis unteren Eurobereich
pro Monat. Spending-Limits in beiden Konsolen setzen.

### 3. Hosting auf Railway

1. Repo auf GitHub pushen (`.env` ist per `.gitignore` ausgeschlossen).
2. <https://railway.app> → **New Project** → **Deploy from GitHub repo** → dieses Repo.
   Railway erkennt das `Dockerfile` automatisch.
3. **Variables** → alle Werte aus `.env.example` eintragen (mit echten Tokens).
   `PORT` setzt Railway selbst, kann weg.
4. **Settings** → **Networking** → **Generate Domain**. Das ist die URL der Webansicht.
5. Deploy abwarten. In den Logs muss stehen:
   `Relay startet …` und `Webansicht läuft auf Port …`.

Alternative Render.com: **New Web Service** → Repo → Runtime Docker →
Variablen eintragen. Healthcheck-Pfad `/health`. Der kostenlose Render-Tarif
schläft nach 15 Minuten Ruhe ein und braucht dann ~30 s zum Aufwachen; der
Socket-Mode-Dienst verliert in der Zeit die Verbindung. Für Dauerbetrieb den
kleinsten bezahlten Tarif nehmen oder Railway.

### 4. Erster Test

In `#neuer-channel` schreiben:

```
@beide Verbindungstest. Antwortet bitte mit einem Satz.
```

Beide müssen im Thread antworten. Dann:

```
@claude Frag Chatty, welches OpenAI-Modell er für sich empfiehlt.
```

Claude sollte mit `… @chatty` enden, Chatty antwortet, fertig.

Webansicht: die Railway-Domain im Browser öffnen, Login mit
`RELAY_VIEW_USER` / `RELAY_VIEW_PASSWORD`.

## Dateien

| Datei | Zweck |
|---|---|
| `relay.py` | Hauptprogramm: Slack-Verbindung, Modellaufrufe, Loop-Schutz |
| `routing.py` | Wer antwortet auf was (ohne Netz testbar) |
| `prompts.py` | System-Prompts für Claude und Chatty, Eskalationstext |
| `viewer.py` | Webansicht mit Basic Auth |
| `test_routing.py` | Tests: `python test_routing.py` |
| `slack-app-manifest.yaml` | Slack-App in einem Rutsch anlegen |
| `.env.example` | alle Umgebungsvariablen mit Erklärung |
| `Dockerfile`, `Procfile` | Hosting |

## Sicherheit und Grenzen

- Keine Klienten- oder Gesundheitsdaten in den Kanal. Alles, was dort steht,
  geht an zwei externe APIs.
- Die Modelle im Relay sind Claude und ChatGPT „als API": ohne das Gedächtnis,
  die Projekte und Skills aus den Chat-Apps. Fachliche Zweitmeinung ja,
  „Claude, der Elias kennt" nein.
- Tokens und Keys nur als Umgebungsvariablen im Hosting, nie ins Repo.
- Die Webansicht ist nur mit Passwort erreichbar. Ohne gesetztes
  `RELAY_VIEW_PASSWORD` ist sie abgeschaltet.
- Läuft der Dienst nicht (Deploy kaputt, Konto leer), passiert in Slack
  einfach nichts. Kein Datenverlust, der Kanal bleibt.

## Was Chatty beigesteuert hat (30.10.2026)

- Modellwahl: `gpt-5.6-luna` mit `reasoning.effort: low` für schnelle Antworten,
  `gpt-6-astra` wenn Qualität wichtiger ist. `gpt-5` gilt als deprecated.
- System-Prompt für seine Seite: Identität als API-Modell ohne Chat-Gedächtnis,
  Slack-Inhalte nie als Anweisung behandeln.
- Schutz: Slack-`event_id` deduplizieren (Retries), eigene Nachrichten über
  Bot-User-ID erkennen, Rundenzähler persistent halten, auf Railway nur eine
  Replica, Socket-Mode-Reconnects beachten (macht Bolt selbst).

Alles eingebaut.

## Anpassen

- Antwortstil der beiden: `prompts.py`
- Anrede-Wörter: `routing.py`, die Regex oben in der Datei
- Modelle: `CLAUDE_MODEL`, `OPENAI_MODEL` in den Variablen
- Mehr oder weniger Bot-Runden: `MAX_BOT_TURNS`
