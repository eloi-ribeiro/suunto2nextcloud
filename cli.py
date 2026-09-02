"""CLI: suunto2nextcloud {auth|sync|import-fit|rebuild-index|test-nextcloud}"""
import argparse
import hashlib
import datetime as dt
import json
import logging
import re
import sys
import time
from pathlib import Path

__version__ = "0.1.0"
import config
import fit2gpx
import index
from nextcloud import Nextcloud, NextcloudError
from state import State
from suunto import SuuntoClient, SuuntoError, activity_name

log = logging.getLogger("suunto2nextcloud")


def _ms_to_iso(ms):
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000.0, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def _first(d, *keys):
    for k in keys:
        cur = d
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur is not None:
            return cur
    return None


def summary_from_api(w):
    """Normalise a /v2/workouts item. Field names vary a bit across docs, so try several."""
    start = _first(w, "startTime")
    if isinstance(start, (int, float)):
        start = _ms_to_iso(start)
    return {k: v for k, v in {
        "workoutKey": _first(w, "workoutKey", "key"),
        "activityId": _first(w, "activityId"),
        "sport": activity_name(_first(w, "activityId"), _first(w, "activityName", "activityType")),
        "startTime": start,
        "totalTime": _first(w, "totalTime", "duration"),
        "totalDistance": _first(w, "totalDistance"),
        "totalAscent": _first(w, "totalAscent"),
        "totalDescent": _first(w, "totalDescent"),
        "avgHr": _first(w, "hrdata.workoutAvgHR", "hrdata.avg", "avgHr", "avgHeartRate"),
        "maxHr": _first(w, "hrdata.workoutMaxHR", "hrdata.max", "maxHr", "maxHeartRate"),
        "avgSpeed": _first(w, "avgSpeed"),
        "maxSpeed": _first(w, "maxSpeed"),
        "calories": _first(w, "energyConsumption"),
        "stepCount": _first(w, "stepCount"),
        "description": _first(w, "description"),
    }.items() if v is not None}


def _safe(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s or "").strip("_") or "Activity"


def workout_paths(summary):
    """Return (base_path_without_ext) like '2026/2026-08-28_0712_Trail_running'."""
    st = summary.get("startTime") or "1970-01-01T00:00:00Z"
    d = dt.datetime.strptime(st[:19], "%Y-%m-%dT%H:%M:%S")
    base = "%s/%s_%s" % (d.strftime("%Y"), d.strftime("%Y-%m-%d_%H%M"), _safe(summary.get("sport")))
    return base


def store_workout(nc, cfg, fit_bytes, summary, key):
    """Convert + upload one workout; returns the index entry."""
    records, laps, session, sport = fit2gpx.parse_fit(fit_bytes)
    fit_summary = fit2gpx.summary_from_fit(session, sport, records)
    merged = dict(fit_summary)
    merged.update({k: v for k, v in summary.items() if v is not None})
    if not merged.get("startTime"):
        merged["startTime"] = fit_summary.get("startTime")
    base = workout_paths(merged)
    name = "%s %s" % (merged.get("sport", "Activity"), (merged.get("startTime") or "")[:16].replace("T", " "))
    gpx, npts = fit2gpx.to_gpx(records, name, merged.get("startTime"), merged.get("sport"), laps)
    entry = dict(merged)
    entry["workoutKey"] = key
    entry["trackPoints"] = npts
    if npts:
        nc.put(base + ".gpx", gpx.encode("utf-8"), "application/gpx+xml")
        entry["gpx"] = base + ".gpx"
    else:
        log.info("%s has no GPS points; only fit/json stored", base)
    if cfg["nextcloud"]["upload_fit"]:
        nc.put(base + ".fit", fit_bytes, "application/vnd.ant.fit")
        entry["fit"] = base + ".fit"
    if cfg["nextcloud"]["upload_json"]:
        nc.put(base + ".json", json.dumps(entry, indent=2, default=str).encode(), "application/json")
    return entry


def write_index(nc, cfg, state):
    entries = [v for v in state.synced.values() if isinstance(v, dict)]
    md = index.build(entries, cfg["nextcloud"]["folder"], cfg["nextcloud"]["url"])
    nc.put("Activities.md", md.encode("utf-8"), "text/markdown")


def cmd_auth(cfg, state, args):
    config.require(cfg, "suunto", "client_id", "client_secret")
    SuuntoClient(cfg, state).interactive_auth(open_browser=not args.no_browser)


def cmd_test_nextcloud(cfg, state, args):
    config.require(cfg, "nextcloud", "url", "user", "app_password")
    nc = Nextcloud(cfg)
    nc.check()
    nc.mkdirs("")
    print("Nextcloud OK: %s as %s, folder '%s' ready" % (cfg["nextcloud"]["url"], cfg["nextcloud"]["user"], nc.root))


