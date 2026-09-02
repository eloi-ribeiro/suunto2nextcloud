# suunto2nextcloud

Pulls your Suunto (Race 2 or any Suunto-app-synced watch) workouts from the **Suunto Cloud API**
and stores them in a folder of your Nextcloud as **GPX + FIT + JSON**, plus an auto-generated
`Activities.md` index. On Nextcloud the tracks are shown with the **GpxPod** app (maps, elevation /
heart-rate / speed charts, per-folder stats) — no custom server-side app needed, so it works on a
managed instance such as Hetzner Storage Share where you cannot install unpublished apps.

```
Suunto app ──► Suunto Cloud ──(OAuth2, /v2/workouts, exportFit)──► suunto2nextcloud ──(WebDAV)──► Nextcloud/Suunto/
                                                                                                     ├── 2026/2026-08-28_0712_Trail_running.gpx  ← GpxPod
                                                                                                     ├── 2026/…​.fit / .json
                                                                                                     └── Activities.md                          ← Nextcloud Text
```

## 1. Nextcloud side (one-off, as admin in the web UI)

1. **Apps → search "GpxPod" → Enable** (v8.3 supports Nextcloud 33).
2. Optional: **Maps** app also lists GPX tracks; GpxPod has the better charts.
3. **Settings → Security → Devices & sessions → "Create new app password"** for the user that will
   own the files. Put it in `nextcloud.app_password` (never your login password).

## 2. Suunto side

Suunto's official API is a partner programme:

1. Sign up at https://apizone.suunto.com, fill the application form (reviewed weekly; up to ~2 weeks).
2. Once accepted, **subscribe to the Development API** (rate-limited but fine for one user) → the
   `Ocp-Apim-Subscription-Key` is on your profile page.
3. On the profile page edit the **OAuth application**: set a redirect URI. Two options:
   * `http://localhost:8765/callback` — run `auth` on your laptop; a local listener catches the code.
   * any URL you own — `auth --no-browser` prints the login link; after logging in you paste back the
     URL you were redirected to (works when running inside Docker on a server).

Until you have API access you can still use `import-fit` with FIT files exported from the Suunto app
(activity → ⋯ → Export → FIT) — same output layout, same index.

## 3. Run it

```bash
cp config.example.yaml config.yaml   # fill in suunto.* and nextcloud.*
pip install -r requirements.txt
python __main__.py test-nextcloud   # checks the app password + creates the folder
python __main__.py auth             # one-off OAuth; tokens go to state/tokens.json (0600)
python __main__.py sync             # pulls everything since sync.start_date
```

Subsequent `sync` runs only fetch workouts newer than the last run (with a 24 h overlap for late
uploads). `sync --full` re-lists from `start_date` (already-stored workouts are skipped, nothing is
re-uploaded). `rebuild-index` regenerates `Activities.md` from the local state.

### Docker (e.g. on a Hetzner VPS)

```bash
mkdir -p config state && cp config.example.yaml config/config.yaml   # edit it; interval_minutes: 30
docker compose build
docker compose run --rm suunto2nextcloud auth --no-browser   # paste flow
docker compose up -d                                          # loops every interval_minutes
```

Every config key can be overridden with `S2N_<SECTION>_<KEY>` environment variables
(`S2N_SUUNTO_CLIENT_SECRET`, `S2N_NEXTCLOUD_APP_PASSWORD`, …) if you prefer secrets outside the file.

## What gets stored

| File | Content |
|---|---|
| `YYYY/YYYY-MM-DD_HHMM_Sport.gpx` | GPX 1.1 track with Garmin TrackPointExtension (`hr`, `cad`, `atemp`, `speed`) and `power`; skipped for indoor workouts without GPS |
| `….fit` | untouched original from Suunto (set `upload_fit: false` to drop) |
| `….json` | normalised summary: sport, start, distance, time, ascent/descent, avg/max HR, calories, training effect, … |
| `Activities.md` | month-by-month table with totals, pace/speed, links to the files; renders in Nextcloud Text |

Sport name comes from the Suunto activity id when it is a known one, otherwise from the FIT
`sport`/`sub_sport` (so "Trail running", "Mountain biking", "Openwater swimming" are preserved).

## Files

* `suunto.py` – OAuth2 (authorise / token / refresh) + `/v2/workouts`, `/v2/workouts/{key}`, `/v2/workout/exportFit/{key}`
* `fit2gpx.py` – FIT → GPX + summary (fitdecode)
* `nextcloud.py` – WebDAV PUT/MKCOL/PROPFIND with app-password auth
* `index.py` – Markdown index
* `cli.py` (+ `__main__.py` wrapper) – CLI (`auth`, `sync`, `import-fit`, `rebuild-index`, `test-nextcloud`)
* `tests/` – FIT conversion + index tests (`python -m pytest tests`)

## Notes / limits

* Field names of the `/v2/workouts` payload are handled defensively (`workoutKey`/`key`,
  `startTime` in epoch-ms or ISO, `hrdata.workoutAvgHR`, …); the FIT file is the source of truth for
  the summary, the API item only needs to supply the key.
* Suunto also offers webhooks (new-workout notifications); not used here — polling every 30 min is
  enough for one athlete and needs no public endpoint.
* A "real" Nextcloud app (PHP + Vue, own dashboard) would have to go through the Nextcloud App Store
  before Storage Share could install it; this repo is the pragmatic path that works today. The stored
  files are plain GPX/FIT, so nothing is lost if that app is built later.
