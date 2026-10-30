from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import new_id, utcnow


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  prompt TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'design',
  status TEXT NOT NULL DEFAULT 'queued',
  state_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  type TEXT NOT NULL,
  agent_id TEXT,
  message TEXT,
  data_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  name TEXT NOT NULL,
  path TEXT NOT NULL,
  content_type TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  created_by TEXT,
  created_at TEXT NOT NULL,
  metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id);
CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  run_id TEXT,
  gate TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT,
  summary TEXT,
  details TEXT,
  options_json TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  decision_json TEXT,
  from_agent TEXT,
  from_codename TEXT,
  sla_minutes INTEGER,
  due_at TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_name TEXT,
  role TEXT,
  action TEXT,
  method TEXT,
  path TEXT,
  status INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  from_agent TEXT,
  to_agent TEXT,
  kind TEXT,
  did TEXT,
  doing TEXT,
  needs TEXT,
  artifact TEXT,
  confidence REAL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_run ON agent_messages(run_id, id);
CREATE TABLE IF NOT EXISTS agent_outputs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  agent_id TEXT,
  task_id TEXT,
  stage TEXT,
  model TEXT,
  goal TEXT,
  output_json TEXT NOT NULL,
  text TEXT,
  usage_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_outputs_run ON agent_outputs(run_id, id);
CREATE TABLE IF NOT EXISTS token_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  task_id TEXT,
  agent_id TEXT,
  model TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cached_tokens INTEGER NOT NULL DEFAULT 0,
  reasoning_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_token_usage_run ON token_usage(run_id);
