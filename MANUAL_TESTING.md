# Manueller Test unter Windows und VS Code

Diese Anleitung prüft den tatsächlich auf GitHub gespeicherten Stand in einem
frischen Ordner. Verwende ausschließlich einen Testbot-Token. Ein Telegram-Token
kann immer nur von einem laufenden Long-Polling-Prozess verwendet werden.

## 1. Privates Repository per SSH klonen

### SSH-Zugang einmalig einrichten

Prüfe in PowerShell, ob bereits ein öffentlicher Schlüssel vorhanden ist:

```powershell
Get-ChildItem $HOME\.ssh\*.pub
```

Falls kein Schlüssel vorhanden ist, erstelle einen geschützten Ed25519-Schlüssel:

```powershell
ssh-keygen -t ed25519 -C "340260116+eimer318@users.noreply.github.com"
```

Vergib dabei möglichst eine Passphrase. Kopiere anschließend ausschließlich den
öffentlichen Schlüssel:

```powershell
Get-Content $HOME\.ssh\id_ed25519.pub | Set-Clipboard
```

Füge ihn unter **GitHub → Settings → SSH and GPG keys → New SSH key** ein. Teile
niemals die Datei ohne `.pub`. Prüfe danach die Verbindung:

```powershell
ssh -T git@github.com
```

Die erste Hostabfrage darf erst nach Kontrolle des angezeigten GitHub-Fingerprints
bestätigt werden. Danach klonst du das private Repository:

```powershell
cd C:\Code
git clone git@github.com:eimer318/telegram-geo-report-bot.git
cd telegram-geo-report-bot
code .
```

## 2. Frische Entwicklungsumgebung erstellen

Öffne in VS Code **Terminal → New Terminal** und verwende PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env.local
Copy-Item config\bot.example.json config\bot.local.json
.\.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Trage in `.env.local` den Testtoken und den neu erzeugten Fernet-Schlüssel ein:

```dotenv
BOT_TOKEN=TESTBOT_TOKEN
ENCRYPTION_KEY=NEU_ERZEUGTER_FERNET_SCHLUESSEL
BOT_CONFIG_FILE=config/bot.local.json
BOT_DATA_DIR=.local/data
BOT_PHOTO_DIR=.local/photos
NOMINATIM_USER_AGENT=TelegramGeoReportBot/1.0 (deine-kontaktadresse@example.org)
```

`.env.local`, `config/bot.local.json` und `.local/` werden von Git ignoriert.
Prüfe trotzdem vor jedem Commit mit `git status`, dass sie nicht vorgemerkt sind.

## 3. Kategorien und Suche in JSON anpassen

Bearbeite `config/bot.local.json`, nicht den erzeugten Datenexport. Relevante
Bereiche sind:

- `welcome_text`, `help_text` und `contact_text`;
- `public_result_url`;
- `amount_options`;
- `categories` mit Kategorien und Unterkategorien;
- `geocoding` für Deutschland, Bundesländer, Landkreise oder Gemeinden.

Beispiel für eine eigene Kategorie:

```json
{
  "id": "street_furniture",
  "label": "Straßenmobiliar",
  "subcategories": [
    {"id": "bench", "label": "Sitzbank"},
    {"id": "bicycle_rack", "label": "Fahrradständer"}
  ]
}
```

IDs bleiben dauerhaft stabil und verwenden am besten kleine ASCII-Zeichen mit
Unterstrichen. Sichtbare Bezeichnungen können später geändert werden.

Deutschlandweit bleiben die Regionalfilter leer:

```json
"allowed_states": [],
"allowed_counties": [],
"allowed_municipalities": [],
"postcode_prefixes": []
```

Für ein Bundesland genügt beispielsweise:

```json
"allowed_states": ["Bayern"]
```

`entries.json` und `entries.geojson` unter `.local/data/` sind abgeleitete
Exporte. Sie dürfen nicht manuell bearbeitet werden. Neue Testeinträge werden
über Telegram erzeugt; anschließend baut der Exportbefehl beide Dateien neu aus
der verschlüsselten führenden Datei `entries.enc` auf.

## 4. Bot starten und ersten Admin einrichten

Stelle sicher, dass kein anderer Prozess denselben Testtoken verwendet. Starte:

```powershell
.\scripts\run_local.ps1
```

Sende dem Bot in Telegram `/start` und danach `/me`. Notiere die angezeigte
numerische Telegram-ID, beende den Bot im VS-Code-Terminal mit `Strg+C` und füge
dich als ersten Admin hinzu:

```powershell
.\scripts\run_local.ps1 -Command admin -CommandArguments @("add", "DEINE_ID")
.\scripts\run_local.ps1
```

## 5. Manueller Abnahmelauf

Führe die Fälle nacheinander durch:

1. **Start und Navigation:** `/start`, Hilfe, Datenverarbeitung und Zurückwege.
2. **Meldung ohne Foto:** Adresse suchen, Treffer wählen, Kategorie,
   Unterkategorie, Menge und Beschreibung erfassen, Foto überspringen.
3. **Bearbeitung:** In der Prüfung Adresse, Kategorie, Menge und Beschreibung
   jeweils ändern und kontrollieren, dass der Rückweg wieder zur Prüfung führt.
4. **Meldung mit Foto:** Foto hinzufügen, ersetzen und in einem weiteren Lauf
   entfernen. Das Originalfoto darf keine Metadaten im Export erzeugen.
5. **Abbruch:** Einen begonnenen Ablauf auf mehreren Stufen abbrechen; danach
   muss das Hauptmenü wieder bedienbar sein.
6. **Admin-Review:** `/admin` öffnen, drei offene Meldungen erzeugen und
   `1/3`, `2/3`, `3/3` sowie Freigeben, Ablehnen und Überspringen prüfen.
7. **Alle Meldungen:** Vor/Zurück sowie nachträgliche Statuswechsel zwischen
   `pending`, `approved` und `rejected` prüfen.
8. **Adminverwaltung:** Einen zweiten Testadmin hinzufügen und entfernen. Der
   letzte verbleibende Admin darf im Telegram-Menü nicht gelöscht werden.
9. **Neustart:** Bot mit `Strg+C` beenden und erneut starten. Einträge,
   Adminrechte und Status müssen erhalten bleiben.
10. **Freier Text:** Text außerhalb eines aktiven Ablaufs senden; der Bot soll
    verständlich zum Hauptmenü zurückführen.

## 6. Exporte und automatisierte Prüfungen

Nach mindestens einer Freigabe:

```powershell
.\scripts\run_local.ps1 -Command export
Get-Content .local\data\entries.json
Get-Content .local\data\entries.geojson
```

Kontrolliere:

- nur freigegebene Meldungen erscheinen;
- GeoJSON verwendet `[Längengrad, Breitengrad]`;
- keine Telegram-ID, Reporterkennung, Reviewdaten, Sicherheitshinweise oder
  internen Fotodateinamen erscheinen;
- `has_photo` ist nur ein boolescher Hinweis.

Führe abschließend aus:

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python scripts\security_check.py
git status --short
```

Erwartung: alle Tests erfolgreich, keine defekten Abhängigkeiten und keine
ungewollten Dateien im Git-Status. Erst danach sollte über eine öffentliche
Freigabe des Repositorys entschieden werden.
