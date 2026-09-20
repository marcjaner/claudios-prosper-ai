import json
import sqlite3
import time
from bisect import bisect_right
from datetime import date, datetime, timedelta
from datetime import time as clock
from math import ceil
from pathlib import Path
from zoneinfo import ZoneInfo

from .bus import CallUpdate, Event

CLINIC_TIMEZONE = ZoneInfo("Europe/Madrid")
DAY_SECONDS = 24 * 60 * 60

# Interim transcripts are for watching the transcript correct itself live. The
# finals are the transcript, and skipping the partials drops most of the writes.
BROADCAST_ONLY = frozenset({"stt_partial"})

# A call either changed the diary, closed with a reasoned record, or left
# nothing behind. Only the last always fails the case, so they are counted
# apart: a refusal with the right reason scores exactly like a booking.
WROTE = ("BOOK", "RESCHEDULE", "CANCEL", "REGISTER")
CLOSED = ("NO_ACTION", "ESCALATE")
SUCCESSFUL = WROTE + ("NO_ACTION",)

# Bar widths that read as time: a minute, five, a quarter, an hour, six, a day.
BUCKET_LADDER = (60, 300, 900, 3600, 6 * 3600, DAY_SECONDS)
BUCKET_TARGET = 40

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
    "score_overall",
    "score_json",
    "score_error",
    "guardrail_breached",
    "guardrail_reason",
    "guardrail_violations",
)

COLUMN_AFFINITY = {"score_overall": "REAL"}

# Reference prices supplied for the hackathon cost view. USD is used because
# the provider prices are quoted in USD; the existing cost_eur column remains
# populated for backwards compatibility.
LLM_INPUT_USD_PER_MILLION = 0.20
LLM_OUTPUT_USD_PER_MILLION = 1.20
DEEPGRAM_STT_USD_PER_MINUTE = 0.0043
CARTESIA_TTS_USD_PER_MINUTE = 0.033
JEV_INPUT_USD_PER_MILLION = 0.042
CHARS_PER_TOKEN = 4
SPOKEN_CHARS_PER_SECOND = 15

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
    verdict_json TEXT,
    score_overall REAL,
    score_json TEXT,
    score_error TEXT
    ,guardrail_breached INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_call_idx ON events(call_id, id);

