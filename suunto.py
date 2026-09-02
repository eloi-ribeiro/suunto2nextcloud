"""Suunto Cloud API client (official partner API, https://apizone.suunto.com).

OAuth2 authorisation-code flow against cloudapi-oauth.suunto.com, then
Bearer JWT + Ocp-Apim-Subscription-Key against cloudapi.suunto.com.
"""
import base64
import http.server
import logging
import threading
import time
import urllib.parse
import webbrowser

import requests

log = logging.getLogger(__name__)

# Suunto activityId -> sport name. Only ids confirmed from Suunto/Sports Tracker data are listed;
# for anything else the sport recorded in the FIT file is used instead.
ACTIVITY_NAMES = {
    1: "Running", 2: "Cycling", 3: "Cross-country skiing", 10: "Mountain biking", 11: "Hiking",
    12: "Roller skating", 13: "Downhill skiing", 14: "Paddling", 15: "Rowing", 16: "Golf",
    17: "Indoor", 21: "Swimming", 22: "Trail running", 23: "Gym", 24: "Nordic walking",
    26: "Motorsports", 29: "Climbing", 30: "Snowboarding", 31: "Ski touring",
    62: "Kayaking", 63: "Canoeing", 65: "Ski touring", 70: "Sailing", 73: "Orienteering",
    74: "Multisport", 75: "Triathlon", 78: "Crossfit", 81: "Treadmill", 82: "Indoor cycling",
    83: "Indoor rowing", 85: "Walking", 88: "Openwater swimming", 89: "Stand up paddling",
    90: "Trekking", 91: "Mountaineering", 93: "Transition",
}


def activity_name(activity_id, fallback=None):
    """Readable sport for an API item, or None when unknown (caller then falls back to the FIT sport)."""
    if fallback:
        return str(fallback).replace("_", " ").capitalize()
    try:
        return ACTIVITY_NAMES.get(int(activity_id))
    except (TypeError, ValueError):
        return None


class SuuntoError(RuntimeError):
    pass


class SuuntoClient:
    def __init__(self, cfg, state):
        self.cfg = cfg["suunto"]
        self.state = state
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "suunto2nextcloud/0.1"

    # ---------------- OAuth ----------------
    def authorize_url(self):
        q = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": self.cfg["client_id"],
            "redirect_uri": self.cfg["redirect_uri"],
        })
        return "%s/oauth/authorize?%s" % (self.cfg["oauth_base"], q)

    def _token_request(self, data):
        basic = base64.b64encode(
            ("%s:%s" % (self.cfg["client_id"], self.cfg["client_secret"])).encode()).decode()
        r = self.session.post(
            "%s/oauth/token" % self.cfg["oauth_base"], data=data,
            headers={"Authorization": "Basic " + basic}, timeout=30)
        if r.status_code != 200:
            raise SuuntoError("token request failed: %s %s" % (r.status_code, r.text[:300]))
        tokens = r.json()
        tokens["obtained_at"] = int(time.time())
        self.state.save_tokens(tokens)
        return tokens

    def exchange_code(self, code):
        return self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.cfg["redirect_uri"],
        })

    def refresh(self):
        rt = self.state.tokens.get("refresh_token")
        if not rt:
            raise SuuntoError("no refresh token; run `auth` first")
        return self._token_request({"grant_type": "refresh_token", "refresh_token": rt})

    def access_token(self):
        t = self.state.tokens
        if not t.get("access_token"):
            raise SuuntoError("not authorised; run `suunto2nextcloud auth`")
        expires_at = t.get("obtained_at", 0) + int(t.get("expires_in", 86400))
        if time.time() > expires_at - 300:
            log.info("access token expired/expiring, refreshing")
            t = self.refresh()
        return t["access_token"]

    def interactive_auth(self, open_browser=True):
        """Run the auth-code flow. Uses a local HTTP listener if redirect_uri points at localhost,
        otherwise asks the user to paste the redirected URL."""
        url = self.authorize_url()
        parsed = urllib.parse.urlparse(self.cfg["redirect_uri"])
        print("\nOpen this URL and log in with your Suunto account:\n\n  %s\n" % url)
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        code = None
        if parsed.hostname in ("localhost", "127.0.0.1"):
            code = _wait_for_code(parsed.hostname, parsed.port or 80, parsed.path or "/")
        if not code:
            pasted = input("Paste the full URL you were redirected to: ").strip()
            code = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).get("code", [None])[0]
        if not code:
            raise SuuntoError("no authorisation code received")
        tokens = self.exchange_code(code)
        print("Authorised. Scope: %s, expires in %ss" % (tokens.get("scope"), tokens.get("expires_in")))
        return tokens

    # ---------------- API ----------------
    def _headers(self):
        return {
            "Authorization": "Bearer " + self.access_token(),
            "Ocp-Apim-Subscription-Key": self.cfg["subscription_key"],
        }

    def _get(self, path, params=None, stream=False, retry=True):
        r = self.session.get(self.cfg["api_base"] + path, params=params,
                             headers=self._headers(), timeout=120, stream=stream)
        if r.status_code == 401 and retry:
            self.refresh()
            return self._get(path, params, stream, retry=False)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "60"))
            log.warning("rate limited, sleeping %ss", wait)
            time.sleep(wait)
            return self._get(path, params, stream, retry=False)
        if r.status_code >= 400:
            raise SuuntoError("GET %s -> %s %s" % (path, r.status_code, r.text[:300]))
        return r

    @staticmethod
    def _payload(r):
        data = r.json()
        if isinstance(data, dict):
            if data.get("error"):
                raise SuuntoError("API error: %s" % data["error"])
            if "payload" in data:
                return data["payload"]
        return data

    def list_workouts(self, since_ms=None, until_ms=None, page_size=100):
        """Yield workout summaries newest-first, following limit/offset pagination."""
        offset = 0
        while True:
            params = {"limit": page_size, "offset": offset}
            if since_ms:
                params["since"] = int(since_ms)
            if until_ms:
                params["until"] = int(until_ms)
            items = self._payload(self._get("/v2/workouts", params)) or []
            if not isinstance(items, list):
                items = [items]
            for w in items:
                yield w
            if len(items) < page_size:
                break
            offset += len(items)

    def get_workout(self, key):
        return self._payload(self._get("/v2/workouts/%s" % key))

    def export_fit(self, key):
        r = self._get("/v2/workout/exportFit/%s" % key, stream=True)
        return r.content


def _wait_for_code(host, port, path, timeout=300):
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(u.query)
            if "code" in qs:
                result["code"] = qs["code"][0]
                body = b"<h2>suunto2nextcloud: authorised, you can close this tab.</h2>"
            else:
                body = b"<h2>No code in request.</h2>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    try:
        srv = http.server.HTTPServer((host, port), Handler)
    except OSError as e:
        log.warning("could not bind %s:%s (%s); falling back to paste flow", host, port, e)
        return None
    srv.timeout = 1
    print("Waiting up to %ss for the redirect on http://%s:%s%s ..." % (timeout, host, port, path))
    deadline = time.time() + timeout
    while "code" not in result and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    return result.get("code")
