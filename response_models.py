"""Store the two fields read from server responses and link them by response ID."""

from collections import OrderedDict
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time


MODEL = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}")
RESPONSE = re.compile(r"resp_[a-zA-Z0-9_-]{1,200}")
USAGE_RECORD = re.compile(rb'"type"\s*:\s*"token_usage_record"\s*,\s*"payload"')
MAX_LINE = 128 * 1024
SCAN_BYTES = 8 * 1024 * 1024
PENDING_WINDOW_MS = 7 * 24 * 60 * 60 * 1000


def model_name(value):
    return value if isinstance(value, str) and MODEL.fullmatch(value) else None


def response_id(value):
    return value if isinstance(value, str) and RESPONSE.fullmatch(value) else None


def prepare_store(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS response_model (
            response_id TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            observed_at INTEGER NOT NULL,
            thread TEXT,
            turn TEXT,
            requested TEXT
        );
        CREATE INDEX IF NOT EXISTS response_model_thread_at ON response_model(thread, observed_at);
    """)


def save_responses(data_dir, responses):
    if not responses:
        return
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    at = time.time_ns() // 1_000_000
    with closing(sqlite3.connect(data_dir / "measurements.sqlite", timeout=2)) as db, db:
        prepare_store(db)
        db.executemany("""
            INSERT OR IGNORE INTO response_model (response_id, model, observed_at)
            VALUES (?, ?, ?)
        """, [(rid, model, at) for rid, model in responses])


def events_from_responses(db, cutoff=0, thread=None):
    query = "SELECT response_id, model, observed_at, thread, turn, requested FROM response_model"
    if thread is None:
        rows = db.execute(query + " WHERE thread IS NOT NULL AND observed_at >= ?", (cutoff,))
    else:
        rows = db.execute(query + " WHERE thread = ?", (thread,))
    for rid, model, at, thread_id, turn, requested in rows:
        yield {
            "kind": "response_model", "at": at, "thread": thread_id, "turn": turn,
            "model": model, "from": requested, "response": rid,
        }


class RolloutLinks:
    """Follow usage records without indexing the conversation content around them."""

    def __init__(self, codex_home):
        self.codex_home = codex_home
        self.cursors = {}
        self.usage_records = OrderedDict()

    def link(self, db, history, sessions, read_context):
        now = time.time_ns() // 1_000_000
        pending = {
            rid: at for rid, at in db.execute("""
                SELECT response_id, observed_at FROM response_model
                WHERE thread IS NULL AND observed_at >= ?
            """, (now - PENDING_WINDOW_MS,))
        }
        if not pending or history is None:
            return
        # Allow for batched writes, process startup, and whole-second turn timestamps.
        since = min(pending.values()) - 300_000
        active_paths = set()
        for session in sessions:
            if not session["rollout_path"]:
                continue
            path = Path(session["rollout_path"]).resolve()
            if not path.is_relative_to(self.codex_home):
                continue
            active_paths.add(path)
            try:
                stat = path.stat()
                if stat.st_mtime_ns // 1_000_000 < since:
                    continue
                matches = self.scan(path, stat, history, session["id"], since)
            except OSError:
                continue
            for rid, thread, turn in matches:
                self.usage_records[rid] = (path, thread, turn)
                self.usage_records.move_to_end(rid)
                if len(self.usage_records) > 8192:
                    self.usage_records.popitem(last=False)
        self.cursors = {path: cursor for path, cursor in self.cursors.items() if path in active_paths}
        links = []
        for rid in pending.keys() & self.usage_records.keys():
            path, thread, turn = self.usage_records[rid]
            row = history.execute("""
                SELECT rollout_byte_offset FROM thread_turns WHERE thread_id = ? AND turn_id = ?
            """, (thread, turn)).fetchone()
            if row is None:
                continue
            context = read_context(str(path), turn, row[0])
            links.append((thread, turn, context[0] if context else None, rid))
        if links:
            with db:
                db.executemany("""
                    UPDATE response_model SET thread = ?, turn = ?, requested = ?
                    WHERE response_id = ? AND thread IS NULL
                """, links)

    def scan(self, path, stat, history, thread, since):
        file_id = (stat.st_dev, stat.st_ino)
        cursor = self.cursors.get(path)
        if cursor and cursor[0] == file_id and cursor[1] <= stat.st_size:
            _, position, skipping = cursor
        else:
            row = history.execute("""
                SELECT min(rollout_byte_offset) FROM thread_turns
                WHERE thread_id = ? AND (completed_at IS NULL OR completed_at >= ?)
            """, (thread, since // 1000)).fetchone()
            if row and row[0] is not None and row[0] <= stat.st_size:
                position, skipping = row[0], False
            else:
                position = max(0, stat.st_size - SCAN_BYTES)
                skipping = position > 0
        matches = []
        end = position + SCAN_BYTES
        with path.open("rb") as stream:
            stream.seek(position)
            while stream.tell() < end:
                start = stream.tell()
                raw = stream.readline(MAX_LINE)
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    if len(raw) < MAX_LINE and not skipping:
                        stream.seek(start)
                        break
                    skipping = True
                    continue
                if skipping:
                    skipping = False
                    continue
                if not USAGE_RECORD.search(raw[:200]):
                    continue
                try:
                    item = json.loads(raw)
                    usage = item["payload"]
                except (ValueError, KeyError, TypeError):
                    continue
                if item.get("type") != "token_usage_record" or not isinstance(usage, dict):
                    continue
                rid = response_id(usage.get("response_id"))
                turn = usage.get("turn_id")
                if rid and usage.get("thread_id") == thread and isinstance(turn, str) and turn:
                    matches.append((rid, thread, turn))
            self.cursors[path] = (file_id, stream.tell(), skipping)
        return matches
