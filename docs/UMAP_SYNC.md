# Optional uMap Synchronization

The optional uMap integration synchronizes approved, non-blocked reports to one
configured uMap layer. It preserves unrelated features, identifies managed
features by report UUID, and defaults to a dry-run without upload.

uMap does not document the layer update request as a stable public write API.
The integration therefore keeps all endpoint-specific behavior in one client,
checks the target before every run, uses the layer conflict version, and
verifies every successful upload with a fresh download. Test a new uMap version
or instance with `umap check` and a dry-run before granting write access.

## 1. Configure a dedicated target

Create a separate uMap map and layer for the deployment. Do not reuse the IDs or
session data of another bot installation. Add an `umap` object to the local bot
configuration:

```json
"umap": {
  "enabled": true,
  "base_url": "https://umap.example.org",
  "map_url": "https://umap.example.org/de/map/example_123/",
  "map_id": "123",
  "layer_id": "00000000-0000-0000-0000-000000000000",
  "language": "de",
  "source_id": "my-report-project",
  "publish_photos": false,
  "category_styles": {
    "category_one": {
      "marker_color": "#3366cc",
      "marker_label": "C1"
    }
  }
}
```

`source_id` separates features owned by this bot from manually maintained or
foreign features. Choose it once and do not change it after the first upload;
changing it would make existing markers appear foreign and create duplicates.

Category styles are optional. Without a style, the target layer's normal uMap
style applies. The synchronizer does not change clustering or other layer
settings.

Photos are off by default. If `publish_photos` is enabled, every available
photo belonging to an approved report is made public as a small, EXIF-free JPEG
embedded in the layer. The current bot has no separate per-report uMap photo
approval, so enable this only when that publication policy is intended.

## 2. Configure local paths and write protection

Add these values to the private environment file:

```dotenv
UMAP_ALLOW_WRITE=false
UMAP_SESSION_FILE=.local/data/umap_session.enc
UMAP_BACKUP_DIR=.local/data/umap_backups
UMAP_USER_AGENT=TelegramGeoReportBot-uMap/1.0
```

The session and layer backups are encrypted with the existing Fernet key. They
must remain outside Git and should be readable only by the service account.

## 3. Store an authenticated session

Log in to the configured uMap instance in a browser account that can edit only
the intended map or layer. Obtain the `sessionid` and `csrftoken` values from
that browser session and run:

```powershell
.\.venv\Scripts\python -m telegram_geo_report_bot `
  --env-file .env.local umap session
```

The command prompts without echoing the cookie values, verifies the login, and
only then writes the encrypted session file. It never stores the cookies in the
JSON configuration or shell history. A session can expire; in that case the
check and synchronization commands stop safely until the session is replaced.

## 4. Check and preview

First verify the login and configured target without changing it:

```powershell
.\.venv\Scripts\python -m telegram_geo_report_bot `
  --env-file .env.local umap check
```

Then calculate the merge as a dry-run:

```powershell
.\.venv\Scripts\python -m telegram_geo_report_bot `
  --env-file .env.local umap sync
```

Review the counts for new, changed, removed, unchanged, and orphaned features.
An orphan is a feature owned by the configured `source_id` whose UUID no longer
exists locally. Orphans are preserved by design to prevent an empty or wrong
data profile from deleting published content.

## 5. Apply an update

Writing requires both the explicit command flag and the environment gate:

```dotenv
UMAP_ALLOW_WRITE=true
```

```powershell
.\.venv\Scripts\python -m telegram_geo_report_bot `
  --env-file .env.local umap sync --apply
```

Immediately before an upload, the current layer is saved as a Fernet-encrypted
backup. The upload uses uMap's conflict reference and is followed by another
download and merge verification. A timeout during upload reports an unknown
server state and is never retried blindly; rerun the command to start with a
fresh download.

## 6. Linux systemd timer

The example units are in `deploy/systemd/`. Review and adapt their user,
working directory, environment file, and writable data directory before
installation. The example expects:

```text
/opt/telegram-geo-report-bot
/etc/telegram-geo-report-bot.env
/var/lib/telegram-geo-report-bot
```

The private environment should point `BOT_DATA_DIR`, `BOT_PHOTO_DIR`,
`UMAP_SESSION_FILE`, and `UMAP_BACKUP_DIR` into the writable data directory.
Test the exact service command manually before enabling the timer.

```bash
sudo cp deploy/systemd/telegram-geo-report-umap.service /etc/systemd/system/
sudo cp deploy/systemd/telegram-geo-report-umap.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start telegram-geo-report-umap.service
sudo journalctl -u telegram-geo-report-umap.service -n 100 --no-pager
sudo systemctl enable --now telegram-geo-report-umap.timer
systemctl list-timers telegram-geo-report-umap.timer
```

The example timer runs daily around 18:00 Europe/Berlin and is persistent. Edit
`OnCalendar` if another schedule or timezone is required. Installing the files
does not enable the timer automatically.