CREATE TABLE IF NOT EXISTS call_pricing (
    call_id TEXT PRIMARY KEY REFERENCES calls(call_id),
    llm_usd REAL NOT NULL DEFAULT 0,
    stt_usd REAL NOT NULL DEFAULT 0,
    tts_usd REAL NOT NULL DEFAULT 0,
    jev_usd REAL NOT NULL DEFAULT 0,
    total_usd REAL NOT NULL DEFAULT 0,
    llm_input_tokens REAL NOT NULL DEFAULT 0,
    llm_output_tokens REAL NOT NULL DEFAULT 0,
    stt_audio_minutes REAL NOT NULL DEFAULT 0,
    tts_audio_minutes REAL NOT NULL DEFAULT 0,
    jev_input_tokens REAL NOT NULL DEFAULT 0,
    estimated_at REAL NOT NULL
);
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
                affinity = COLUMN_AFFINITY.get(name, "TEXT")
                self._writer.execute(
                    f"ALTER TABLE calls ADD COLUMN {name} {affinity}"
                )

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
            for call_id in {
                item.call_id for item in batch if isinstance(item, (Event, CallUpdate))
            }:
                self._recalculate_pricing(call_id)
        except Exception:
            self._writer.execute("ROLLBACK")
            raise

    def _recalculate_pricing(self, call_id: str) -> None:
        events = self._writer.execute(
            "SELECT kind, payload_json FROM events WHERE call_id = ?", (call_id,)
        ).fetchall()
        llm_input = llm_output = 0
        stt_chars = tts_chars = 0
        for kind, payload_json in events:
            payload = json.loads(payload_json)
            if kind == "llm_call":
                llm_input += payload.get("prompt_tokens") or payload.get("prompt_chars", 0) / CHARS_PER_TOKEN
                llm_output += payload.get("completion_tokens") or 0
            elif kind == "stt_final":
                stt_chars += len(payload.get("text", ""))
            elif kind == "tts":
                tts_chars += len(payload.get("text", ""))

        call = self._writer.execute(
            "SELECT score_overall, score_json FROM calls WHERE call_id = ?", (call_id,)
        ).fetchone()
        spoken_chars = stt_chars + tts_chars
        jev_tokens = 0
        if call and call[0] is not None:
            jev_tokens = spoken_chars / CHARS_PER_TOKEN
            if call[1]:
                try:
                    jev_tokens += len(json.loads(call[1]).get("guardrail_violations", [])) * spoken_chars / CHARS_PER_TOKEN
                except (TypeError, json.JSONDecodeError):
                    pass

        llm_usd = llm_input / 1_000_000 * LLM_INPUT_USD_PER_MILLION + llm_output / 1_000_000 * LLM_OUTPUT_USD_PER_MILLION
        stt_minutes = stt_chars / SPOKEN_CHARS_PER_SECOND / 60
        tts_minutes = tts_chars / SPOKEN_CHARS_PER_SECOND / 60
        stt_usd = stt_minutes * DEEPGRAM_STT_USD_PER_MINUTE
        tts_usd = tts_minutes * CARTESIA_TTS_USD_PER_MINUTE
        jev_usd = jev_tokens / 1_000_000 * JEV_INPUT_USD_PER_MILLION
        total_usd = llm_usd + stt_usd + tts_usd + jev_usd
        self._writer.execute(
            "INSERT INTO call_pricing (call_id, llm_usd, stt_usd, tts_usd, jev_usd, total_usd, "
            "llm_input_tokens, llm_output_tokens, stt_audio_minutes, tts_audio_minutes, "
            "jev_input_tokens, estimated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(call_id) DO UPDATE SET llm_usd=excluded.llm_usd, stt_usd=excluded.stt_usd, "
            "tts_usd=excluded.tts_usd, jev_usd=excluded.jev_usd, total_usd=excluded.total_usd, "
            "llm_input_tokens=excluded.llm_input_tokens, llm_output_tokens=excluded.llm_output_tokens, "
            "stt_audio_minutes=excluded.stt_audio_minutes, tts_audio_minutes=excluded.tts_audio_minutes, "
            "jev_input_tokens=excluded.jev_input_tokens, estimated_at=excluded.estimated_at",
            (call_id, llm_usd, stt_usd, tts_usd, jev_usd, total_usd, llm_input, llm_output,
             stt_minutes, tts_minutes, jev_tokens, time.time()),
        )
        self._writer.execute("UPDATE calls SET cost_eur = ? WHERE call_id = ?", (total_usd * 0.92, call_id))

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

    def _where(
        self,
        outcome: str | None = None,
        reason: str | None = None,
        search: str | None = None,
        ended_only: bool = False,
        name: str | None = None,
        insurer: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        started_after: float | None = None,
        started_before: float | None = None,
    ) -> tuple[str, list]:
        """The filter the table and the histogram share, so they cannot drift."""
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
            params.append(_day_start(date_to, offset=1))
        if started_after is not None:
            clauses.append("started_at >= ?")
            params.append(started_after)
        if started_before is not None:
            clauses.append("started_at < ?")
            params.append(started_before)
        if search:
            # Finding a phrase and landing on the call that said it is the
            # point of the history view, so search runs over what was spoken.
            clauses.append(
                "EXISTS (SELECT 1 FROM events WHERE events.call_id = calls.call_id "
                f"AND kind IN ({', '.join('?' * len(SPOKEN_KINDS))}) "
                "AND payload_json LIKE ?)"
            )
            params.extend([*SPOKEN_KINDS, f"%{search}%"])

        return f"WHERE {' AND '.join(clauses)}" if clauses else "", params

    def list_calls(self, limit: int = 200, offset: int = 0, **filters) -> list[dict]:
        where, params = self._where(**filters)
        with self._read() as connection:
            rows = connection.execute(
                f"SELECT * FROM calls {where} "
                "ORDER BY ended_at IS NULL DESC, started_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def histogram(self, buckets: int = BUCKET_TARGET, **filters) -> dict:
        """Calls over time, split by what the call left behind.

        A write and a reasoned close both score; leaving no record at all is
        the only outcome that always fails, so the three are counted apart.
        """
        where, params = self._where(**filters)
        with self._read() as connection:
            span = connection.execute(
                f"SELECT MIN(started_at) AS first, MAX(started_at) AS last "
                f"FROM calls {where}",
                params,
            ).fetchone()
            first = (
                _day_start(filters["date_from"])
                if filters.get("date_from") else span["first"]
            )
            last = (
                _day_start(filters["date_to"], offset=1) - 0.001
                if filters.get("date_to") else span["last"]
            )
            if filters.get("started_after") is not None:
                first = (
                    max(first, filters["started_after"])
                    if filters.get("date_from") else filters["started_after"]
                )
            if filters.get("started_before") is not None:
                end = filters["started_before"] - 0.001
                last = min(last, end) if filters.get("date_to") else end
            if first is None or last is None or first > last:
                return {"bucket_seconds": BUCKET_LADDER[0], "buckets": []}

            bucket = _pick_bucket(last - first, buckets)
            if filters.get("date_from") or filters.get("date_to"):
                bucket = max(DAY_SECONDS, bucket)
            starts = _bucket_starts(first, last, bucket)
            connection.create_function(
                "histogram_bucket", 1, lambda ts: starts[bisect_right(starts, ts) - 1]
            )
            action_columns = ", ".join(
                f"SUM(COALESCE(outcome, '') = ?) AS {action}" for action in WROTE + CLOSED
            )
            rows = connection.execute(
                f"SELECT histogram_bucket(started_at) AS bucket, COUNT(*) AS total, "
                f"{action_columns}, "
                # COALESCE, not a bare IN: SQL's three-valued logic makes
                # `NULL IN (...)` itself NULL, and a bucket of calls that
                # submitted nothing would sum to NULL instead of zero.
                f"SUM(COALESCE(outcome, '') IN ({_marks(WROTE)})) AS wrote, "
                f"SUM(COALESCE(outcome, '') IN ({_marks(CLOSED)})) AS closed, "
                f"SUM(COALESCE(outcome, '') NOT IN ({_marks(WROTE + CLOSED)})) AS absent "
                f"FROM calls {where} GROUP BY bucket ORDER BY bucket",
                (*WROTE, *CLOSED, *WROTE, *CLOSED, *WROTE, *CLOSED, *params),
            ).fetchall()
        counts = {row["bucket"]: dict(row) for row in rows}
        result = []
        for start in starts:
            values = counts.get(start, {})
            result.append({
                "bucket": start,
                **{key: values.get(key, 0) for key in ("total", "wrote", "closed", "absent")},
                "outcomes": {action: values.get(action, 0) for action in WROTE + CLOSED},
            })
        return {
            "bucket_seconds": bucket,
            "range_start": first,
            "range_end": last,
            "buckets": result,
        }

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
            completed = connection.execute(
                "SELECT COUNT(*) FROM calls WHERE ended_at IS NOT NULL"
            ).fetchone()[0]
            successful = connection.execute(
                f"SELECT COUNT(*) FROM calls WHERE ended_at IS NOT NULL "
                f"AND outcome IN ({_marks(SUCCESSFUL)})",
                SUCCESSFUL,
            ).fetchone()[0]
            bookings = connection.execute(
                "SELECT COUNT(*) FROM calls WHERE ended_at IS NOT NULL AND outcome = ?",
                ("BOOK",),
            ).fetchone()[0]
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
            "success_pct": _percentage(successful, completed),
            "bookings_pct": _percentage(bookings, completed),
            "outcomes": [dict(row) for row in outcomes],
            # The number that decides whether the agent sounds alive.
            "ttfa_p50": _percentile(latencies, 0.50),
            "ttfa_p95": _percentile(latencies, 0.95),
        }

    def save_score(
        self, call_id: str, result: dict | None = None, error: str | None = None
    ) -> None:
        if result is not None:
            self._writer.execute(
                "UPDATE calls SET score_overall = ?, score_json = ?, "
                "score_error = NULL WHERE call_id = ?",
                (result["overall"], json.dumps(result, ensure_ascii=False), call_id),
            )
        else:
            self._writer.execute(
                "UPDATE calls SET score_overall = NULL, score_json = NULL, "
                "score_error = ? WHERE call_id = ?",
                (error, call_id),
            )

    def get_call(self, call_id: str) -> dict | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM calls WHERE call_id = ?", (call_id,)
            ).fetchone()
            pricing = connection.execute(
                "SELECT * FROM call_pricing WHERE call_id = ?", (call_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["pricing"] = dict(pricing) if pricing else None
        return result

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


def _percentage(part: int, total: int) -> float:
    return round(part * 100 / total, 1) if total else 0.0


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    index = min(len(values) - 1, round(fraction * (len(values) - 1)))
    return round(values[index], 3)


def _day_start(day: str, offset: int = 0) -> float:
    return datetime.combine(
        date.fromisoformat(day) + timedelta(days=offset),
        clock.min,
        tzinfo=CLINIC_TIMEZONE,
    ).timestamp()


def _marks(values: tuple[str, ...]) -> str:
    return ", ".join("?" * len(values))


def _bucket_starts(first: float, last: float, bucket: int) -> list[int]:
    if bucket < DAY_SECONDS:
        return list(range(int(first // bucket) * bucket, int(last) + 1, bucket))
    start = datetime.fromtimestamp(first, CLINIC_TIMEZONE).date()
    end = datetime.fromtimestamp(last, CLINIC_TIMEZONE).date()
    return [
        int(_day_start((start + timedelta(days=offset)).isoformat()))
        for offset in range(0, (end - start).days + 1, bucket // DAY_SECONDS)
    ]


def _pick_bucket(span_seconds: float, target: int) -> int:
    for bucket in (*BUCKET_LADDER, 7 * DAY_SECONDS, 30 * DAY_SECONDS):
        if span_seconds / bucket <= target:
            return bucket
    return ceil(span_seconds / (max(1, target) * DAY_SECONDS)) * DAY_SECONDS
