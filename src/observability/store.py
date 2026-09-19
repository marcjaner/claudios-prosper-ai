import json
import sqlite3
import time
from datetime import date, datetime
from datetime import time as clock
from pathlib import Path
from zoneinfo import ZoneInfo

from .bus import CallUpdate, Event

CLINIC_TIMEZONE = ZoneInfo("Europe/Madrid")
DAY_SECONDS = 24 * 60 * 60

# Interim transcripts are for watching the transcript correct itself live. The
# finals are the transcript, and skipping the partials drops most of the writes.
BROADCAST_ONLY = frozenset({"stt_partial"})

# What a person actually said or heard, and so what history search covers.
SPOKEN_KINDS = ("stt_final", "llm", "tts")

CALL_FIELDS = (
    "started_at",
    "ended_at",
    "state",
    "from_number",
    "patient_id",
    "patient_name",
    "insurer",
    "outcome",
    "reason",
    "cost_eur",
    "ttfa_seconds",
    "error",
    "verdict_json",
)

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    ended_at REAL,
    state TEXT,
    from_number TEXT,
    patient_id TEXT,
    patient_name TEXT,
    insurer TEXT,
    outcome TEXT,
    reason TEXT,
    cost_eur REAL,
    ttfa_seconds REAL,
    error TEXT,
    verdict_json TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_call_idx ON events(call_id, id);
"""


class Store:
    """SQLite behind the bus. One writer, owned by the drain task.

    Readers open their own connection: in WAL mode they never block the writer.
    """

    def __init__(self, path: Path):
        self.path = path
        self._writer = sqlite3.connect(path, isolation_level=None)
        self._writer.executescript(SCHEMA)
        self._add_missing_columns()
        # A call left open by a process that died is over, not live.
        self._writer.execute(
            "UPDATE calls SET ended_at = started_at, state = 'lost' "
            "WHERE ended_at IS NULL"
        )

    def _add_missing_columns(self) -> None:
        # CREATE TABLE IF NOT EXISTS leaves an older file on its old shape, and
        # the weekend's history is worth more than a clean migration story.
        present = {row[1] for row in self._writer.execute("PRAGMA table_info(calls)")}
        for name in CALL_FIELDS:
            if name not in present:
                self._writer.execute(f"ALTER TABLE calls ADD COLUMN {name} TEXT")

    def close(self) -> None:
        self._writer.close()

    def write(self, batch: list[Event | CallUpdate]) -> None:
        self._writer.execute("BEGIN")
        try:
            for item in batch:
                if isinstance(item, CallUpdate):
                    self._upsert_call(item)
                elif item.kind not in BROADCAST_ONLY:
                    self._insert_event(item)
            self._writer.execute("COMMIT")
        except Exception:
            self._writer.execute("ROLLBACK")
            raise

    def _insert_event(self, event: Event) -> None:
        self._writer.execute(
            "INSERT INTO events (call_id, ts, kind, payload_json) VALUES (?, ?, ?, ?)",
            (
                event.call_id,
                event.ts,
                event.kind,
                # ensure_ascii would store 'dermat\\u00f3loga' and no LIKE over
                # a Spanish transcript would ever match.
                json.dumps(event.payload, default=str, ensure_ascii=False),
            ),
        )

    def _upsert_call(self, update: CallUpdate) -> None:
        # Field names reach SQL, so they come from the whitelist, never the caller.
        given = {
            name: update.fields[name] for name in CALL_FIELDS if name in update.fields
        }
        # Only what the caller actually passed is updated. Defaulting
        # started_at into the UPDATE arm would let the closing write stamp the
        # end time over the start, and every call would show zero duration.
        assignments = ", ".join(f"{name} = excluded.{name}" for name in given)
        columns = dict(given)
        columns.setdefault("started_at", time.time())
        placeholders = ", ".join("?" * len(columns))
        conflict = f"DO UPDATE SET {assignments}" if assignments else "DO NOTHING"
        self._writer.execute(
            f"INSERT INTO calls (call_id, {', '.join(columns)}) "
            f"VALUES (?, {placeholders}) ON CONFLICT(call_id) {conflict}",
            (update.call_id, *columns.values()),
        )

    def _read(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def list_calls(
        self,
        limit: int = 200,
        offset: int = 0,
        outcome: str | None = None,
        reason: str | None = None,
        search: str | None = None,
        ended_only: bool = False,
        name: str | None = None,
        insurer: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        clauses, params = [], []
        # The wall owns calls in flight; history is what already finished.
        if ended_only:
            clauses.append("ended_at IS NOT NULL")
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        if reason:
            clauses.append("reason = ?")
            params.append(reason)
        if name:
            clauses.append("patient_name LIKE ?")
            params.append(f"%{name}%")
        if insurer:
            clauses.append("insurer = ?")
            params.append(insurer)
        # Dates arrive as ISO days and are read in Europe/Madrid, the only
        # clock this clinic has; date_to is inclusive of its whole day.
        if date_from:
            clauses.append("started_at >= ?")
            params.append(_day_start(date_from))
        if date_to:
            clauses.append("started_at < ?")
            params.append(_day_start(date_to) + DAY_SECONDS)
        if search:
            # Finding a phrase and landing on the call that said it is the
            # point of the history view, so search runs over what was spoken.
            clauses.append(
                "EXISTS (SELECT 1 FROM events WHERE events.call_id = calls.call_id "
                f"AND kind IN ({', '.join('?' * len(SPOKEN_KINDS))}) "
                "AND payload_json LIKE ?)"
            )
            params.extend([*SPOKEN_KINDS, f"%{search}%"])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._read() as connection:
            rows = connection.execute(
                f"SELECT * FROM calls {where} "
                "ORDER BY ended_at IS NULL DESC, started_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict:
        with self._read() as connection:
            totals = connection.execute(
                "SELECT COUNT(*) AS calls, "
                "SUM(error IS NOT NULL) AS failed, "
                "SUM(ended_at IS NULL) AS live, "
                "AVG(cost_eur) AS avg_cost, "
                "SUM(cost_eur) AS total_cost FROM calls"
            ).fetchone()
            outcomes = connection.execute(
                "SELECT COALESCE(outcome, 'sin registrar') AS outcome, COUNT(*) AS count "
                "FROM calls GROUP BY 1 ORDER BY count DESC"
            ).fetchall()
            latencies = [
                row[0]
                for row in connection.execute(
                    "SELECT ttfa_seconds FROM calls WHERE ttfa_seconds IS NOT NULL "
                    "ORDER BY ttfa_seconds"
                )
            ]
        return {
            "calls": totals["calls"],
            "live": totals["live"] or 0,
            "failed": totals["failed"] or 0,
            "avg_cost_eur": totals["avg_cost"],
            "total_cost_eur": totals["total_cost"],
            "outcomes": [dict(row) for row in outcomes],
            # The number that decides whether the agent sounds alive.
            "ttfa_p50": _percentile(latencies, 0.50),
            "ttfa_p95": _percentile(latencies, 0.95),
        }

    def get_call(self, call_id: str) -> dict | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM calls WHERE call_id = ?", (call_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_events(self, call_id: str) -> list[dict]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT id, ts, kind, payload_json FROM events WHERE call_id = ? "
                "ORDER BY id",
                (call_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "ts": row["ts"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    index = min(len(values) - 1, round(fraction * (len(values) - 1)))
    return round(values[index], 3)


def _day_start(day: str) -> float:
    return datetime.combine(
        date.fromisoformat(day), clock.min, tzinfo=CLINIC_TIMEZONE
    ).timestamp()
