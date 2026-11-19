"""Entry point: python -m rubricvision.main (or the RubricVisionAI.exe)."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / "RubricVision AI"
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup_logging() -> None:
    handler = RotatingFileHandler(log_dir() / "rubricvision.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # The SDK and HTTP libraries can log request URLs; keep them quiet.
    for noisy in ("httpx", "httpcore", "google_genai", "google.genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    setup_logging()
    from .config import Config
    from .ui import run

    try:
        run(Config.load())
    except Exception:
        logging.exception("RubricVision AI crashed")
        try:
            from tkinter import messagebox

            messagebox.showerror("RubricVision AI", f"RubricVision AI had an unexpected problem.\nDetails were saved to:\n{log_dir()}")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
