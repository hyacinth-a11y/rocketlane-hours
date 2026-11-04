"""Local storage for synced Rocketlane data and app settings.

Synced data lives in a SQLite file next to the app (data/rocketlane.db).
Settings (API key, company name, sync window) live in data/settings.json.
On Streamlit Community Cloud this disk is wiped when the app restarts, so the
app simply asks for a new Sync; the API key in Secrets is the permanent copy.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "rocketlane.db"
SETTINGS_PATH = DATA_DIR / "settings.json"

DEFAULT_SETTINGS = {"api_key": "", "company": "nClouds", "sync_months": 12}


# ---------------------------------------------------------------- settings
def load_settings() -> dict:
    try:
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS_PATH.read_text())}
    except (FileNotFoundError, ValueError):
        return dict(DEFAULT_SETTINGS)


def save_settings(**changes) -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    s = {**load_settings(), **changes}
    SETTINGS_PATH.write_text(json.dumps(s))
    try:
        SETTINGS_PATH.chmod(0o600)
    except OSError:
        pass
    return s


# ---------------------------------------------------------------- synced data
def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    return sqlite3.connect(DB_PATH)


def save_sync(projects: list[dict], entries: list[dict], since, source: str) -> None:
    with _conn() as c:
        pd.DataFrame(projects).to_sql("projects", c, if_exists="replace", index=False)
        pd.DataFrame(entries).to_sql("entries", c, if_exists="replace", index=False)
        meta = {"synced_at": datetime.now(timezone.utc).isoformat(), "since": str(since),
                "source": source, "projects": len(projects), "entries": len(entries)}
        pd.DataFrame([meta]).to_sql("meta", c, if_exists="replace", index=False)


def sync_info() -> dict | None:
    if not DB_PATH.exists():
        return None
    try:
        with _conn() as c:
            return pd.read_sql("select * from meta", c).iloc[0].to_dict()
    except Exception:
        return None


def projects() -> pd.DataFrame:
    with _conn() as c:
        return pd.read_sql("select * from projects order by name", c)


def entries(project_id=None, start=None, end=None) -> pd.DataFrame:
    q, args = "select * from entries where 1=1", []
    if project_id is not None:
        q += " and project_id = ?"; args.append(int(project_id))
    if start:
        q += " and date >= ?"; args.append(str(start))
    if end:
        q += " and date <= ?"; args.append(str(end))
    with _conn() as c:
        df = pd.read_sql(q + " order by date", c, params=args)
    if not df.empty:
        df["billable"] = df["billable"].astype(bool)
        df["notes"] = df["notes"].fillna("")
    return df


def project_summary() -> pd.DataFrame:
    """Hours logged and last activity per project, for the project list."""
    with _conn() as c:
        return pd.read_sql("select project_id, sum(hours) as hours, max(date) as last_logged "
                           "from entries group by project_id", c)


def clear() -> None:
    DB_PATH.unlink(missing_ok=True)
