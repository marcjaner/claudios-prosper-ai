"""Local, read-only call explorer for debugging the voice agent."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _database_path() -> Path:
    url = make_url(os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/agent.db"))
    if url.get_backend_name() != "sqlite" or not url.database:
        raise RuntimeError("Call explorer currently supports SQLite DATABASE_URL only.")
    path = Path(url.database)
    return path if path.is_absolute() else ROOT / path


AGENT_DB = _database_path()
CALLS_DB = ROOT / os.getenv("CALLS_DB", "calls.db")
RECORDINGS_DIR = ROOT / os.getenv("CALL_RECORDINGS_DIR", "outputs/recordings")
SAFE_CALL_ID = re.compile(r"^[A-Za-z0-9_-]+$")
AUDIO_FILES = frozenset({"caller.wav", "agent.wav", "mixed.wav", "stereo.wav"})
CLINIC_TIMEZONE = ZoneInfo("Europe/Madrid")
CALL_LIMIT_SECONDS = 180

app = FastAPI(title="Call Explorer", docs_url=None, redoc_url=None)


def _rows(path: Path, query: str, parameters: tuple[Any, ...] = ()) -> list[dict]:
    if not path.exists():
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query, parameters).fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        connection.close()


def _json(value: str | None, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _timestamp(value: str | float | None, *, naive_timezone=UTC) -> float | None:
    if value is None:
        return None
    if isinstance(value, (float, int)):
        return float(value)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=naive_timezone)
    return parsed.timestamp()


def _recording_metadata(call_id: str) -> dict:
    path = RECORDINGS_DIR / call_id / "metadata.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _all_recording_metadata() -> list[dict]:
    if not RECORDINGS_DIR.is_dir():
        return []
    records = []
    for path in RECORDINGS_DIR.glob("*/metadata.json"):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return records


def _calls() -> list[dict]:
    calls: dict[str, dict] = {}

    for row in _rows(CALLS_DB, "SELECT * FROM calls"):
        calls[row["call_id"]] = {
            **row,
            "sources": ["calls.db"],
            "started_at": _timestamp(row.get("started_at")),
            "ended_at": _timestamp(row.get("ended_at")),
        }

    for row in _rows(AGENT_DB, "SELECT * FROM calls"):
        call = calls.setdefault(row["call_id"], {"call_id": row["call_id"], "sources": []})
        call["sources"].append("agent.db")
        call["agent_status"] = row.get("status")
        call["final_outcome"] = row.get("final_outcome")
        call["workflow_stage"] = row.get("workflow_stage")
        call["workflow_state"] = _json(row.get("workflow_state"), {})
        call["from_number"] = call.get("from_number") or row.get("from_number_hint")
        call.setdefault(
            "started_at",
            _timestamp(row.get("started_at"), naive_timezone=CLINIC_TIMEZONE),
        )
        call.setdefault(
            "ended_at",
            _timestamp(row.get("ended_at"), naive_timezone=CLINIC_TIMEZONE),
        )

    for metadata in _all_recording_metadata():
        call_id = metadata.get("call_id")
        if not call_id:
            continue
        call = calls.setdefault(call_id, {"call_id": call_id, "sources": []})
        call["sources"].append("recording")
        call["recording"] = True
        call["from_number"] = call.get("from_number") or metadata.get("from_number")
        if "calls.db" not in call["sources"]:
            call["started_at"] = _timestamp(metadata.get("connected_at"))
            call["ended_at"] = _timestamp(metadata.get("finished_at"))
        call["recording_outcome"] = metadata.get("outcome")
        call["metrics"] = metadata.get("metrics")

    return sorted(calls.values(), key=lambda call: call.get("started_at") or 0, reverse=True)


def _agent_events(call_id: str) -> list[dict]:
    rows = _rows(
        AGENT_DB,
        "SELECT id, occurred_at, event_type, payload FROM call_events "
        "WHERE call_id = ? ORDER BY id",
        (call_id,),
    )
    return [
        {
            "id": f"agent-{row['id']}",
            "ts": _timestamp(row["occurred_at"]),
            "kind": row["event_type"],
            "payload": _json(row["payload"], {}),
            "source": "agent.db",
        }
        for row in rows
    ]


def _submissions(call_id: str) -> list[dict]:
    rows = _rows(
        AGENT_DB,
        "SELECT id, submitted_at, action, request, response_status, response "
        "FROM submissions WHERE call_id = ? ORDER BY id",
        (call_id,),
    )
    return [
        {
            "id": row["id"],
            "ts": _timestamp(row["submitted_at"]),
            "action": row["action"],
            "request": _json(row["request"], {}),
            "status": row["response_status"],
            "response": _json(row["response"], {}),
        }
        for row in rows
    ]


def _api_observations(call_id: str) -> list[dict]:
    rows = _rows(
        AGENT_DB,
        "SELECT id, occurred_at, endpoint, request, response_status, response, duration_ms "
        "FROM api_observations WHERE call_id = ? ORDER BY id",
        (call_id,),
    )
    return [
        {
            "id": row["id"],
            "ts": _timestamp(row["occurred_at"]),
            "endpoint": row["endpoint"],
            "request": _json(row["request"], {}),
            "status": row["response_status"],
            "response": _json(row["response"], {}),
            "duration_ms": row["duration_ms"],
        }
        for row in rows
    ]


def _timeline(call_id: str) -> list[dict]:
    path = RECORDINGS_DIR / call_id / "timeline.jsonl"
    if not path.is_file():
        return []
    events = []
    try:
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {"event": "invalid_json", "raw": line}
            event["id"] = index
            event["ts"] = _timestamp(event.get("at"))
            events.append(event)
    except OSError:
        return []
    return events


def _latency(timeline: list[dict], metadata: dict) -> dict:
    phases: list[dict] = []
    caller_started = None
    caller_final = None
    agent_started = None
    agent_stopped = None
    tts_requested = None

    def add(category: str, label: str, start: float | None, end: float) -> None:
        if start is None or end <= start:
            return
        phases.append(
            {
                "category": category,
                "label": label,
                "start": round(start, 3),
                "duration": round(end - start, 3),
            }
        )

    for event in timeline:
        elapsed = float(event.get("elapsed_ms") or 0) / 1_000
        kind = event.get("event")
        if kind == "caller_turn_started":
            add("caller_wait", "Waiting for caller", agent_stopped, elapsed)
            agent_stopped = None
            caller_started = elapsed
            caller_final = None
        elif kind == "caller_transcript" and event.get("final"):
            caller_final = elapsed
        elif kind == "caller_turn_stopped":
            add("caller", "Caller utterance", caller_started, caller_final or elapsed)
            add("endpoint", "End-of-turn decision", caller_final, elapsed)
            caller_started = None
            caller_final = None
        elif kind == "llm_response_finished":
            duration = float(event.get("duration_ms") or 0) / 1_000
            add("llm", f"LLM · {event.get('model', 'model')}", elapsed - duration, elapsed)
        elif kind == "tool_call_finished":
            duration = float(event.get("duration_ms") or 0) / 1_000
            add("tool", f"Tool · {event.get('tool', 'unknown')}", elapsed - duration, elapsed)
        elif kind == "tts_requested":
            tts_requested = elapsed
        elif kind == "agent_speech_started":
            add("tts", "TTS first audio", tts_requested, elapsed)
            tts_requested = None
            agent_started = elapsed
        elif kind == "agent_speech_stopped":
            add("agent", "Agent speaking", agent_started, elapsed)
            agent_started = None
            agent_stopped = elapsed

    metrics = metadata.get("metrics") or {}
    duration = float(metrics.get("duration_seconds") or 0)
    if not duration and timeline:
        duration = max(float(event.get("elapsed_ms") or 0) for event in timeline) / 1_000
    scale = max(CALL_LIMIT_SECONDS, ((int(duration) + 9) // 10) * 10)
    totals: dict[str, float] = {}
    for phase in phases:
        category = phase["category"]
        totals[category] = round(totals.get(category, 0) + phase["duration"], 3)
    return {
        "duration": round(duration, 3),
        "limit": CALL_LIMIT_SECONDS,
        "scale": scale,
        "totals": totals,
        "phases": phases,
    }


def _audio(call_id: str) -> list[str]:
    directory = RECORDINGS_DIR / call_id
    return [name for name in AUDIO_FILES if (directory / name).is_file()]


@app.get("/", response_class=HTMLResponse)
def explorer() -> str:
    return PAGE


@app.get("/api/calls")
def list_calls(q: str = "") -> JSONResponse:
    calls = _calls()
    if q:
        needle = q.casefold()
        calls = [call for call in calls if needle in call["call_id"].casefold()]
    return JSONResponse({"calls": calls}, headers={"Cache-Control": "no-store"})


@app.get("/api/calls/{call_id}")
def get_call(call_id: str) -> JSONResponse:
    if not SAFE_CALL_ID.fullmatch(call_id):
        raise HTTPException(status_code=400, detail="Invalid call ID")
    call = next((item for item in _calls() if item["call_id"] == call_id), None)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    timeline = _timeline(call_id)
    metadata = _recording_metadata(call_id)
    return JSONResponse(
        {
            "call": call,
            "agent_events": _agent_events(call_id),
            "submissions": _submissions(call_id),
            "api_observations": _api_observations(call_id),
            "timeline": timeline,
            "latency": _latency(timeline, metadata),
            "audio": _audio(call_id),
            "recording_metadata": metadata,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/calls/{call_id}/audio/{filename}")
def get_audio(call_id: str, filename: str) -> FileResponse:
    if not SAFE_CALL_ID.fullmatch(call_id) or filename not in AUDIO_FILES:
        raise HTTPException(status_code=404, detail="Audio not found")
    path = RECORDINGS_DIR / call_id / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(path, media_type="audio/wav", filename=f"{call_id}-{filename}")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Call Explorer</title>
  <style>
    :root { color-scheme: dark; --bg:#090b0c; --panel:#101416; --line:#273034; --ink:#e9efea; --muted:#7f8b86; --green:#8ef0b5; --amber:#ffc86a; --red:#ff7f82; --blue:#8ac9ff; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; background:radial-gradient(circle at 85% 0,#17201c 0,transparent 32rem),var(--bg); color:var(--ink); font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
    button,input { font:inherit; }
    .shell { display:grid; grid-template-columns:minmax(270px,340px) 1fr; height:100vh; }
    aside { border-right:1px solid var(--line); background:#0c0f10e8; overflow:hidden; display:flex; flex-direction:column; }
    header { padding:20px; border-bottom:1px solid var(--line); }
    h1 { margin:0; font-size:15px; letter-spacing:.16em; text-transform:uppercase; }
    .live { display:inline-flex; align-items:center; gap:7px; margin-top:7px; color:var(--green); font-size:11px; }
    .dot { width:7px; height:7px; border-radius:50%; background:currentColor; box-shadow:0 0 10px currentColor; animation:pulse 1.4s infinite; }
    @keyframes pulse { 50% { opacity:.35; } }
    #search { width:100%; margin-top:16px; padding:10px 12px; color:var(--ink); background:#080a0b; border:1px solid var(--line); border-radius:5px; outline:none; }
    #search:focus { border-color:var(--green); }
    #calls { overflow:auto; padding:8px; }
    .call { display:block; width:100%; padding:11px 12px; border:0; border-left:2px solid transparent; background:transparent; color:inherit; text-align:left; cursor:pointer; }
    .call:hover,.call.active { background:#18201d; border-left-color:var(--green); }
    .call-id { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#cdd6d1; font-size:11px; }
    .call-meta { display:flex; justify-content:space-between; margin-top:5px; color:var(--muted); font-size:10px; }
    main { min-width:0; overflow:auto; }
    .empty { display:grid; min-height:100%; place-items:center; color:var(--muted); }
    .content { max-width:1260px; margin:auto; padding:28px; }
    .title-row { display:flex; align-items:flex-start; gap:16px; flex-wrap:wrap; }
    h2 { margin:0; font-size:17px; overflow-wrap:anywhere; }
    .badge { border:1px solid var(--line); border-radius:999px; padding:3px 8px; color:var(--muted); font-size:10px; text-transform:uppercase; }
    .summary { display:grid; grid-template-columns:repeat(5,minmax(120px,1fr)); gap:1px; margin:20px 0; background:var(--line); border:1px solid var(--line); }
    .metric { padding:12px; background:var(--panel); }
    .label { color:var(--muted); font-size:9px; letter-spacing:.12em; text-transform:uppercase; }
    .value { margin-top:4px; color:var(--ink); overflow-wrap:anywhere; }
    .latency-panel { border:1px solid var(--line); background:var(--panel); padding:14px; overflow:hidden; }
    .latency-totals { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:14px; }
    .latency-total { display:flex; align-items:center; gap:6px; padding:4px 7px; border:1px solid var(--line); border-radius:3px; color:var(--muted); font-size:10px; }
    .swatch { width:7px; height:7px; border-radius:1px; background:var(--phase); }
    .latency-row,.latency-axis { display:grid; grid-template-columns:105px minmax(500px,1fr); gap:10px; align-items:center; }
    .latency-row { margin:5px 0; }
    .latency-label { color:var(--muted); font-size:10px; }
    .latency-track { position:relative; height:15px; background:#080a0b; border-left:1px solid var(--line); border-right:1px solid var(--line); }
    .latency-track::after { content:''; position:absolute; inset:0; background:repeating-linear-gradient(90deg,transparent 0,transparent calc(25% - 1px),#202729 25%); pointer-events:none; }
    .latency-phase { position:absolute; top:2px; z-index:1; height:11px; min-width:2px; border-radius:2px; background:var(--phase); opacity:.82; }
    .latency-phase:hover { opacity:1; box-shadow:0 0 9px color-mix(in srgb,var(--phase) 55%,transparent); }
    .deadline { position:absolute; z-index:2; top:-3px; bottom:-3px; width:1px; background:var(--red); box-shadow:0 0 7px var(--red); }
    .deadline::before { content:'180s'; position:absolute; right:4px; top:-14px; color:var(--red); font-size:8px; }
    .latency-axis { margin-top:3px; }
    .axis-track { position:relative; height:15px; color:#59645f; font-size:8px; }
    .axis-tick { position:absolute; transform:translateX(-50%); }
    .phase-caller { --phase:#74bff3; } .phase-endpoint { --phase:#ff7f82; }
    .phase-llm { --phase:#ffc86a; } .phase-tool { --phase:#ed94c8; }
    .phase-agent { --phase:#8ef0b5; } .phase-tts { --phase:#d6f57a; }
    .phase-caller_wait { --phase:#56615d; }
    .audio-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:10px; margin-bottom:20px; }
    .audio { padding:10px; border:1px solid var(--line); background:var(--panel); }
    audio { width:100%; height:32px; margin-top:7px; }
    section { margin-top:22px; }
    .section-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:9px; }
    h3 { margin:0; color:#b8c3bd; font-size:11px; letter-spacing:.12em; text-transform:uppercase; }
    .count { color:var(--muted); font-size:10px; }
    .stream { border-top:1px solid var(--line); }
    .turn { display:grid; grid-template-columns:76px 1fr; gap:14px; padding:12px 2px; border-bottom:1px solid #202628; }
    .who { color:var(--muted); font-size:10px; text-transform:uppercase; }
    .caller .who { color:var(--blue); } .agent .who { color:var(--green); }
    .text { white-space:pre-wrap; overflow-wrap:anywhere; }
    details { border-bottom:1px solid #202628; }
    summary { display:flex; align-items:center; gap:10px; padding:11px 2px; cursor:pointer; list-style:none; }
    summary::-webkit-details-marker { display:none; }
    .chevron::before { content:'+'; color:var(--muted); } details[open] .chevron::before { content:'−'; }
    .tool-name { color:var(--amber); }
    .status { margin-left:auto; color:var(--muted); font-size:10px; }
    .failed .status { color:var(--red); } .failed { border-left:2px solid var(--red); padding-left:10px; }
    .payloads { display:grid; grid-template-columns:1fr 1fr; gap:10px; padding:0 0 12px 23px; }
    .payload { min-width:0; }
    pre { max-height:430px; margin:5px 0 0; padding:11px; overflow:auto; border:1px solid var(--line); border-radius:4px; background:#070909; color:#b8c3bd; font:11px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; white-space:pre-wrap; overflow-wrap:anywhere; }
    .raw-filter { padding:5px 8px; color:var(--ink); background:#080a0b; border:1px solid var(--line); border-radius:4px; }
    .raw-row summary { padding:8px 2px; }
    .raw-kind { color:#b8c3bd; font-size:11px; }
    .raw-time { width:72px; color:var(--muted); font-size:10px; }
    .source { color:#59645f; font-size:9px; }
    .json-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
    @media (max-width:850px) { .shell { grid-template-columns:1fr; height:auto; } aside { height:42vh; border-right:0; border-bottom:1px solid var(--line); } main { min-height:58vh; } .summary { grid-template-columns:1fr 1fr; } .payloads,.json-grid { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <header>
        <h1>Call Explorer</h1>
        <div class="live"><span class="dot"></span><span id="sync">reading live data</span></div>
        <input id="search" autofocus placeholder="Find call ID…">
      </header>
      <div id="calls"></div>
    </aside>
    <main id="main"><div class="empty">Select a call to inspect its trace.</div></main>
  </div>
  <script>
    const $ = (selector) => document.querySelector(selector);
    const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
    const pretty = (value) => esc(JSON.stringify(value ?? {}, null, 2));
    const stamp = (seconds) => seconds ? new Intl.DateTimeFormat('en-GB',{dateStyle:'short',timeStyle:'medium',timeZone:'Europe/Madrid'}).format(new Date(seconds * 1000)) : '—';
    const clock = (seconds) => seconds ? new Intl.DateTimeFormat('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit',timeZone:'Europe/Madrid'}).format(new Date(seconds * 1000)) : '—';
    let calls = [];
    let selected = decodeURIComponent(location.hash.replace(/^#\/call\//, '')) || null;
    let detailSignature = '';
    let timelineFilter = '';

    function duration(call) {
      if (!call.started_at) return '—';
      const seconds = Math.max(0, (call.ended_at ?? Date.now() / 1000) - call.started_at);
      return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2,'0')}`;
    }

    function renderCalls() {
      const needle = $('#search').value.toLowerCase();
      const visible = calls.filter((call) => call.call_id.toLowerCase().includes(needle));
      $('#calls').innerHTML = visible.map((call) => `
        <button class="call ${call.call_id === selected ? 'active' : ''}" data-call="${esc(call.call_id)}">
          <span class="call-id">${esc(call.call_id)}</span>
          <span class="call-meta"><span>${stamp(call.started_at)}</span><span>${duration(call)}</span></span>
        </button>`).join('') || '<div class="empty">No matching calls.</div>';
      document.querySelectorAll('[data-call]').forEach((button) => button.onclick = () => openCall(button.dataset.call));
    }

    function openCall(callId) {
      selected = callId;
      detailSignature = '';
      location.hash = `#/call/${encodeURIComponent(callId)}`;
      renderCalls();
      refreshDetail();
    }

    function pairTools(events) {
      const result = [];
      const pending = new Map();
      for (const event of events) {
        if (event.kind === 'tool_call') {
          const item = {call:event, result:null};
          const name = event.payload.name;
          if (!pending.has(name)) pending.set(name, []);
          pending.get(name).push(item);
          result.push({type:'tool', item});
        } else if (event.kind === 'tool_result' && pending.get(event.payload.name)?.length) {
          pending.get(event.payload.name).shift().result = event;
        } else if (['caller_text_received','agent_response','agent_follow_up'].includes(event.kind)) {
          result.push({type:'turn', event});
        }
      }
      return result;
    }

    function renderTool(item, index) {
      const call = item.call;
      const output = item.result?.payload?.output;
      const status = output?.status_code ?? (output?.ok === false ? 'error' : item.result ? 'ok' : 'running');
      const failed = output?.ok === false || Number(status) >= 400;
      return `<details class="${failed ? 'failed' : ''}" data-open-key="tool-${index}">
        <summary><span class="chevron"></span><span class="raw-time">${clock(call.ts)}</span><span class="tool-name">${esc(call.payload.name)}</span><span class="status">${esc(status)}</span></summary>
        <div class="payloads">
          <div class="payload"><div class="label">Arguments</div><pre>${pretty(call.payload.arguments)}</pre></div>
          <div class="payload"><div class="label">Response</div><pre>${pretty(output ?? {pending:true})}</pre></div>
        </div>
      </details>`;
    }

    function renderFlow(events) {
      return pairTools(events).map((entry, index) => {
        if (entry.type === 'tool') return renderTool(entry.item, index);
        const event = entry.event;
        const caller = event.kind === 'caller_text_received';
        return `<div class="turn ${caller ? 'caller' : 'agent'}"><div><div class="who">${caller ? 'Caller' : 'Agent'}</div><div class="raw-time">${clock(event.ts)}</div></div><div class="text">${esc(event.payload.text)}</div></div>`;
      }).join('') || '<div class="empty">No agent events stored for this call.</div>';
    }

    const latencyRows = [
      ['caller','Caller speaking'], ['endpoint','Endpointing'], ['llm','LLM'],
      ['tool','Tools'], ['tts','TTS startup'], ['agent','Agent speaking'],
      ['caller_wait','Reply gap'],
    ];

    function renderLatency(latency) {
      if (!latency?.phases?.length) return '<div class="empty">No timing trace for this call.</div>';
      const scale = latency.scale || 180;
      const deadline = Math.min(100, (latency.limit / scale) * 100);
      const totals = latencyRows.filter(([key]) => latency.totals[key] != null).map(([key,label]) => `
        <span class="latency-total phase-${key}"><span class="swatch"></span>${label}<strong>${latency.totals[key].toFixed(1)}s</strong></span>`).join('');
      const rows = latencyRows.map(([key,label]) => {
        const phases = latency.phases.filter((phase) => phase.category === key).map((phase) => {
          const left = (phase.start / scale) * 100;
          const width = (phase.duration / scale) * 100;
          return `<span class="latency-phase phase-${key}" style="left:${left}%;width:${width}%" title="${esc(phase.label)} · ${phase.duration.toFixed(2)}s · starts ${phase.start.toFixed(2)}s"></span>`;
        }).join('');
        return `<div class="latency-row"><span class="latency-label">${label}</span><div class="latency-track">${phases}<span class="deadline" style="left:${deadline}%"></span></div></div>`;
      }).join('');
      const ticks = [...new Set([0,60,120,180,scale])].filter((value) => value <= scale).map((value) => `<span class="axis-tick" style="left:${(value/scale)*100}%">${value}s</span>`).join('');
      return `<div class="latency-panel"><div class="latency-totals">${totals}</div>${rows}<div class="latency-axis"><span></span><div class="axis-track">${ticks}</div></div><div class="count">Cumulative lane totals; overlapping phases are shown on separate lanes.</div></div>`;
    }

    function renderRaw(timeline) {
      const needle = timelineFilter.toLowerCase();
      return timeline.filter((event) => !needle || String(event.event).toLowerCase().includes(needle)).map((event) => {
        const body = Object.fromEntries(Object.entries(event).filter(([key]) => !['id','event','ts'].includes(key)));
        return `<details class="raw-row"><summary><span class="chevron"></span><span class="raw-time">${clock(event.ts)}</span><span class="raw-kind">${esc(event.event)}</span><span class="source">${esc(event.source ?? '')}</span></summary><pre>${pretty(body)}</pre></details>`;
      }).join('') || '<div class="empty">No matching timeline events.</div>';
    }

    function renderDetail(body) {
      const open = new Set([...document.querySelectorAll('details[open][data-open-key]')].map((node) => node.dataset.openKey));
      const call = body.call;
      const metrics = call.metrics ?? body.recording_metadata?.metrics ?? {};
      const audio = body.audio.map((name) => `<div class="audio"><div class="label">${esc(name.replace('.wav',''))}</div><audio controls preload="none" src="/api/calls/${encodeURIComponent(call.call_id)}/audio/${name}"></audio></div>`).join('');
      const submissions = body.submissions.map((item, index) => {
        const failed = Number(item.status) >= 400;
        return `<details class="${failed ? 'failed' : ''}" data-open-key="submission-${index}"><summary><span class="chevron"></span><span class="raw-time">${clock(item.ts)}</span><span class="tool-name">${esc(item.action)}</span><span class="status">${esc(item.status ?? '—')}</span></summary><div class="payloads"><div><div class="label">Request</div><pre>${pretty(item.request)}</pre></div><div><div class="label">Stored response</div><pre>${pretty(item.response)}</pre></div></div></details>`;
      }).join('');
      $('#main').innerHTML = `<div class="content">
        <div class="title-row"><h2>${esc(call.call_id)}</h2>${(call.sources ?? []).map((source) => `<span class="badge">${esc(source)}</span>`).join('')}</div>
        <div class="summary">
          <div class="metric"><div class="label">Started</div><div class="value">${stamp(call.started_at)}</div></div>
          <div class="metric"><div class="label">Duration</div><div class="value">${duration(call)}</div></div>
          <div class="metric"><div class="label">Workflow</div><div class="value">${esc(call.workflow_stage ?? '—')}</div></div>
          <div class="metric"><div class="label">First audio</div><div class="value">${metrics.time_to_first_audio_seconds != null ? esc(metrics.time_to_first_audio_seconds + ' s') : '—'}</div></div>
          <div class="metric"><div class="label">Outcome</div><div class="value">${esc(call.outcome ?? call.final_outcome ?? call.recording_outcome ?? '—')}</div></div>
        </div>
        <section><div class="section-head"><h3>Latency waterfall</h3><span class="count">${body.latency.duration.toFixed(1)}s total · ${body.latency.limit}s limit</span></div>${renderLatency(body.latency)}</section>
        ${audio ? `<section><div class="section-head"><h3>Recordings</h3><span class="count">${body.audio.length} tracks</span></div><div class="audio-grid">${audio}</div></section>` : ''}
        <section><div class="section-head"><h3>Conversation + tool calls</h3><span class="count">${body.agent_events.length} stored events</span></div><div class="stream">${renderFlow(body.agent_events)}</div></section>
        ${submissions ? `<section><div class="section-head"><h3>Tool submissions</h3><span class="count">${body.submissions.length}</span></div><div class="stream">${submissions}</div></section>` : ''}
        <section><div class="section-head"><h3>Raw pipeline timeline</h3><span><input id="raw-filter" class="raw-filter" value="${esc(timelineFilter)}" placeholder="Filter event type…"> <span class="count">${body.timeline.length} events</span></span></div><div id="raw" class="stream">${renderRaw(body.timeline)}</div></section>
        <section><div class="section-head"><h3>Stored state</h3></div><div class="json-grid"><div><div class="label">Call + workflow</div><pre>${pretty(call)}</pre></div><div><div class="label">Recording metadata</div><pre>${pretty(body.recording_metadata)}</pre></div></div></section>
      </div>`;
      document.querySelectorAll('details[data-open-key]').forEach((node) => node.open = open.has(node.dataset.openKey));
      $('#raw-filter').oninput = (event) => { timelineFilter = event.target.value; $('#raw').innerHTML = renderRaw(body.timeline); };
    }

    async function refreshCalls() {
      try {
        const response = await fetch('/api/calls', {cache:'no-store'});
        calls = (await response.json()).calls;
        renderCalls();
        $('#sync').textContent = `live · ${calls.length} calls`;
      } catch (_) { $('#sync').textContent = 'read failed · retrying'; }
    }

    async function refreshDetail() {
      if (!selected) return;
      try {
        const response = await fetch(`/api/calls/${encodeURIComponent(selected)}`, {cache:'no-store'});
        if (!response.ok) throw new Error('not found');
        const body = await response.json();
        const signature = JSON.stringify(body);
        if (signature !== detailSignature) { detailSignature = signature; renderDetail(body); }
      } catch (_) { $('#main').innerHTML = '<div class="empty">Call could not be loaded.</div>'; }
    }

    $('#search').oninput = renderCalls;
    window.addEventListener('hashchange', () => { selected = decodeURIComponent(location.hash.replace(/^#\/call\//,'')) || null; renderCalls(); refreshDetail(); });
    refreshCalls().then(() => { if (selected) refreshDetail(); });
    setInterval(refreshCalls, 1000);
    setInterval(refreshDetail, 1000);
  </script>
</body>
</html>"""


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("CALL_EXPLORER_PORT", "7861")))
