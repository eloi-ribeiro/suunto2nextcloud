import json
from pathlib import Path


class State:
    """Tokens and the set of already-synced workout keys, persisted as JSON."""

    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tokens_file = self.dir / "tokens.json"
        self.synced_file = self.dir / "synced.json"
        self.tokens = self._read(self.tokens_file, {})
        data = self._read(self.synced_file, {"workouts": {}, "last_sync_ms": 0})
        self.synced = data.get("workouts", {})
        self.last_sync_ms = int(data.get("last_sync_ms", 0))

    @staticmethod
    def _read(path, default):
        if path.is_file():
            with open(path) as fh:
                return json.load(fh)
        return default

    def save_tokens(self, tokens):
        self.tokens = tokens
        self.tokens_file.write_text(json.dumps(tokens, indent=2))
        try:
            self.tokens_file.chmod(0o600)
        except OSError:
            pass

    def mark_synced(self, key, info):
        self.synced[key] = info
        self.save_synced()

    def save_synced(self):
        self.synced_file.write_text(json.dumps(
            {"workouts": self.synced, "last_sync_ms": self.last_sync_ms}, indent=2))
