#!/usr/bin/env python3
"""Local session index and a metadata-only OTLP receiver for Codex."""

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from runtime_paths import CODEX_HOME, LOCAL_DASHBOARD_OFF
from site_metrics import sync_forever


ASSETS = Path(__file__).resolve().parent / "dashboard"
ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
MODEL = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}")
MAX_TURNS = 24
MAX_SESSIONS = 120
MAX_OTLP_BYTES = 12 * 1024 * 1024


def model_name(value):
    return value if isinstance(value, str) and MODEL.fullmatch(value) else None


def identifier(value):
    return value.lower() if isinstance(value, str) and ID.fullmatch(value) else None


def integer(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        result = int(value)
    except ValueError:
        return None
    return result if 0 <= result <= (2**63 - 1) else None


def now_ms():
    return time.time_ns() // 1_000_000


def read_db(path):
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.8)
    db.row_factory = sqlite3.Row
    return db


def newest_database(codex_home, prefix):
    files = []
    for path in codex_home.glob(f"{prefix}_*.sqlite"):
        version = path.stem[len(prefix) + 1:]
        if version.isdigit():
            files.append((int(version), path))
    return max(files)[1] if files else None


def attributes(record):
    values = {}
    for entry in record.get("attributes", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("value"), dict):
            continue
        value = entry["value"]
        for field in ("stringValue", "intValue"):
            if field in value:
                values[entry.get("key")] = value[field]
                break
    return values


def timestamp(record, fields):
    stamp = fields.get("event.timestamp")
    if isinstance(stamp, str):
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo:
                return int(parsed.timestamp() * 1000)
        except (ValueError, OverflowError, OSError):
            pass
    ns = integer(record.get("timeUnixNano")) or integer(record.get("observedTimeUnixNano"))
    return ns // 1_000_000 if ns else None


def otlp_records(payload, resource_key, scope_key, records_key):
    for resource in payload.get(resource_key, []):
        for scope in resource.get(scope_key, []):
            yield from scope.get(records_key, [])


def log_measurements(payload):
    for record in otlp_records(payload, "resourceLogs", "scopeLogs", "logRecords"):
        fields = attributes(record)
        name = fields.get("event.name")
        if name not in ("codex.turn_ttft", "codex.sse_event"):
            continue
        thread = identifier(fields.get("conversation.id") or fields.get("thread.id"))
        at = timestamp(record, fields)
        if not thread or not at:
            continue
        base = {
            "thread": thread,
            "turn": identifier(fields.get("turn.id") or fields.get("turn_id")),
            "at": at,
            "model": model_name(fields.get("model")),
        }
        if name == "codex.turn_ttft":
            delay = integer(fields.get("duration_ms"))
            if delay is not None:
                yield dict(base, kind="ttft", delay=delay)
        elif fields.get("event.kind") == "response.completed":
            tokens = integer(fields.get("output_token_count"))
            if tokens:
                yield dict(
                    base, kind="stream", tokens=tokens,
                    delay=integer(fields.get("ttft_ms")),
                    effort=model_name(fields.get("model_reasoning_effort")),
                )


def trace_measurements(payload):
    for span in otlp_records(payload, "resourceSpans", "scopeSpans", "spans"):
        name = span.get("name")
        if name not in ("codex.sampling", "receiving_stream"):
            continue
        start_ns = integer(span.get("startTimeUnixNano"))
        end_ns = integer(span.get("endTimeUnixNano"))
        trace = span.get("traceId")
        parent = span.get("parentSpanId")
        if not (start_ns and end_ns and end_ns > start_ns):
            continue
        if not (isinstance(trace, str) and re.fullmatch(r"[0-9a-fA-F]{32}", trace)
                and isinstance(parent, str) and re.fullmatch(r"[0-9a-fA-F]{16}", parent)):
            continue
        fields = attributes(span)
        thread = identifier(fields.get("conversation.id") or fields.get("thread.id"))
        turn = identifier(fields.get("turn.id") or fields.get("turn_id"))
        if name == "codex.sampling" and not (thread and turn):
            continue
        yield {
            "kind": "sampling" if name == "codex.sampling" else "receiving",
            "thread": thread,
            "turn": turn,
            "at": end_ns // 1_000_000,
            "start": start_ns // 1_000_000,
            "call": trace.lower() + parent.lower(),
        }


