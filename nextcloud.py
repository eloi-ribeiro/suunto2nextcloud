"""Minimal WebDAV client for Nextcloud (works with app passwords on Hetzner Storage Share)."""
import logging
import urllib.parse

import requests

log = logging.getLogger(__name__)


class NextcloudError(RuntimeError):
    pass


class Nextcloud:
    def __init__(self, cfg):
        c = cfg["nextcloud"]
        self.base = "%s/remote.php/dav/files/%s" % (c["url"], urllib.parse.quote(c["user"]))
        self.root = c["folder"].strip("/")
        self.session = requests.Session()
        self.session.auth = (c["user"], c["app_password"])
        self.session.headers["User-Agent"] = "suunto2nextcloud/0.1"
        self._known_dirs = set()

    def _url(self, path):
        parts = [self.root] + [p for p in path.split("/") if p]
        return self.base + "/" + "/".join(urllib.parse.quote(p) for p in parts if p)

    def check(self):
        r = self.session.request("PROPFIND", self.base, headers={"Depth": "0"}, timeout=30)
        if r.status_code not in (207, 200):
            raise NextcloudError("Nextcloud login failed: %s %s" % (r.status_code, r.text[:200]))

    def mkdirs(self, path):
        parts = [p for p in path.split("/") if p]
        for i in range(len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub in self._known_dirs:
                continue
            r = self.session.request("MKCOL", self._url(sub), timeout=30)
            if r.status_code not in (201, 405):  # 405 = already exists
                raise NextcloudError("MKCOL %s -> %s" % (sub, r.status_code))
            self._known_dirs.add(sub)

    def exists(self, path):
        r = self.session.request("PROPFIND", self._url(path), headers={"Depth": "0"}, timeout=30)
        return r.status_code == 207

    def put(self, path, data, content_type="application/octet-stream"):
        self.mkdirs(path.rsplit("/", 1)[0] if "/" in path else "")
        r = self.session.put(self._url(path), data=data,
                             headers={"Content-Type": content_type}, timeout=300)
        if r.status_code not in (200, 201, 204):
            raise NextcloudError("PUT %s -> %s %s" % (path, r.status_code, r.text[:200]))
        log.info("uploaded %s (%d bytes)", path, len(data))

    def get(self, path):
        r = self.session.get(self._url(path), timeout=120)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise NextcloudError("GET %s -> %s" % (path, r.status_code))
        return r.content
