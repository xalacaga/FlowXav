"""Journal local rotatif de FlowXav."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flowxav.paths import user_data_path

LOG_MAX_BYTES = 2_000_000
LOG_BACKUP_COUNT = 5
_HANDLER_MARKER = "_flowxav_rotating_file"


def configure_logging(log_file: Path | None = None) -> Path:
    """Configure un journal UTF-8 borné, sans dupliquer les handlers."""
    target = (log_file or user_data_path("logs", "flowxav.log")).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in root.handlers:
        if getattr(handler, _HANDLER_MARKER, False) and Path(getattr(handler, "baseFilename", "")).resolve() == target:
            return target

    handler = RotatingFileHandler(target, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8")
    setattr(handler, _HANDLER_MARKER, True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    # L'interface interroge /api/etat plusieurs fois par seconde : seules les
    # requêtes en erreur méritent le journal.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.captureWarnings(True)
    return target
