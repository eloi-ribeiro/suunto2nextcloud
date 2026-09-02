import os
from pathlib import Path

import yaml

_DEFAULTS = {
    "suunto": {
        "client_id": "",
        "client_secret": "",
        "subscription_key": "",
        "redirect_uri": "http://localhost:8765/callback",
        "api_base": "https://cloudapi.suunto.com",
        "oauth_base": "https://cloudapi-oauth.suunto.com",
    },
    "nextcloud": {
        "url": "",
        "user": "",
        "app_password": "",
        "folder": "Suunto",
        "upload_fit": True,
        "upload_json": True,
    },
    "sync": {
        "start_date": "2024-01-01",
        "interval_minutes": 0,
        "state_dir": "./state",
    },
}


def _coerce(value, like):
    if isinstance(like, bool):
        return str(value).lower() in ("1", "true", "yes", "on")
    if isinstance(like, int):
        return int(value)
    return value


def load(path=None):
    """Merge defaults <- yaml file <- S2N_<SECTION>_<KEY> environment variables."""
    cfg = {k: dict(v) for k, v in _DEFAULTS.items()}
    candidates = [path] if path else ["config.yaml", "/config/config.yaml"]
    for cand in candidates:
        if cand and Path(cand).is_file():
            with open(cand) as fh:
                data = yaml.safe_load(fh) or {}
            for section, values in data.items():
                if section in cfg and isinstance(values, dict):
                    cfg[section].update(values)
            break
    for section, values in cfg.items():
        for key, default in values.items():
            env = os.environ.get("S2N_%s_%s" % (section.upper(), key.upper()))
            if env is not None:
                values[key] = _coerce(env, default)
    cfg["nextcloud"]["url"] = cfg["nextcloud"]["url"].rstrip("/")
    return cfg


def require(cfg, section, *keys):
    missing = [k for k in keys if not cfg[section].get(k)]
    if missing:
        raise SystemExit(
            "Missing config: %s (set in config.yaml or as %s)"
            % (", ".join("%s.%s" % (section, k) for k in missing),
               ", ".join("S2N_%s_%s" % (section.upper(), k.upper()) for k in missing))
        )
