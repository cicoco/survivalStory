from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class EventStore:
    def __init__(self, db_path: str = "game.db"):
        self.db_path = db_path
        Path(db_path).touch(exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    phase_no INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    actor_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS game_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    winner_text TEXT NOT NULL,
                    finish_reason TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def log(self, game_id: str, phase_no: int, phase: str, actor_id: str | None, event_type: str, payload: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO event_log (game_id, phase_no, phase, actor_id, event_type, payload_json)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (game_id, phase_no, phase, actor_id, event_type, json.dumps(payload, ensure_ascii=False)),
            )

    def save_summary(self, game_id: str, winner_text: str, finish_reason: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO game_summary (game_id, winner_text, finish_reason)
                VALUES (?, ?, ?);
                """,
                (game_id, winner_text, finish_reason),
            )
