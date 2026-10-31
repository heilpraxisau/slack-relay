"""Tests für routing.py, laufen ohne Slack und ohne API-Keys: python -I test_routing.py"""

from routing import Target, decide, strip_prefix

C, G = Target.CLAUDE, Target.CHATTY


def check(name, got, want):
    status = "OK " if got == want else "FAIL"
    print(f"[{status}] {name}: {got!r}")
    assert got == want, f"{name}: {got!r} != {want!r}"


# --- Mensch spricht an ------------------------------------------------------
check("Mensch -> Claude", decide("@claude wie geht das?", from_relay=False), [C])
check("Mensch -> Claude (Doppelpunkt)", decide("Claude: hilf mal", from_relay=False), [C])
check("Mensch -> Chatty", decide("@chatty was meinst du?", from_relay=False), [G])
check("Mensch -> Chatty (gpt)", decide("gpt, deine Meinung", from_relay=False), [G])
check("Mensch -> beide", decide("@beide kurze Einschätzung", from_relay=False), [C, G])
check("Mensch ohne Anrede", decide("ich bin dann mal weg", from_relay=False), [])
check("Slack-Mention entfernt", decide("<@U123> @claude test", from_relay=False), [C])

# --- Bot spricht Bot an ------------------------------------------------------
check("Claude übergibt an Chatty", decide("🟠 *Claude*\nIch sehe das so … @chatty", from_relay=True), [G])
check("Chatty übergibt an Claude", decide("🟢 *Chatty*\nPasst. Einwände? @claude", from_relay=True), [C])
check("Bot ohne Übergabe", decide("🟠 *Claude*\nFertig, hier die Antwort.", from_relay=True), [])
check("Bot erwähnt Partner mittendrin", decide("🟠 *Claude*\n@chatty hat recht, fertig.", from_relay=True), [])
check("Fehlertext löst nichts aus", decide("🟠 *Claude*\n(Claude konnte nicht antworten: Timeout) @chatty", from_relay=True), [])

check("Übergabe trotz Connector-Footer", decide("🟢 *Chatty*\nHallo, Testfrage? @claude *Gesendet mit* Claude", from_relay=True), [C])
check("Footer wird entfernt", strip_prefix("@claude hi *Gesendet mit* Claude"), "hi")

# --- Präfix entfernen ------------------------------------------------------
check("strip Claude", strip_prefix("@claude wie geht das?"), "wie geht das?")
check("strip beide", strip_prefix("beide: kurze Einschätzung"), "kurze Einschätzung")
check("strip Tag", strip_prefix("🟠 *Claude*\nText hier"), "Text hier")

print("\nAlle Routing-Tests bestanden.")
