from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class EventStore:
    def __init__(self, db_path: str = "game.db"):
        self.db_path = db_path
        Path(db_path).touch(exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=3000;")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    room_id TEXT,
                    phase_no INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    action_seq INTEGER,
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
                    room_id TEXT NOT NULL,
                    survivors_text TEXT NOT NULL,
                    finish_reason TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            self._ensure_indexes(conn)

    def _ensure_indexes(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_log_game_phase_id ON event_log(game_id, phase_no, id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_log_room_phase_seq ON event_log(room_id, phase_no, action_seq, id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_log_event_type ON event_log(event_type);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_game_summary_game ON game_summary(game_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_game_summary_room ON game_summary(room_id);")

    def _exec_with_retry(self, fn, retries: int = 3, sleep_sec: float = 0.05):
        last_err = None
        for _ in range(retries):
            try:
                return fn()
            except sqlite3.OperationalError as err:
                last_err = err
                if "locked" not in str(err).lower():
                    raise
                time.sleep(sleep_sec)
        if last_err:
            raise last_err

    def log(
        self,
        game_id: str,
        phase_no: int,
        phase: str,
        actor_id: str | None,
        event_type: str,
        payload: dict,
        room_id: str | None = None,
        action_seq: int | None = None,
    ) -> None:
        def _write():
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO event_log (game_id, room_id, phase_no, phase, action_seq, actor_id, event_type, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        game_id,
                        room_id,
                        phase_no,
                        phase,
                        action_seq,
                        actor_id,
                        event_type,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )

        self._exec_with_retry(_write)

    def save_summary(self, game_id: str, survivors_text: str, finish_reason: str, room_id: str | None = None) -> None:
        if room_id is None:
            raise ValueError("room_id_required_for_summary")

        def _write():
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO game_summary (game_id, room_id, survivors_text, finish_reason)
                    VALUES (?, ?, ?, ?);
                    """,
                    (game_id, room_id, survivors_text, finish_reason),
                )

        self._exec_with_retry(_write)
