"""Build the Activities.md index (rendered by Nextcloud Text) from per-workout summaries."""
import datetime as dt


def _fmt_dur(sec):
    if sec is None:
        return ""
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def _fmt_km(m):
    return "%.2f" % (m / 1000.0) if m else ""


def _pace(total_time, dist_m, sport):
    if not total_time or not dist_m:
        return ""
    if "cycl" in (sport or "").lower() or "bik" in (sport or "").lower():
        return "%.1f km/h" % (dist_m / 1000.0 / (total_time / 3600.0))
    p = total_time / (dist_m / 1000.0)
    return "%d:%02d /km" % (int(p // 60), int(p % 60))


def build(entries, folder, nc_url):
    """entries: list of dicts with startTime (ISO), sport, totalTime, totalDistance, totalAscent,
    avgHr, gpx (relative path), fit (relative path)."""
    entries = sorted(entries, key=lambda e: e.get("startTime") or "", reverse=True)
    lines = [
        "# Suunto activities", "",
        "_Synced by suunto2nextcloud — %d workouts, updated %s UTC_" % (
            len(entries), dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")), "",
        "Open the folder in **GpxPod** for maps and charts: %s/apps/gpxpod/" % nc_url, "",
    ]
    by_month = {}
    for e in entries:
        by_month.setdefault((e.get("startTime") or "")[:7], []).append(e)
    for month in sorted(by_month, reverse=True):
        rows = by_month[month]
        tot_d = sum((r.get("totalDistance") or 0) for r in rows)
        tot_t = sum((r.get("totalTime") or 0) for r in rows)
        lines += ["## %s — %d activities, %s km, %s" % (month or "unknown", len(rows), _fmt_km(tot_d), _fmt_dur(tot_t)), "",
                  "| Date | Sport | Distance km | Time | Pace | Ascent m | Avg HR | Files |",
                  "|---|---|---:|---:|---:|---:|---:|---|"]
        for r in rows:
            files = []
            if r.get("gpx"):
                files.append("[gpx](%s)" % r["gpx"])
            if r.get("fit"):
                files.append("[fit](%s)" % r["fit"])
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                (r.get("startTime") or "")[:16].replace("T", " "),
                r.get("sport") or "",
                _fmt_km(r.get("totalDistance")),
                _fmt_dur(r.get("totalTime")),
                _pace(r.get("totalTime"), r.get("totalDistance"), r.get("sport")),
                int(r["totalAscent"]) if r.get("totalAscent") else "",
                int(r["avgHr"]) if r.get("avgHr") else "",
                " ".join(files)))
        lines.append("")
    return "\n".join(lines)
