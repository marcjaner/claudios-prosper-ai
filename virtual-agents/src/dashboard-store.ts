import { chmodSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";

export interface DashboardCall {
  call_id: string;
  started_at: number;
  ended_at: number | null;
  state: string;
  from_number: string | null;
  patient_id: string | null;
  patient_name: string | null;
  insurer: string | null;
  outcome: string | null;
  reason: string | null;
  cost_eur: number | null;
  ttfa_seconds: number | null;
  error: string | null;
  score_overall: number | null;
  score_json: string | null;
  score_error: string | null;
  guardrail_breached: boolean;
  guardrail_reason: string | null;
  guardrail_violations: string | null;
}

export interface DashboardEvent {
  id: number;
  call_id: string;
  ts: number;
  kind: string;
  payload: Record<string, unknown>;
}

export class DashboardStore {
  private readonly database: DatabaseSync;
  private closed = false;

  constructor(path = resolve(".local/dashboard.sqlite")) {
    const directory = dirname(path);
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    chmodSync(directory, 0o700);
    this.database = new DatabaseSync(path);
    chmodSync(path, 0o600);
    this.database.exec(`
      PRAGMA foreign_keys = ON;
      PRAGMA busy_timeout = 5000;
      CREATE TABLE IF NOT EXISTS dashboard_calls (
        call_id TEXT PRIMARY KEY,
        data_json TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS dashboard_events (
        id INTEGER PRIMARY KEY,
        call_id TEXT NOT NULL REFERENCES dashboard_calls(call_id) ON DELETE CASCADE,
        ts REAL NOT NULL,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS dashboard_events_call_id_id
        ON dashboard_events(call_id, id);
    `);
  }

  load(): { calls: DashboardCall[]; events: DashboardEvent[] } {
    const calls = this.database.prepare("SELECT data_json FROM dashboard_calls")
      .all().map((row) => JSON.parse(String(row.data_json)) as DashboardCall);
    const events = this.database.prepare(
      "SELECT id, call_id, ts, kind, payload_json FROM dashboard_events ORDER BY id",
    ).all().map((row) => ({
      id: Number(row.id),
      call_id: String(row.call_id),
      ts: Number(row.ts),
      kind: String(row.kind),
      payload: JSON.parse(String(row.payload_json)) as Record<string, unknown>,
    }));
    return { calls, events };
  }

  resetCall(call: DashboardCall): void {
    if (this.closed) return;
    this.database.exec("BEGIN IMMEDIATE");
    try {
      this.database.prepare("DELETE FROM dashboard_events WHERE call_id = ?").run(call.call_id);
      this.saveCall(call);
      this.database.exec("COMMIT");
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    }
  }

  saveCall(call: DashboardCall): void {
    if (this.closed) return;
    this.database.prepare(`
      INSERT INTO dashboard_calls (call_id, data_json) VALUES (?, ?)
      ON CONFLICT(call_id) DO UPDATE SET data_json = excluded.data_json
    `).run(call.call_id, JSON.stringify(call));
  }

  appendEvent(event: DashboardEvent, maximum: number): void {
    if (this.closed) return;
    this.database.prepare(`
      INSERT INTO dashboard_events (id, call_id, ts, kind, payload_json)
      VALUES (?, ?, ?, ?, ?)
    `).run(event.id, event.call_id, event.ts, event.kind, JSON.stringify(event.payload));
    this.database.prepare(`
      DELETE FROM dashboard_events
      WHERE call_id = ? AND id NOT IN (
        SELECT id FROM dashboard_events WHERE call_id = ? ORDER BY id DESC LIMIT ?
      )
    `).run(event.call_id, event.call_id, maximum);
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.database.close();
  }
}

export function createDashboardStore(path?: string): DashboardStore {
  return new DashboardStore(path);
}