CREATE TABLE IF NOT EXISTS evolution_proposals (
  proposal_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  focus TEXT,
  status TEXT NOT NULL DEFAULT 'proposed',
  proposal_json TEXT NOT NULL,
  evaluation_json TEXT,
  decision_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            # Lightweight migrations for existing local databases.
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(approvals)").fetchall()}
            for column, definition in {
                "from_agent": "TEXT",
                "from_codename": "TEXT",
                "sla_minutes": "INTEGER",
                "due_at": "TEXT",
                "summary": "TEXT",
                "details": "TEXT",
            }.items():
                if column not in columns:
                    self._conn.execute(f"ALTER TABLE approvals ADD COLUMN {column} {definition}")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------ runs
    def create_run(self, run_id: str, prompt: str, mode: str, status: str = "queued") -> dict[str, Any]:
        now = utcnow()
        self._execute(
            "INSERT OR REPLACE INTO runs(run_id,prompt,mode,status,state_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (run_id, prompt, mode, status, None, now, now),
        )
        return self.get_run(run_id) or {}

    def update_run(self, run_id: str, status: str | None = None, state: dict[str, Any] | None = None) -> None:
        now = utcnow()
        if state is not None:
            self._execute(
                "UPDATE runs SET status=COALESCE(?,status), state_json=?, updated_at=? WHERE run_id=?",
                (status, json.dumps(state, default=str), now, run_id),
            )
        else:
            self._execute(
                "UPDATE runs SET status=COALESCE(?,status), updated_at=? WHERE run_id=?",
                (status, now, run_id),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM runs WHERE run_id=?", (run_id,))
        if not rows:
            return None
        row = rows[0]
        if row.get("state_json"):
            try:
                row["state"] = json.loads(row["state_json"])
            except json.JSONDecodeError:
                row["state"] = None
        else:
            row["state"] = None
        row.pop("state_json", None)
        return row

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))
        for row in rows:
            if row.get("state_json"):
                try:
                    row["state"] = json.loads(row["state_json"])
                except json.JSONDecodeError:
                    row["state"] = None
            else:
                row["state"] = None
            row.pop("state_json", None)
        return rows

    # ---------------------------------------------------------------- events
    def add_event(
        self,
        run_id: str,
        event_type: str,
        message: str = "",
        agent_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        cur = self._execute(
            "INSERT INTO events(run_id,type,agent_id,message,data_json,created_at) VALUES(?,?,?,?,?,?)",
            (run_id, event_type, agent_id, message, json.dumps(data or {}, default=str), now),
        )
        return {
            "id": cur.lastrowid,
            "run_id": run_id,
            "type": event_type,
            "agent_id": agent_id,
            "message": message,
            "data": data or {},
            "created_at": now,
        }

    def list_events(self, run_id: str, after_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id ASC LIMIT ?",
            (run_id, after_id, limit),
        )
        for row in rows:
            try:
                row["data"] = json.loads(row["data_json"] or "{}")
            except json.JSONDecodeError:
                row["data"] = {}
            row.pop("data_json", None)
        return rows

    # ------------------------------------------------------------- artifacts
    def add_artifact(
        self,
        run_id: str,
        name: str,
        path: str,
        content_type: str,
        sha256: str,
        created_by: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact_id = new_id("art")
        now = utcnow()
        self._execute(
            "INSERT INTO artifacts(artifact_id,run_id,name,path,content_type,sha256,created_by,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)",
            (artifact_id, run_id, name, path, content_type, sha256, created_by, now, json.dumps(metadata or {}, default=str)),
        )
        return {
            "artifact_id": artifact_id,
            "run_id": run_id,
            "name": name,
            "path": path,
            "content_type": content_type,
            "sha256": sha256,
            "created_by": created_by,
            "created_at": now,
            "metadata": metadata or {},
        }

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at ASC", (run_id,))
        for row in rows:
            try:
                row["metadata"] = json.loads(row.get("metadata_json") or "{}")
            except json.JSONDecodeError:
                row["metadata"] = {}
            row.pop("metadata_json", None)
        return rows

    def get_artifact(self, run_id: str, name: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM artifacts WHERE run_id=? AND name=? ORDER BY created_at DESC LIMIT 1", (run_id, name))
        return rows[0] if rows else None

    # -------------------------------------------------------------- approvals
    def create_approval(
        self,
        run_id: str | None,
        gate: str,
        title: str,
        message: str = "",
        options: list[dict[str, Any]] | None = None,
        from_agent: str | None = None,
        from_codename: str | None = None,
        sla_minutes: int | None = None,
        summary: str = "",
        details: str = "",
    ) -> dict[str, Any]:
        approval_id = new_id("appr")
        now = utcnow()
        due_at = None
        if sla_minutes:
            due_at = (datetime.now(timezone.utc) + timedelta(minutes=int(sla_minutes))).isoformat()
        self._execute(
            "INSERT INTO approvals(approval_id,run_id,gate,title,message,summary,details,options_json,status,from_agent,from_codename,sla_minutes,due_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                approval_id,
                run_id,
                gate,
                title,
                message,
                summary or message,
                details or message,
                json.dumps(options or [], default=str),
                "pending",
                from_agent,
                from_codename,
                sla_minutes,
                due_at,
                now,
            ),
        )
        return self.get_approval(approval_id) or {}

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM approvals WHERE approval_id=?", (approval_id,))
        if not rows:
            return None
        row = rows[0]
        try:
            row["options"] = json.loads(row.get("options_json") or "[]")
        except json.JSONDecodeError:
            row["options"] = []
        try:
            row["decision"] = json.loads(row["decision_json"]) if row.get("decision_json") else None
        except json.JSONDecodeError:
            row["decision"] = None
        row.pop("options_json", None)
        row.pop("decision_json", None)
        return row

    def list_approvals(self, status: str | None = "pending", limit: int = 100) -> list[dict[str, Any]]:
        if status:
            rows = self._query("SELECT * FROM approvals WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit))
        else:
            rows = self._query("SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (limit,))
        for row in rows:
            try:
                row["options"] = json.loads(row.get("options_json") or "[]")
            except json.JSONDecodeError:
                row["options"] = []
            try:
                row["decision"] = json.loads(row["decision_json"]) if row.get("decision_json") else None
            except json.JSONDecodeError:
                row["decision"] = None
            row.pop("options_json", None)
            row.pop("decision_json", None)
        return rows

    def dismiss_stale_approvals(self) -> int:
        """Close approvals whose run/proposal is already terminal so they cannot pollute the inbox."""
        pending = self.list_approvals("pending", 1000)
        dismissed = 0
        terminal_runs = {"completed", "failed", "stopped", "escalated"}
        for approval in pending:
            run_id = approval.get("run_id")
            if not run_id:
                continue
            if approval.get("gate") == "evolution_approval":
                proposal = self.get_evolution(run_id)
                if proposal and proposal.get("status") not in {"pending_approval", "evaluating", "canary"}:
                    self.decide_approval(
                        approval["approval_id"],
                        {"decision": "dismiss", "notes": "Proposal was already terminal."},
                        status="dismissed",
                    )
                    dismissed += 1
                continue
            run = self.get_run(run_id)
            if run and run.get("status") in terminal_runs:
                self.decide_approval(
                    approval["approval_id"],
                    {"decision": "dismiss", "notes": "Run was already terminal."},
                    status="dismissed",
                )
                dismissed += 1
        return dismissed

    def decide_approval(self, approval_id: str, decision: dict[str, Any], status: str | None = None) -> None:
        final_status = status or ("approved" if decision.get("decision") in {"approve", "override"} else "rejected")
        self._execute(
            "UPDATE approvals SET status=?, decision_json=?, decided_at=? WHERE approval_id=?",
            (final_status, json.dumps(decision, default=str), utcnow(), approval_id),
        )

    def pending_approval_for_run(self, run_id: str, gate: str | None = None) -> dict[str, Any] | None:
        if gate:
            rows = self._query(
                "SELECT * FROM approvals WHERE run_id=? AND gate=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
                (run_id, gate),
            )
        else:
            rows = self._query(
                "SELECT * FROM approvals WHERE run_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            )
        if not rows:
            return None
        return self.get_approval(rows[0]["approval_id"])

    # ---------------------------------------------------------------- audit
    def add_audit(
        self,
        user_name: str,
        role: str,
        action: str,
        method: str,
        path: str,
        status: int,
    ) -> None:
        self._execute(
            "INSERT INTO audit_log(user_name,role,action,method,path,status,created_at) VALUES(?,?,?,?,?,?,?)",
            (user_name, role, action, method, path, status, utcnow()),
        )

    def list_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))

    # ---------------------------------------------------------- agent feed
    def add_agent_message(
        self,
        run_id: str,
        from_agent: str | None,
        to_agent: str | None,
        kind: str,
        did: str = "",
        doing: str = "",
        needs: str = "",
        artifact: str | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        cur = self._execute(
            "INSERT INTO agent_messages(run_id,from_agent,to_agent,kind,did,doing,needs,artifact,confidence,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (run_id, from_agent, to_agent, kind, did, doing, needs, artifact, confidence, now),
        )
        return {
            "id": cur.lastrowid,
            "run_id": run_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "kind": kind,
            "did": did,
            "doing": doing,
            "needs": needs,
            "artifact": artifact,
            "confidence": confidence,
            "created_at": now,
        }

    def list_agent_messages(self, run_id: str, after_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        return self._query(
            "SELECT * FROM agent_messages WHERE run_id=? AND id>? ORDER BY id ASC LIMIT ?",
            (run_id, after_id, limit),
        )

    def delete_run(self, run_id: str) -> dict[str, Any]:
        """Delete a run and all of its saved relational data.

        Filesystem artifacts are returned before deletion so the caller can clean
        them up. This is intentionally explicit: callers must confirm the action.
        """
        artifacts = self.list_artifacts(run_id)
        artifact_paths = [a.get("path") for a in artifacts if a.get("path")]
        counts: dict[str, int] = {}
        with self._lock:
            for table in (
                "agent_outputs",
                "agent_messages",
                "events",
                "approvals",
                "artifacts",
                "token_usage",
                "runs",
            ):
                cur = self._conn.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
                counts[table] = int(cur.rowcount or 0)
            self._conn.commit()
        return {"run_id": run_id, "deleted": counts, "artifact_paths": artifact_paths}

    # ------------------------------------------------------- agent outputs
    def add_agent_output(
        self,
        run_id: str,
        agent_id: str | None,
        *,
        task_id: str | None = None,
        stage: str | None = None,
        model: str | None = None,
        goal: str = "",
        output: Any = None,
        text: str = "",
        usage: dict[str, Any] | None = None,
    ) -> int:
        cur = self._execute(
            "INSERT INTO agent_outputs(run_id,agent_id,task_id,stage,model,goal,output_json,text,usage_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                agent_id,
                task_id,
                stage,
                model,
                goal,
                json.dumps(output, ensure_ascii=False, default=str),
                text,
                json.dumps(usage or {}, ensure_ascii=False, default=str),
                utcnow(),
            ),
        )
        return int(cur.lastrowid or 0)

    def list_agent_outputs(self, run_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM agent_outputs WHERE run_id=? ORDER BY id ASC LIMIT ?",
            (run_id, limit),
        )
        for row in rows:
            try:
                row["output"] = json.loads(row.get("output_json") or "null")
            except json.JSONDecodeError:
                row["output"] = None
            try:
                row["usage"] = json.loads(row.get("usage_json") or "{}")
            except json.JSONDecodeError:
                row["usage"] = {}
            row.pop("output_json", None)
            row.pop("usage_json", None)
        return rows

    # ---------------------------------------------------------------- budget
    def record_usage(
        self,
        run_id: str | None,
        task_id: str | None,
        agent_id: str | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        self._execute(
            "INSERT INTO token_usage(run_id,task_id,agent_id,model,input_tokens,output_tokens,cached_tokens,reasoning_tokens,cost_usd,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (run_id, task_id, agent_id, model, input_tokens, output_tokens, cached_tokens, reasoning_tokens, cost_usd, utcnow()),
        )

    def budget_summary(self, monthly_total: int | None = None) -> dict[str, Any]:
        rows = self._query("SELECT * FROM token_usage")
        input_tokens = sum(int(r["input_tokens"]) for r in rows)
        output_tokens = sum(int(r["output_tokens"]) for r in rows)
        cached_tokens = sum(int(r["cached_tokens"]) for r in rows)
        reasoning_tokens = sum(int(r["reasoning_tokens"]) for r in rows)
        cost = sum(float(r["cost_usd"]) for r in rows)
        used = input_tokens + output_tokens + reasoning_tokens
        total = monthly_total or 100_000_000
        by_agent: dict[str, int] = {}
        by_model: dict[str, int] = {}
        for row in rows:
            tokens = int(row["input_tokens"]) + int(row["output_tokens"]) + int(row["reasoning_tokens"])
            by_agent[str(row["agent_id"] or "unknown")] = by_agent.get(str(row["agent_id"] or "unknown"), 0) + tokens
            by_model[str(row["model"] or "unknown")] = by_model.get(str(row["model"] or "unknown"), 0) + tokens
        return {
            "used_tokens": used,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "reasoning_tokens": reasoning_tokens,
            "monthly_total_tokens": total,
            "remaining_tokens": max(0, total - used),
            "used_pct": round((used / total) * 100, 2) if total else 0,
            "cost_usd": round(cost, 4),
            "by_agent": dict(sorted(by_agent.items(), key=lambda item: item[1], reverse=True)),
            "by_model": dict(sorted(by_model.items(), key=lambda item: item[1], reverse=True)),
        }

    # --------------------------------------------------------------- cleanup
    def purge_mock_data(self) -> dict[str, Any]:
        """Remove mock/demo runs and their events, approvals, artifacts, and token rows."""
        rows = self._query("SELECT run_id, state_json FROM runs")
        mock_ids: list[str] = []
        for row in rows:
            try:
                state = json.loads(row.get("state_json") or "{}")
            except json.JSONDecodeError:
                state = {}
            if isinstance(state, dict) and state.get("backend") == "mock":
                mock_ids.append(str(row["run_id"]))
        counts = {
            "runs": 0,
            "events": 0,
            "artifacts": 0,
            "approvals": 0,
            "agent_messages": 0,
            "token_rows": 0,
        }
        if mock_ids:
            placeholders = ",".join("?" for _ in mock_ids)
            counts["events"] = len(self._query(f"SELECT id FROM events WHERE run_id IN ({placeholders})", tuple(mock_ids)))
            counts["artifacts"] = len(self._query(f"SELECT artifact_id FROM artifacts WHERE run_id IN ({placeholders})", tuple(mock_ids)))
            counts["approvals"] = len(self._query(f"SELECT approval_id FROM approvals WHERE run_id IN ({placeholders})", tuple(mock_ids)))
            counts["agent_messages"] = len(self._query(f"SELECT id FROM agent_messages WHERE run_id IN ({placeholders})", tuple(mock_ids)))
            counts["token_rows"] = len(self._query(f"SELECT id FROM token_usage WHERE run_id IN ({placeholders})", tuple(mock_ids)))
            counts["runs"] = len(mock_ids)
            self._execute(f"DELETE FROM events WHERE run_id IN ({placeholders})", tuple(mock_ids))
            self._execute(f"DELETE FROM artifacts WHERE run_id IN ({placeholders})", tuple(mock_ids))
            self._execute(f"DELETE FROM approvals WHERE run_id IN ({placeholders})", tuple(mock_ids))
            self._execute(f"DELETE FROM agent_messages WHERE run_id IN ({placeholders})", tuple(mock_ids))
            self._execute(f"DELETE FROM token_usage WHERE run_id IN ({placeholders})", tuple(mock_ids))
            self._execute(f"DELETE FROM runs WHERE run_id IN ({placeholders})", tuple(mock_ids))
        # Legacy mock provider rows may have survived without a runs row.
        legacy = self._query("SELECT id FROM token_usage WHERE model LIKE 'mock/%'")
        if legacy:
            counts["token_rows"] += len(legacy)
            self._execute("DELETE FROM token_usage WHERE model LIKE 'mock/%'")
        return {"purged_run_ids": mock_ids, **counts}

    # ------------------------------------------------------------- evolution
    def create_evolution(self, proposal: dict[str, Any], focus: str = "quality") -> dict[str, Any]:
        proposal_id = str(proposal.get("proposal_id") or new_id("evo"))
        now = utcnow()
        self._execute(
            "INSERT OR REPLACE INTO evolution_proposals(proposal_id,title,focus,status,proposal_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (proposal_id, str(proposal.get("title", "Evolution proposal")), focus, str(proposal.get("status", "proposed")), json.dumps(proposal, default=str), now, now),
        )
        return self.get_evolution(proposal_id) or {}

    def get_evolution(self, proposal_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM evolution_proposals WHERE proposal_id=?", (proposal_id,))
        if not rows:
            return None
        row = rows[0]
        row["proposal"] = json.loads(row["proposal_json"])
        row["evaluation"] = json.loads(row["evaluation_json"]) if row.get("evaluation_json") else None
        row["decision"] = json.loads(row["decision_json"]) if row.get("decision_json") else None
        row.pop("proposal_json", None)
        row.pop("evaluation_json", None)
        row.pop("decision_json", None)
        return row

    def list_evolutions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM evolution_proposals ORDER BY created_at DESC LIMIT ?", (limit,))
        for row in rows:
            row["proposal"] = json.loads(row["proposal_json"])
            row["evaluation"] = json.loads(row["evaluation_json"]) if row.get("evaluation_json") else None
            row["decision"] = json.loads(row["decision_json"]) if row.get("decision_json") else None
            row.pop("proposal_json", None)
            row.pop("evaluation_json", None)
            row.pop("decision_json", None)
        return rows

    def update_evolution(
        self,
        proposal_id: str,
        status: str | None = None,
        evaluation: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
        proposal: dict[str, Any] | None = None,
    ) -> None:
        current = self.get_evolution(proposal_id)
        if not current:
            return
        new_status = status or current["status"]
        new_eval = json.dumps(evaluation, default=str) if evaluation is not None else (json.dumps(current["evaluation"], default=str) if current["evaluation"] else None)
        new_decision = json.dumps(decision, default=str) if decision is not None else (json.dumps(current["decision"], default=str) if current["decision"] else None)
        new_proposal = json.dumps(proposal, default=str) if proposal is not None else json.dumps(current["proposal"], default=str)
        self._execute(
            "UPDATE evolution_proposals SET status=?, evaluation_json=?, decision_json=?, proposal_json=?, updated_at=? WHERE proposal_id=?",
            (new_status, new_eval, new_decision, new_proposal, utcnow(), proposal_id),
        )