def in_turn(event, turns):
    if event.get("turn"):
        return next((turn for turn in turns if turn["id"] == event["turn"]), None)
    at = event["at"]
    start = at - event["delay"] if event["kind"] == "ttft" else None
    matches = []
    for turn in turns:
        began = turn["started"]
        ended = turn["completed"]
        if began is None or at < began or (ended and at > ended + 999):
            continue
        # The history DB truncates Unix timestamps to whole seconds.
        if start is None or began - 100 <= start <= began + 1099:
            matches.append(turn)
    return matches[0] if len(matches) == 1 else None


def assigned_streams(streams, windows):
    # Prefer an event inside the stream. A small tail tolerance permits a
    # completion log emitted just after the provider stream span has closed.
    grouped = defaultdict(list)
    for event in streams:
        candidates = [window for window in windows
                      if window["start"] <= event["at"] <= window["at"]]
        if not candidates:
            candidates = [window for window in windows
                          if 0 < event["at"] - window["at"] <= 100]
        if len(candidates) == 1:
            grouped[candidates[0]["call"]].append(event)
    return {key: values[0] for key, values in grouped.items() if len(values) == 1}


def annotate(turns, events, receiving):
    first_tokens = {}
    for event in events:
        if event["kind"] == "ttft":
            turn = in_turn(event, turns)
            if turn and turn["id"] not in first_tokens:
                first_tokens[turn["id"]] = event
                turn["ttft"] = event["delay"]
    windows = [e for e in events if e["kind"] == "sampling"]
    streams = [e for e in events if e["kind"] == "stream"]
    stream_by_call = assigned_streams(streams, windows)
    totals = defaultdict(lambda: {"tokens": 0, "ms": 0, "count": 0})
    for window in windows:
        stream = stream_by_call.get(window["call"])
        if not stream:
            continue
        turn = in_turn(window, turns)
        if not turn:
            continue
        if stream.get("model") and turn["model"] is None:
            turn["model"] = stream["model"]
        if stream.get("effort") and turn["effort"] is None:
            turn["effort"] = stream["effort"]
        first = first_tokens.get(turn["id"])
        receive = receiving.get(window["call"])
        if first and window["start"] <= first["at"] < stream["at"]:
            generation_start = first["at"]
        elif receive and stream["delay"] is not None:
            generation_start = receive["start"] + stream["delay"]
        else:
            continue
        span_ms = stream["at"] - generation_start
        if span_ms <= 0:
            continue
        summary = totals[turn["id"]]
        summary["tokens"] += stream["tokens"]
        summary["ms"] += span_ms
        summary["count"] += 1
    for turn in turns:
        summary = totals.get(turn["id"])
        if summary:
            turn["rate"] = round(1000 * summary["tokens"] / summary["ms"], 1)
            turn["sample"] = summary


