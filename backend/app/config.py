"""Central configuration. Everything is overridable through environment variables."""

from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv is optional: plain environment variables work too
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"

if load_dotenv is not None:
    load_dotenv(BACKEND_DIR / ".env")


def _path_from_env(name: str, default: Path | None) -> Path | None:
    value = os.getenv(name)
    if not value:
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else BACKEND_DIR / path


# The care provider's database. It is NOT part of this repo: point
# SOURCE_DB_PATH (in backend/.env) at wherever the file lives. Opened read-only.
SOURCE_DB_PATH = _path_from_env("SOURCE_DB_PATH", None)

# Everything this system derives (flattened tables, signals, search index).
# Not a copy of the source: safe to delete, it is rebuilt from the source.
INDEX_DB_PATH = _path_from_env("INDEX_DB_PATH", DATA_DIR / "agent_index.db")

# Optional pin for "today". By default "today" is the latest visit in the data,
# so "this week" means the last week of records, not the real calendar week.
AS_OF_OVERRIDE = os.getenv("AS_OF") or None

# Rows processed per transaction during ingestion. Keeps memory flat
# no matter how big the source table is.
INGEST_BATCH_SIZE = int(os.getenv("INGEST_BATCH_SIZE", "500"))

# --------------------------------------------------------------------------- LLM
# Model names are configurable because providers rename models often.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or None
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "gpt-5-mini")  # runs once per visit
AGENT_MODEL = os.getenv("AGENT_MODEL", "gpt-5")                  # answers questions
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "512"))  # 512 vs 1536: 3x less storage
EXTRACTION_CONCURRENCY = int(os.getenv("EXTRACTION_CONCURRENCY", "8"))

# --------------------------------------------------------------------------- risk scoring
# Illustrative weights and threshold, set on the 14-day sample. In production they
# would be calibrated against outcomes (falls, GP call-outs, admissions).
SEVERITY_WEIGHTS = {"low": 1, "medium": 2, "high": 3}
TREND_ALERT_THRESHOLD = int(os.getenv("TREND_ALERT_THRESHOLD", "6"))  # 7-day rolling score
ROLLING_DAYS = 7
# Rules and roll-ups are recomputed over this trailing window on each run, so the
# work per run stays constant as history grows.
RECOMPUTE_WINDOW_DAYS = int(os.getenv("RECOMPUTE_WINDOW_DAYS", "30"))

# --------------------------------------------------------------------------- agent
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "8"))       # tool rounds per question
TOOL_RESULT_MAX_CHARS = int(os.getenv("TOOL_RESULT_MAX_CHARS", "12000"))
SQL_MAX_ROWS = 200
SQL_TIMEOUT_SECONDS = 5
