"""Upload only the dashboard's allowlisted measurement fields to a private Site."""

from datetime import datetime, timezone
from hashlib import blake2s, sha256
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


CONNECTION = "site-connection.json"
SYNC_STATUS = "site-sync-status.json"
WEEK = 604_800_000
MAX_PAYLOAD = 640 * 1024
DEVICE = re.compile(r"[a-f0-9]{32}")
TITLE_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


HTTP = build_opener(NoRedirect)


def site_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".chatgpt.site")
        or parsed.username or parsed.password or parsed.port or parsed.path not in ("", "/")
        or parsed.query or parsed.fragment
    ):
        raise ValueError("비공개 Sites 대시보드의 HTTPS 주소를 입력하세요.")
    return f"https://{parsed.hostname}"


def read_connection(runtime):
    path = runtime / CONNECTION
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    label = value["label"]
    token = value["service_token"]
    identifier = value["device_id"]
    if (
        not isinstance(label, str) or not 1 <= len(label.strip()) <= 48
        or any(ord(char) < 32 or ord(char) == 127 for char in label)
        or not isinstance(token, str) or not 16 <= len(token) <= 4096
        or not token.isascii() or any(char.isspace() for char in token)
        or not isinstance(identifier, str) or not DEVICE.fullmatch(identifier)
    ):
        raise ValueError("장비 이름이나 접속 키가 올바르지 않습니다.")
    return dict(value, url=site_url(value["url"]), label=label.strip())


def write_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def auth_headers(token):
    proof = sha256(("codex-session-monitor/v1\0" + token).encode()).hexdigest()
    return {"OAI-Sites-Authorization": "Bearer " + token, "X-Codex-Monitor-Key": proof}


def connect(runtime, url, label, token):
    url = site_url(url)
    label, token = label.strip(), token.strip()
    if (
        not 1 <= len(label) <= 48 or any(ord(char) < 32 or ord(char) == 127 for char in label)
        or not 16 <= len(token) <= 4096 or not token.isascii() or any(char.isspace() for char in token)
    ):
        raise ValueError("장비 이름이나 접속 키가 올바르지 않습니다.")
    try:
        request = Request(url + "/health", headers=auth_headers(token))
        with HTTP.open(request, timeout=10) as response:
            verified = json.loads(response.read(2048))
    except (OSError, ValueError) as error:
        status = error.code if isinstance(error, HTTPError) else "접속 불가"
        raise RuntimeError(f"사이트 접속 키를 확인할 수 없습니다. ({status})") from None
    if verified != {"service": "codex-session-monitor"}:
        raise RuntimeError("이 주소에서 Codex 세션 모니터를 찾을 수 없습니다.")
    previous = read_connection(runtime)
    device_id = previous["device_id"] if previous and previous["url"] == url else secrets.token_hex(16)
    write_json(runtime / CONNECTION, {"url": url, "label": label, "service_token": token, "device_id": device_id})


def site_report(snapshot, details, config, now):
    def anonymize(identifier):
        return blake2s(identifier.encode(), key=bytes.fromhex(config["device_id"]), digest_size=16).hexdigest()

    def turn(value):
        if not value:
            return None
        output = {key: value.get(key) for key in (
            "status", "started", "completed", "duration", "model", "effort", "ttft", "rate",
        )}
        output["id"] = anonymize(value["id"])
        output["served"] = value["served"][:12]
        output["servedSources"] = {slug: value.get("servedSources", {}).get(slug) for slug in output["served"]}
        if value.get("sample"):
            output["sample"] = {key: value["sample"].get(key) for key in ("tokens", "count")}
        return output

    items = []
    cutoff = now - WEEK
    for session in (snapshot or {}).get("sessions", ()):
        if not session["latest"] or (session["updated"] < cutoff and not session.get("timing")):
            continue
        row = {key: session[key] for key in ("model", "effort", "status", "updated")}
        row["id"] = anonymize(session["id"])
        row["title"] = TITLE_CONTROLS.sub(" ", session.get("title") or "").strip()[:240] or None
        row["latest"] = turn(session["latest"])
        row["timing"] = turn(session.get("timing"))
        row["turns"] = [turn(value) for value in details.get(session["id"], (session["latest"],))[:12]]
        row["routes"] = [{
            "at": route["at"], "from": route["from"], "to": route["to"], "source": route.get("source"),
            "turn": anonymize(route["turn"]) if route.get("turn") else None,
        } for route in session["routes"] if route["at"] >= cutoff][:24]
        items.append(row)
        if len(items) >= 80:
            break
    return {
        "version": 1, "device": {"id": config["device_id"], "label": config["label"]},
        "observedAt": now, "telemetryAt": snapshot.get("telemetryAt") if snapshot else None,
        "sessions": items if snapshot else None,
    }


def post(config, report):
    data = json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode()
    while len(data) > MAX_PAYLOAD and report["sessions"]:
        report["sessions"].pop()
        data = json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode()
    request = Request(
        config["url"] + "/api/report", data=data,
        headers={**auth_headers(config["service_token"]), "Content-Type": "application/json"}, method="POST",
    )
    with HTTP.open(request, timeout=10) as response:
        receipt = json.loads(response.read(2048))
    if receipt.get("accepted") is not True:
        raise ValueError("Site did not accept the report")
    return receipt["receivedAt"]


def sync_forever(monitor):
    latest = None
    last_sent = 0
    runtime = monitor.data_dir
    while True:
        try:
            config = read_connection(runtime)
            snapshot = monitor.snapshot
            if config:
                now = time.time_ns() // 1_000_000
                report = site_report(snapshot, monitor.recent_turns, config, now)
                signature = sha256(json.dumps([config["url"], report["device"], report["sessions"]], sort_keys=True).encode()).digest()
                if signature != latest or time.monotonic() - last_sent >= 45:
                    receipt = post(config, report)
                    sessions = len(report["sessions"]) if report["sessions"] is not None else None
                    write_json(runtime / SYNC_STATUS, {"status": "connected", "receivedAt": receipt, "sessions": sessions})
                    latest = signature
                    last_sent = time.monotonic()
        except (OSError, ValueError, KeyError, TypeError) as error:
            status = error.code if isinstance(error, HTTPError) else type(error).__name__
            try:
                write_json(runtime / SYNC_STATUS, {
                    "status": "retrying", "error": status, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                })
            except OSError:
                pass
        time.sleep(8)