class Monitor:
    def __init__(self, codex_home, data_dir):
        self.codex_home = codex_home.resolve()
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.data_path = self.data_dir / "measurements.sqlite"
        self.state_path = newest_database(self.codex_home, "state")
        self.history_path = newest_database(self.codex_home, "thread_history")
        self.context_cache = {}
        self.context_lock = threading.Lock()
        self.snapshot = None
        self.recent_turns = {}
        self.error = None
        with closing(self.measurement_db()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS event (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, thread TEXT, at INTEGER NOT NULL,
                    detail TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS event_thread_at ON event(thread, at);
                CREATE INDEX IF NOT EXISTS event_kind_at ON event(kind, at);
            """)

    def measurement_db(self):
        return sqlite3.connect(self.data_path, timeout=2)

    def write_events(self, events):
        rows = []
        for event in events:
            detail = json.dumps(event, sort_keys=True, separators=(",", ":"))
            key = sha256(detail.encode()).hexdigest()
            rows.append((key, event["kind"], event["thread"], event["at"], detail))
        if rows:
            with closing(self.measurement_db()) as db, db:
                db.executemany("INSERT OR IGNORE INTO event VALUES (?, ?, ?, ?, ?)", rows)

    def read_context(self, filename, turn_id, offset):
        if offset is None or not filename:
            return None
        key = (filename, turn_id, offset)
        with self.context_lock:
            if key in self.context_cache:
                return self.context_cache[key]
        path = Path(filename)
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(self.codex_home) or not resolved.is_file():
                return None
            with resolved.open("rb") as stream:
                stream.seek(offset)
                bound = offset + 384 * 1024
                while stream.tell() < bound:
                    raw = stream.readline(min(64 * 1024 + 1, bound - stream.tell()))
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):
                        while stream.tell() < bound and raw and not raw.endswith(b"\n"):
                            raw = stream.readline(min(64 * 1024, bound - stream.tell()))
                        continue
                    if b'"type":"turn_context"' not in raw[:220]:
                        continue
                    try:
                        line = json.loads(raw)
                    except ValueError:
                        continue
                    fields = line.get("payload", {})
                    if line.get("type") == "turn_context" and identifier(fields.get("turn_id")) == turn_id:
                        result = (model_name(fields.get("model")), model_name(fields.get("effort")))
                        with self.context_lock:
                            if len(self.context_cache) >= 3200:
                                self.context_cache.clear()
                            self.context_cache[key] = result
                        return result
        except (OSError, ValueError):
            pass
        return None

    def turn_list(self, db, session, recent_only=False):
        if db is None:
            return []
        rows = db.execute("""
            SELECT turn_id, status, started_at, completed_at, duration_ms, rollout_byte_offset
            FROM thread_turns WHERE thread_id = ?
            ORDER BY rollout_ordinal DESC LIMIT ?
        """, (session["id"], 1 if recent_only else MAX_TURNS)).fetchall()
        turns = []
        for row in rows:
            turn_id = row["turn_id"]
            context = self.read_context(session["rollout_path"], turn_id, row["rollout_byte_offset"])
            selected, effort = context or (None, None)
            turns.append({
                "id": turn_id, "status": row["status"],
                "started": row["started_at"] * 1000 if row["started_at"] else None,
                "completed": row["completed_at"] * 1000 if row["completed_at"] else None,
                "duration": row["duration_ms"], "model": selected, "effort": effort,
                "ttft": None, "rate": None, "sample": None,
            })
        return turns

    def collect(self):
        if self.state_path is None:
            self.state_path = newest_database(self.codex_home, "state")
        if self.history_path is None:
            self.history_path = newest_database(self.codex_home, "thread_history")
        sessions = []
        if self.state_path:
            with closing(read_db(self.state_path)) as db:
                sessions = db.execute("""
                    SELECT id, title, model, reasoning_effort, model_provider, source, cwd,
                        rollout_path, COALESCE(updated_at_ms, updated_at * 1000) AS updated
                    FROM threads WHERE archived = 0
                    ORDER BY recency_at_ms DESC LIMIT ?
                """, (MAX_SESSIONS,)).fetchall()
        history_db = read_db(self.history_path) if self.history_path else None
        result = []
        recent_turns = {}
        try:
            with closing(self.measurement_db()) as db:
                cutoff = now_ms() - 7 * 24 * 60 * 60 * 1000
                by_thread = defaultdict(list)
                receiving = {}
                for kind, encoded in db.execute("""
                    SELECT kind, detail FROM event
                    WHERE at >= ? AND kind IN ('ttft', 'stream', 'sampling', 'receiving') ORDER BY at
                """, (cutoff,)):
                    event = json.loads(encoded)
                    if kind == "receiving":
                        receiving[event["call"]] = event
                    else:
                        by_thread[event["thread"]].append(event)
                last_timing = db.execute("SELECT max(at) FROM event WHERE kind='ttft'").fetchone()[0]
            for session in sessions:
                events = by_thread.get(session["id"], [])
                turns = self.turn_list(history_db, session, recent_only=not bool(events))
                annotate(turns, events, receiving)
                recent_turns[session["id"]] = turns
                latest = turns[0] if turns else None
                result.append({
                    "id": session["id"], "title": (session["title"] or "")[:240] or "제목 없는 세션",
                    "project": Path(session["cwd"]).name if session["cwd"] else "",
                    "model": model_name(session["model"]), "effort": model_name(session["reasoning_effort"]),
                    "status": latest["status"] if latest else "idle",
                    "updated": session["updated"],
                    "latest": latest,
                    "timing": next((turn for turn in turns if turn["ttft"] is not None or turn["rate"] is not None), None),
                })
        finally:
            if history_db:
                history_db.close()
        self.recent_turns = recent_turns
        return {"sessions": result, "updated": now_ms(), "telemetryAt": last_timing}

    def detail(self, thread_id):
        if self.state_path is None or self.history_path is None:
            return None
        with closing(read_db(self.state_path)) as db:
            session = db.execute("SELECT id, rollout_path FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if session is None:
            return None
        with closing(read_db(self.history_path)) as db:
            turns = self.turn_list(db, session)
        with closing(self.measurement_db()) as db:
            events = [json.loads(row[0]) for row in db.execute(
                "SELECT detail FROM event WHERE thread = ? AND kind IN ('ttft', 'stream', 'sampling') ORDER BY at", (thread_id,)
            )]
            calls = {event["call"] for event in events if event["kind"] == "sampling"}
            receiving = {}
            if calls:
                for row in db.execute("SELECT detail FROM event WHERE kind='receiving'"):
                    event = json.loads(row[0])
                    if event["call"] in calls:
                        receiving[event["call"]] = event
        annotate(turns, events, receiving)
        return {"turns": turns}

    def poll(self):
        while True:
            began = time.monotonic()
            try:
                self.snapshot = self.collect()
                self.error = None
            except (sqlite3.Error, OSError, ValueError) as error:
                self.error = type(error).__name__
            time.sleep(max(0.1, 1.5 - (time.monotonic() - began)))


class Handler(BaseHTTPRequestHandler):
    server_version = "CodexSessionMonitor"

    def log_message(self, *_):
        pass

    def permitted(self):
        port = self.server.server_port
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        return self.headers.get("Origin") in (None, f"http://{host}")

    def respond(self, code, payload, mime="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json_response(self, code, data):
        self.respond(code, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())

    def do_GET(self):
        if not self.permitted():
            return self.json_response(403, {"error": "Local access only"})
        path = urlsplit(self.path).path
        monitor = self.server.monitor
        dashboard_enabled = not (monitor.data_dir / LOCAL_DASHBOARD_OFF).exists()
        if path == "/health":
            return self.json_response(200, {
                "service": "codex-session-monitor", "pid": os.getpid(), "localDashboard": dashboard_enabled,
            })
        if not dashboard_enabled:
            return self.json_response(404, {"error": "Local dashboard disabled"})
        if path == "/api/sessions":
            payload = monitor.snapshot or {"sessions": [], "updated": None, "telemetryAt": None}
            return self.json_response(200, dict(payload, error=monitor.error))
        if path.startswith("/api/session/"):
            thread_id = identifier(path[len("/api/session/"):])
            try:
                detail = monitor.detail(thread_id) if thread_id else None
            except (sqlite3.Error, OSError, ValueError):
                return self.json_response(503, {"error": "Session history is temporarily unavailable"})
            return self.json_response(200 if detail is not None else 404, detail)
        assets = {
            "/": ("index.html", "text/html"),
            "/style.css": ("style.css", "text/css"),
            "/app.js": ("app.js", "text/javascript"),
        }
        if path in assets:
            filename, mime = assets[path]
            return self.respond(200, (ASSETS / filename).read_bytes(), mime + "; charset=utf-8")
        return self.json_response(404, {"error": "Not found"})

    def do_POST(self):
        if not self.permitted():
            return self.json_response(403, {"error": "Local access only"})
        processors = {"/v1/logs": log_measurements, "/v1/traces": trace_measurements}
        processor = processors.get(urlsplit(self.path).path)
        if processor is None:
            return self.json_response(404, {"error": "Not found"})
        key = self.headers.get("x-codex-monitor-key", "")
        if not hmac.compare_digest(key.encode(), self.server.key):
            return self.json_response(403, {"error": "Unknown exporter"})
        length = integer(self.headers.get("Content-Length"))
        if not length or length > MAX_OTLP_BYTES:
            return self.json_response(413, {"error": "Invalid batch size"})
        if self.headers.get_content_type() != "application/json":
            return self.json_response(415, {"error": "Expected OTLP JSON"})
        try:
            self.connection.settimeout(5)
            self.server.monitor.write_events(processor(json.loads(self.rfile.read(length))))
        except (ValueError, TypeError, AttributeError, OSError):
            return self.json_response(400, {"error": "Invalid telemetry batch"})
        except sqlite3.Error:
            return self.json_response(503, {"error": "Measurement store is temporarily unavailable"})
        return self.json_response(200, {})


def serve(codex_home, data_dir, port):
    monitor = Monitor(codex_home, data_dir)
    key = (data_dir / "exporter.key").read_bytes().strip()
    if len(key) < 32:
        raise ValueError("Invalid local exporter key")
    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        server.monitor = monitor
        server.key = key
        threading.Thread(target=monitor.poll, daemon=True).start()
        threading.Thread(target=sync_forever, args=(monitor,), daemon=True).start()
        print(f"http://127.0.0.1:{server.server_port}/", flush=True)
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=CODEX_HOME)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.codex_home, args.data_dir, args.port)


if __name__ == "__main__":
    main()
