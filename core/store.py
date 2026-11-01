"""Versioned SQLite snapshots: a failed refresh never destroys the last good data."""
from __future__ import annotations

import json
import os
import sqlite3
import gzip
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("MACRO_DESK_DATA", str(ROOT / "data")))


class SnapshotConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def public_mode() -> bool:
    flag = ROOT / "deployment.json"
    return os.environ.get("MACRO_DESK_PUBLIC_MODE") == "1" or (flag.exists() and json.loads(flag.read_text(encoding="utf-8")).get("public_mode") is True)


def data_dir() -> Path:
    # Cloud visitors may import or refresh data without changing another
    # visitor's view. Workers inherit this exact directory via their environment.
    if not public_mode() or os.environ.get("MACRO_DESK_DATA"):
        return DATA_DIR
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    if get_script_run_ctx(suppress_warning=True) is None:
        return DATA_DIR
    import streamlit as st
    if "_cloud_data_directory" not in st.session_state:
        st.session_state["_cloud_data_directory"] = tempfile.TemporaryDirectory(prefix="macro-desk-session-",ignore_cleanup_errors=True)
    return Path(st.session_state["_cloud_data_directory"].name)


def connect() -> sqlite3.Connection:
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    first = not (directory / "snapshots.sqlite3").exists()
    con = sqlite3.connect(directory / "snapshots.sqlite3", timeout=20,factory=SnapshotConnection)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, name TEXT, fetched_at TEXT, body TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS attempts (name TEXT PRIMARY KEY, attempted_at TEXT, error TEXT)")
    seed = ROOT / "resources" / "public_seed.json.gz"
    if first and public_mode() and seed.exists():
        with gzip.open(seed,"rt",encoding="utf-8") as stream:
            snapshots = json.load(stream)["snapshots"]
        for entry in snapshots:
            # Preserve original successful acquisition dates, not boot time.
            con.execute("INSERT INTO snapshots(name,fetched_at,body) VALUES(?,?,?)",
                (entry["name"],entry["fetched_at"],json.dumps(entry["payload"],ensure_ascii=False,allow_nan=False)))
        con.commit()
    return con


def save(name: str, payload: dict) -> None:
    if not payload.get("records"):
        raise ValueError("返回空数据，保留上一次成功记录")
    with connect() as con:
        con.execute("INSERT INTO snapshots(name,fetched_at,body) VALUES(?,?,?)",
                    (name, now(), json.dumps(payload, ensure_ascii=False, allow_nan=False)))
        con.execute("INSERT OR REPLACE INTO attempts VALUES(?,?,?)", (name, now(), ""))


def failure(name: str, error: str) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO attempts VALUES(?,?,?)", (name, now(), error))


def load(name: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT fetched_at,body FROM snapshots WHERE name=? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
        attempt = con.execute("SELECT attempted_at,error FROM attempts WHERE name=?", (name,)).fetchone()
    if not row:
        return None
    return {**json.loads(row[1]), "fetched_at": row[0], "attempt": attempt}


def status() -> list[dict]:
    with connect() as con:
        rows = con.execute("SELECT name,attempted_at,error FROM attempts ORDER BY name").fetchall()
    return [{"数据集": n, "最后尝试（北京时间）": t, "状态": e or "成功"} for n, t, e in rows]
