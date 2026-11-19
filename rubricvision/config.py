"""Settings loaded from config.json, next to the program.

Prices and the model live here, not in the code, so they can be updated
without rebuilding the .exe. If config.json is missing it is created with
the defaults below.
"""

from __future__ import annotations

import copy
import json
import logging
import sys
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_CONFIG: dict = {
    "model": "gemini-3.8-flash",
    "models": {
        "gemini-3.8-flash": {
            "label": "Gemini 3.8 Flash",
            "prices_usd_per_million": [
                {"from": "2026-01-01", "input": 0.75, "output": 3.75},
                {"from": "2027-01-01", "input": 1.50, "output": 7.50},
            ],
            "video_tokens_per_frame": {"normal": 70, "high": 280},
            "audio_tokens_per_second": 25,
        },
        "gemini-4-hydrogen": {
            "label": "Gemini 4 Hydrogen",
            "prices_usd_per_million": [
                {"from": "2026-01-01", "input": 1.50, "output": 7.50},
            ],
            "video_tokens_per_frame": {"normal": 70, "high": 280},
            "audio_tokens_per_second": 25,
        },
        "gemini-4-neon": {
            "label": "Gemini 4 Neon",
            "prices_usd_per_million": [
                {"from": "2026-01-01", "input": 3.00, "output": 15.00},
            ],
            "video_tokens_per_frame": {"normal": 70, "high": 280},
            "audio_tokens_per_second": 25,
        },
    },
    "max_submissions": 10,
    "max_parallel": 10,
    "default_accuracy": "high",
    "min_recommended_height": 720,
    "youtube_assumed_minutes_without_rubric_limit": 15,
    "request_timeout_seconds": 900,
    "file_processing_timeout_seconds": 900,
    "leftover_upload_min_age_hours": 3,
}


def app_dir() -> Path:
    """Folder that holds the .exe (or the project root when run from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or app_dir() / "config.json"
        data = DEFAULT_CONFIG
        if path.exists():
            try:
                data = _merge(DEFAULT_CONFIG, json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                log.warning("Could not read %s, using defaults: %s", path, exc)
        else:
            try:
                path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
            except OSError as exc:
                log.info("Could not write default config to %s: %s", path, exc)
        return cls(data)

    # ----- model -----
    @property
    def model_id(self) -> str:
        return self.data["model"]

    @property
    def model(self) -> dict:
        models = self.data["models"]
        if self.model_id not in models:
            raise ValueError(f"Model '{self.model_id}' is not described in config.json")
        return models[self.model_id]

    @property
    def model_label(self) -> str:
        return self.model.get("label", self.model_id)

    def prices(self, on: date | None = None) -> tuple[float, float]:
        """(input, output) price in USD per million tokens valid on a date."""
        on = on or date.today()
        entries = sorted(self.model["prices_usd_per_million"], key=lambda e: e["from"])
        chosen = entries[0]
        for entry in entries:
            if date.fromisoformat(entry["from"]) <= on:
                chosen = entry
        return float(chosen["input"]), float(chosen["output"])

    # ----- other settings -----
    def get(self, key: str):
        return self.data.get(key, DEFAULT_CONFIG.get(key))
