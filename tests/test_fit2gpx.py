import io
import xml.dom.minidom
from pathlib import Path

import pytest

import fit2gpx
import index
from cli import summary_from_api, workout_paths

SAMPLE = Path(__file__).parent / "fixtures" / "garmin-fenix-5-bike.fit"


@pytest.fixture
def fit_bytes():
    return SAMPLE.read_bytes()


def test_parse_and_convert(fit_bytes):
    rec, laps, sess, sport = fit2gpx.parse_fit(fit_bytes)
    s = fit2gpx.summary_from_fit(sess, sport, rec)
    assert s["sport"] == "Cycling"
    assert s["startTime"] == "2017-06-12T16:09:22Z"
    assert s["avgHr"] == 101
    gpx, n = fit2gpx.to_gpx(rec, "t", s["startTime"], s["sport"], laps)
    assert n == 19
    xml.dom.minidom.parseString(gpx)
    assert "<gpxtpx:hr>" in gpx and 'lat="37.41' in gpx


def test_no_gps_yields_no_points():
    gpx, n = fit2gpx.to_gpx([{"heart_rate": 120}], "indoor")
    assert n == 0 and "<trkpt" not in gpx


def test_summary_from_api_variants():
    a = summary_from_api({"workoutKey": "abc", "activityId": 22, "startTime": 1756364400000,
                          "totalTime": 3600, "totalDistance": 10000, "hrdata": {"workoutAvgHR": 150}})
    assert a["workoutKey"] == "abc" and a["sport"] == "Trail running"
    assert a["startTime"] == "2025-08-28T07:00:00Z" and a["avgHr"] == 150
    b = summary_from_api({"key": "k", "activityId": 9999, "startTime": "2026-01-02T03:04:05Z"})
    assert b["workoutKey"] == "k" and "sport" not in b


def test_workout_paths():
    assert workout_paths({"startTime": "2026-08-28T07:12:00Z", "sport": "Trail running"}) == \
        "2026/2026-08-28_0712_Trail_running"


def test_index_build():
    md = index.build([{"startTime": "2026-08-28T07:12:00Z", "sport": "Running", "totalTime": 1800,
                       "totalDistance": 5000, "gpx": "2026/a.gpx"}], "Suunto", "https://x")
    assert "## 2026-08" in md and "6:00 /km" in md and "[gpx](2026/a.gpx)" in md