def sync_once(cfg, state, full=False):
    config.require(cfg, "suunto", "client_id", "client_secret", "subscription_key")
    config.require(cfg, "nextcloud", "url", "user", "app_password")
    api = SuuntoClient(cfg, state)
    nc = Nextcloud(cfg)
    nc.check()
    if state.last_sync_ms and not full:
        since_ms = state.last_sync_ms - 24 * 3600 * 1000  # overlap a day for late uploads
    else:
        since_ms = int(dt.datetime.strptime(cfg["sync"]["start_date"], "%Y-%m-%d")
                       .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    now_ms = int(time.time() * 1000)
    new = 0
    for w in api.list_workouts(since_ms=since_ms):
        summary = summary_from_api(w)
        key = summary.get("workoutKey")
        if not key:
            log.warning("workout without key, skipping: %s", json.dumps(w)[:200])
            continue
        if key in state.synced:
            continue
        log.info("fetching %s %s (%s)", summary.get("startTime"), summary.get("sport"), key)
        try:
            fit = api.export_fit(key)
        except SuuntoError as e:
            log.error("FIT export failed for %s: %s", key, e)
            continue
        entry = store_workout(nc, cfg, fit, summary, key)
        state.mark_synced(key, entry)
        new += 1
    state.last_sync_ms = now_ms
    state.save_synced()
    if new:
        write_index(nc, cfg, state)
    log.info("sync done: %d new workout(s), %d total", new, len(state.synced))
    return new


def cmd_sync(cfg, state, args):
    interval = args.interval if args.interval is not None else int(cfg["sync"]["interval_minutes"])
    while True:
        try:
            sync_once(cfg, state, full=args.full)
        except Exception as e:  # keep the daemon alive on transient errors
            log.exception("sync failed: %s", e)
            if not interval:
                return 1
        if not interval:
            return 0
        time.sleep(interval * 60)


def cmd_import_fit(cfg, state, args):
    """Fallback: import FIT files exported manually (Suunto app → Export, or any other source)."""
    config.require(cfg, "nextcloud", "url", "user", "app_password")
    nc = Nextcloud(cfg)
    nc.check()
    files = []
    for p in args.paths:
        p = Path(p)
        files += sorted(p.rglob("*.fit")) if p.is_dir() else [p]
    n = 0
    for f in files:
        data = f.read_bytes()
        key = "file:" + hashlib.sha1(data).hexdigest()[:16]
        if key in state.synced and not args.force:
            continue
        records, laps, session, sport = fit2gpx.parse_fit(data)
        probe = fit2gpx.summary_from_fit(session, sport, records)
        dup = [k for k, v in state.synced.items() if isinstance(v, dict)
               and v.get("startTime") == probe.get("startTime") and v.get("totalTime") == probe.get("totalTime")]
        if dup and not args.force:
            log.info("%s duplicates already-synced %s, skipping", f.name, dup[0])
            continue
        log.info("importing %s", f)
        entry = store_workout(nc, cfg, data, {}, key)
        state.mark_synced(key, entry)
        n += 1
    if n:
        write_index(nc, cfg, state)
    print("imported %d file(s)" % n)


def cmd_rebuild_index(cfg, state, args):
    config.require(cfg, "nextcloud", "url", "user", "app_password")
    nc = Nextcloud(cfg)
    nc.check()
    write_index(nc, cfg, state)
    print("Activities.md rebuilt with %d entries" % len(state.synced))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="suunto2nextcloud",
                                 description="Sync Suunto workouts into Nextcloud as GPX/FIT.")
    ap.add_argument("-c", "--config", help="config.yaml path")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("auth", help="run the Suunto OAuth flow")
    a.add_argument("--no-browser", action="store_true")
    s = sub.add_parser("sync", help="pull new workouts")
    s.add_argument("--full", action="store_true", help="re-list from sync.start_date")
    s.add_argument("--interval", type=int, help="minutes between runs (0 = once)")
    i = sub.add_parser("import-fit", help="import local .fit files instead of the API")
    i.add_argument("paths", nargs="+")
    i.add_argument("--force", action="store_true")
    sub.add_parser("rebuild-index", help="regenerate Activities.md")
    sub.add_parser("test-nextcloud", help="check Nextcloud credentials")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = config.load(args.config)
    state = State(cfg["sync"]["state_dir"])
    handler = {
        "auth": cmd_auth, "sync": cmd_sync, "import-fit": cmd_import_fit,
        "rebuild-index": cmd_rebuild_index, "test-nextcloud": cmd_test_nextcloud,
    }[args.cmd]
    try:
        return handler(cfg, state, args) or 0
    except (SuuntoError, NextcloudError, SystemExit) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
