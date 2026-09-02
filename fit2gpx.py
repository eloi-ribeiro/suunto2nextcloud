"""Convert a Suunto FIT file to GPX 1.1 (with Garmin TrackPointExtension for hr/cad/temp/power)
and extract a summary dict from the FIT session message."""
import datetime as dt
import io
import math
from xml.sax.saxutils import escape

import fitdecode

SEMI = 180.0 / 2 ** 31
GPX_NS = {
    "xmlns": "http://www.topografix.com/GPX/1/1",
    "xmlns:gpxtpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
    "xmlns:gpxx": "http://www.garmin.com/xmlschemas/GpxExtensions/v3",
    "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "xsi:schemaLocation": "http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd",
}

# FIT sport enum -> readable (fallback when the API summary lacks a name)
FIT_SPORTS = {
    "running": "Running", "cycling": "Cycling", "hiking": "Hiking", "walking": "Walking",
    "swimming": "Swimming", "training": "Training", "generic": "Activity",
    "cross_country_skiing": "Cross-country skiing", "alpine_skiing": "Downhill skiing",
    "trail_running": "Trail running", "mountaineering": "Mountaineering", "rowing": "Rowing",
    "paddling": "Paddling", "fitness_equipment": "Gym", "e_biking": "E-biking",
}


def _iso(t):
    if isinstance(t, dt.datetime):
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _num(v):
    return v is not None and isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v))


def parse_fit(data):
    """Return (records, laps, session, sport) from FIT bytes."""
    records, laps, session, sport = [], [], {}, {}
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.WARN) as fr:
        for frame in fr:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "record":
                rec = {}
                for f in frame.fields:
                    if f.value is not None:
                        rec[f.name] = f.value
                records.append(rec)
            elif frame.name == "lap":
                laps.append({f.name: f.value for f in frame.fields if f.value is not None})
            elif frame.name == "session" and not session:
                session = {f.name: f.value for f in frame.fields if f.value is not None}
            elif frame.name == "sport" and not sport:
                sport = {f.name: f.value for f in frame.fields if f.value is not None}
    return records, laps, session, sport


def summary_from_fit(session, sport, records):
    """Normalise the FIT session into the same shape we use for the API summary."""
    s = session or {}
    start = s.get("start_time") or (records[0].get("timestamp") if records else None)
    sport_key = str(s.get("sport") or sport.get("sport") or "").lower()
    sub = str(s.get("sub_sport") or sport.get("sub_sport") or "").lower()
    sport_name = FIT_SPORTS.get(sport_key)
    if sport_key == "running" and sub == "trail":
        sport_name = "Trail running"
    elif sport_key == "cycling" and sub == "mountain":
        sport_name = "Mountain biking"
    elif sport_key == "cycling" and (sub in ("indoor_cycling", "spin", "virtual_activity")):
        sport_name = "Indoor cycling"
    elif sport_key == "running" and sub in ("treadmill", "indoor_running"):
        sport_name = "Treadmill"
    elif sport_key == "swimming" and sub == "open_water":
        sport_name = "Openwater swimming"
    if not sport_name and sport_key:
        sport_name = sport_key.replace("_", " ").capitalize()
    out = {
        "startTime": _iso(start),
        "sport": sport_name,
        "totalTime": s.get("total_timer_time") or s.get("total_elapsed_time"),
        "totalDistance": s.get("total_distance"),
        "totalAscent": s.get("total_ascent"),
        "totalDescent": s.get("total_descent"),
        "avgHr": s.get("avg_heart_rate"),
        "maxHr": s.get("max_heart_rate"),
        "avgSpeed": s.get("enhanced_avg_speed") or s.get("avg_speed"),
        "maxSpeed": s.get("enhanced_max_speed") or s.get("max_speed"),
        "calories": s.get("total_calories"),
        "avgCadence": s.get("avg_cadence"),
        "avgPower": s.get("avg_power"),
        "trainingEffect": s.get("total_training_effect"),
        "anaerobicTrainingEffect": s.get("total_anaerobic_training_effect"),
        "hasGps": any("position_lat" in r for r in records[:200]),
        "points": len(records),
    }
    if not out["sport"] and sport.get("name"):
        out["sport"] = sport["name"]
    return {k: v for k, v in out.items() if v is not None}


def to_gpx(records, name, start_iso=None, sport=None, laps=None):
    """Build a GPX 1.1 string. Records without a position are skipped (indoor workouts yield no trkpt)."""
    buf = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<gpx version="1.1" creator="suunto2nextcloud" ',
        " ".join('%s="%s"' % (k, v) for k, v in GPX_NS.items()), ">\n",
        "  <metadata>\n    <name>%s</name>\n" % escape(name),
    ]
    if start_iso:
        buf.append("    <time>%s</time>\n" % start_iso)
    buf.append("  </metadata>\n  <trk>\n    <name>%s</name>\n" % escape(name))
    if sport:
        buf.append("    <type>%s</type>\n" % escape(str(sport)))
    buf.append("    <trkseg>\n")
    n = 0
    for r in records:
        lat, lon = r.get("position_lat"), r.get("position_long")
        if not (_num(lat) and _num(lon)):
            continue
        lat, lon = lat * SEMI, lon * SEMI
        if abs(lat) > 90 or abs(lon) > 180:
            continue
        buf.append('      <trkpt lat="%.7f" lon="%.7f">\n' % (lat, lon))
        ele = r.get("enhanced_altitude", r.get("altitude"))
        if _num(ele):
            buf.append("        <ele>%.1f</ele>\n" % ele)
        t = _iso(r.get("timestamp"))
        if t:
            buf.append("        <time>%s</time>\n" % t)
        ext = []
        if _num(r.get("heart_rate")):
            ext.append("<gpxtpx:hr>%d</gpxtpx:hr>" % r["heart_rate"])
        if _num(r.get("cadence")):
            ext.append("<gpxtpx:cad>%d</gpxtpx:cad>" % r["cadence"])
        if _num(r.get("temperature")):
            ext.append("<gpxtpx:atemp>%d</gpxtpx:atemp>" % r["temperature"])
        spd = r.get("enhanced_speed", r.get("speed"))
        if _num(spd):
            ext.append("<gpxtpx:speed>%.3f</gpxtpx:speed>" % spd)
        if _num(r.get("power")):
            ext.append("<power>%d</power>" % r["power"])
        if ext:
            buf.append("        <extensions><gpxtpx:TrackPointExtension>%s</gpxtpx:TrackPointExtension></extensions>\n"
                       % "".join(e for e in ext if e.startswith("<gpxtpx")))
            pw = [e for e in ext if e.startswith("<power")]
            if pw:
                buf[-1] = buf[-1].replace("</extensions>", "%s</extensions>" % pw[0])
        buf.append("      </trkpt>\n")
        n += 1
    buf.append("    </trkseg>\n  </trk>\n</gpx>\n")
    return "".join(buf), n
