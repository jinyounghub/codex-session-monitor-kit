"""Launch the bundled Codex app-server and extract only response and model IDs."""

from collections import OrderedDict
import json
import os
import queue
import signal
import sqlite3
import subprocess
import sys
import threading
import time

from response_models import model_name, response_id, save_responses
from runtime_paths import codex_binary, runtime_dir


MAX_TRACE_LINE = 8 * 1024 * 1024
RAW_TARGETS = ("tungstenite::protocol", "codex_api::sse::responses")
RAW_PREFIXES = ("Received message ", "SSE event: ")
TRACE_FILTER = "tungstenite::protocol=trace,tungstenite::protocol::frame=off,codex_api::sse::responses=trace"


class ResponseReader:
    def __init__(self):
        self.started = OrderedDict()

    def read(self, message):
        prefix = next((part for part in RAW_PREFIXES if message.startswith(part)), None)
        if not prefix:
            return None
        try:
            event = json.loads(message[len(prefix):])
        except (ValueError, RecursionError):
            return None
        if not isinstance(event, dict) or event.get("type") not in (
            "response.created", "response.completed", "response.incomplete",
        ):
            return None
        body = event.get("response")
        if not isinstance(body, dict):
            return None
        rid = response_id(body.get("id"))
        if not rid:
            return None
        model = model_name(body.get("model"))
        if event["type"] == "response.created":
            if model:
                self.started[rid] = model
                self.started.move_to_end(rid)
                if len(self.started) > 512:
                    self.started.popitem(last=False)
            return None
        earlier = self.started.pop(rid, None)
        return (rid, model or earlier) if model or earlier else None


def app_server(args):
    if "app-server" not in args:
        return False
    following = args[args.index("app-server") + 1:]
    return not set(following).intersection({
        "daemon", "proxy", "generate-ts", "generate-json-schema", "help",
        "-h", "--help", "--print-startup-config",
    })


def writer(pairs, runtime):
    while True:
        item = pairs.get()
        if item is None:
            return
        batch = [item]
        done = False
        for _ in range(31):
            try:
                item = pairs.get_nowait()
            except queue.Empty:
                break
            if item is None:
                done = True
                break
            batch.append(item)
        for attempt in range(3):
            try:
                save_responses(runtime, batch)
                break
            except (OSError, sqlite3.Error):
                if attempt < 2:
                    time.sleep(0.2)
        else:
            print("Codex session monitor could not save a response model ID.", file=sys.stderr, flush=True)
        if done:
            return


def filter_stderr(stream, pairs):
    reader = ResponseReader()
    while line := stream.readline(MAX_TRACE_LINE):
        if len(line) == MAX_TRACE_LINE and not line.endswith(b"\n"):
            while part := stream.readline(65536):
                if part.endswith(b"\n"):
                    break
            continue
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            # The child uses JSON logging. An unframed line has no safe source tag.
            continue
        if not isinstance(record, dict):
            continue
        fields = record.get("fields")
        if not isinstance(fields, dict):
            continue
        message = fields.get("message")
        targets = (record.get("target"), fields.get("log.target"))
        traced = any(isinstance(target, str) and target.startswith(RAW_TARGETS) for target in targets)
        raw = isinstance(message, str) and message.startswith(RAW_PREFIXES)
        # Never forward response traces or other verbose diagnostics from these added targets.
        if traced or raw:
            if traced and raw:
                pair = reader.read(message)
                if pair:
                    pairs.put(pair)
            continue
        try:
            sys.stderr.buffer.write(line)
            sys.stderr.buffer.flush()
        except (BrokenPipeError, OSError):
            pass


def start_local_monitor(runtime):
    if runtime == runtime_dir() and (runtime / "exporter.key").is_file():
        from manage import start
        try:
            start(False, announce=False)
        except (OSError, RuntimeError):
            print("Codex session monitor could not start the local collector.", file=sys.stderr, flush=True)


def run(binary, args, runtime):
    command = [str(binary), *args]
    # Older macOS GUI launch shortcuts may still point here. Hand off immediately
    # so the signed app-server runs as the desktop helper's direct child.
    if sys.platform == "darwin" or not app_server(args):
        if os.name == "nt":
            return subprocess.call(command)
        os.execv(binary, command)
    threading.Thread(target=start_local_monitor, args=(runtime,), daemon=True).start()
    env = dict(os.environ)
    env["RUST_LOG"] = (env.get("RUST_LOG", "warn") + "," + TRACE_FILTER).lstrip(",")
    env["LOG_FORMAT"] = "json"
    child = subprocess.Popen(command, stderr=subprocess.PIPE, env=env, start_new_session=os.name != "nt")

    def forward(signum, _frame):
        if child.poll() is None:
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

    for signum in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", None)):
        if signum is not None:
            signal.signal(signum, forward)
    pairs = queue.SimpleQueue()
    sink = threading.Thread(target=writer, args=(pairs, runtime), daemon=True)
    sink.start()
    try:
        with child.stderr:
            filter_stderr(child.stderr, pairs)
    finally:
        pairs.put(None)
        sink.join()
    status = child.wait()
    return status if status >= 0 else 128 - status


if __name__ == "__main__":
    os.umask(0o077)
    try:
        sys.exit(run(codex_binary(), sys.argv[1:], runtime_dir()))
    except (OSError, RuntimeError) as error:
        sys.exit(str(error))
