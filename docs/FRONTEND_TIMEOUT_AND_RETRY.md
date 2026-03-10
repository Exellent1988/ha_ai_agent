# Frontend: Timeout und Retry

Das Backend unterstützt jetzt einen konfigurierbaren **Request-Timeout** (Einstellungen → Integration → AI Agent HA → Optionen: "Request timeout (seconds)", 30–900 s, Standard 300 s).

Damit lokale Modelle nicht vorzeitig abgebrochen werden, sollten im Frontend folgende Anpassungen vorgenommen werden:

## 1. Längerer Timeout im Frontend (optional)

Im Panel (`ai_agent_ha-panel.js`) wird der Loading-Zustand aktuell nach 60 Sekunden abgebrochen. Empfehlung: diesen Wert auf **300 Sekunden (5 Minuten)** erhöhen, damit er zum Backend-Standard passt und lokale Modelle genug Zeit haben.

- Suche nach `60000` (60 s) bzw. dem `setTimeout` für den Service-Call-Timeout.
- Ersetze durch `300000` (5 Min) oder nutze eine Konstante, z. B.:
  - `const FRONTEND_REQUEST_TIMEOUT_MS = 300000; // 5 min, should be >= backend request_timeout`
- In der Timeout-Meldung z. B. anzeigen: "Request timed out. You can increase the timeout in the integration options or try again (Retry)."

## 2. Retry-Button bei Fehler/Timeout

Wenn ein Fehler angezeigt wird (`_error` gesetzt) oder die Anfrage wegen Timeout abgebrochen wurde, soll der Nutzer die **letzte Anfrage erneut senden** können.

- **Letzte Nachricht speichern:** Beim Senden der Nutzer-Nachricht (`_sendMessage` / Submit) den zuletzt gesendeten Prompt speichern, z. B. `this._lastSentPrompt = prompt;`.
- **Retry-Button anzeigen:** Wenn `this._error` gesetzt ist, unter der Fehlermeldung einen Button "Erneut versuchen" / "Retry" anzeigen.
- **Retry-Handler:** Beim Klick auf Retry:
  - `this._error = null;`
  - Falls `this._lastSentPrompt` gesetzt ist: dieselbe Logik wie beim normalen Senden ausführen (Service `query` mit `this._lastSentPrompt` aufrufen, Loading-State setzen, Timeout-Timer starten).
  - Optional: die letzte Nutzer-Nachricht aus `_messages` entfernen (die, die zur fehlgeschlagenen Anfrage gehörte), damit nach erfolgreichem Retry keine doppelte Nutzer-Nachricht erscheint – je nach gewünschtem UX.

## Kurzüberblick

| Änderung | Wo | Was |
|----------|-----|-----|
| Timeout erhöhen | `setTimeout` für Service-Call-Timeout | 60000 → 300000 (oder Konstante) |
| Last prompt | Beim Senden | `_lastSentPrompt = prompt` |
| Retry-Button | UI bei `_error` | Button "Erneut versuchen" |
| Retry-Aktion | On-Click | `_error = null`, erneuter Aufruf mit `_lastSentPrompt` |

Das Backend verwendet den in den Integrations-Optionen eingestellten **Request timeout** für alle AI-API-Aufrufe; lokale Modelle können dort z. B. auf 600 s gestellt werden.
