#!/usr/bin/env python3
"""Start the local dashboard and manage its machine-local exporter settings."""

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from getpass import getpass
import json
import os
from pathlib import Path
import plistlib
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import webbrowser

from runtime_paths import APP, CODEX_HOME, LOCAL_DASHBOARD_OFF, mac_gui, runtime_dir
from site_metrics import CONNECTION, SYNC_STATUS, connect, read_connection


RUNTIME = runtime_dir()
CONFIG = CODEX_HOME / "config.toml"
LAUNCH_AGENT = "com.local.codex-session-monitor"
SYSTEMD_UNIT = "codex-session-monitor.service"
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"
START = b"# BEGIN Codex Session Monitor"
END = b"# END Codex Session Monitor"
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health():
    try:
        with HTTP.open(URL + "health", timeout=0.4) as response:
            value = json.load(response)
            if isinstance(value, dict) and value.get("service") == "codex-session-monitor":
                return value
    except (OSError, ValueError):
        pass
    return None


def key_file(runtime=RUNTIME):
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = runtime / "exporter.key"
    try:
        descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return key
    with os.fdopen(descriptor, "w", encoding="utf-8") as writer:
        writer.write(secrets.token_hex(32) + "\n")
    return key


def atomic_write(path, data, perms):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".codex-monitor-", delete=False) as output:
        candidate = Path(output.name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(output.fileno(), perms)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            output.close()
            os.replace(candidate, path)
        finally:
            candidate.unlink(missing_ok=True)


@contextmanager
def service_lock(runtime=RUNTIME):
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (runtime / "service.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0, os.SEEK_END)
            if not lock.tell():
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def start(open_browser, announce=True):
    with service_lock():
        key_file()
        if not health():
            options = (
                {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
                if os.name == "nt" else {"start_new_session": True, "umask": 0o077}
            )
            with (RUNTIME / "server.log").open("ab") as log:
                child = subprocess.Popen(
                    [sys.executable, "-B", str(APP / "monitor.py"), "--data-dir", str(RUNTIME), "--port", str(PORT)],
                    cwd=APP, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    **options,
                )
            for _ in range(36):
                if health():
                    break
                if child.poll() is not None:
                    break
                time.sleep(0.15)
            if not health():
                if child.poll() is None:
                    child.terminate()
                raise RuntimeError(f"대시보드가 시작되지 않았습니다. 실행 기록은 {RUNTIME / 'server.log'}에 있습니다.")
            atomic_write(RUNTIME / "server.pid", f"{child.pid}\n".encode(), 0o600)
    target = URL
    if (RUNTIME / LOCAL_DASHBOARD_OFF).exists():
        settings = read_connection(RUNTIME) if open_browser else None
        target = settings["url"] if settings else None
    if announce:
        print(target or "로컬 수집기가 실행 중입니다.")
    if open_browser and target:
        webbrowser.open(target)


def stop(runtime=RUNTIME):
    with service_lock(runtime):
        live = health()
        pid_file = runtime / "server.pid"
        if live and pid_file.exists():
            pid = int(pid_file.read_text().strip())
            if pid > 1 and live.get("pid") == pid:
                os.kill(pid, signal.SIGTERM)
                for _ in range(30):
                    running = health()
                    if not running or running.get("pid") != pid:
                        pid_file.unlink(missing_ok=True)
                        return
                    time.sleep(0.1)
                raise RuntimeError("로컬 서버가 아직 종료되지 않았습니다.")


def run_collector(codex_home, data_dir, register_capture):
    from monitor import serve

    os.umask(0o077)
    key_file(data_dir)
    with (data_dir / "server.log").open("a", encoding="utf-8", buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            stop(data_dir)
            if register_capture:
                try:
                    restore_mac_cli()
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    print(error, file=log)
            try:
                serve(codex_home, data_dir, PORT)
            except (OSError, ValueError) as error:
                print(error, file=log)
                raise


def enable_autostart():
    args = [sys.executable, "-B", str(APP / "manage.py"), "run",
            "--codex-home", str(CODEX_HOME), "--data-dir", str(RUNTIME.resolve())]
    if sys.platform == "darwin":
        launchctl = "/bin/launchctl"
        domain = f"gui/{os.getuid()}"
        subprocess.run([launchctl, "print", domain], stdout=subprocess.DEVNULL, check=True)
        folder = Path.home() / "Library" / "LaunchAgents"
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        plist = folder / f"{LAUNCH_AGENT}.plist"
        service = f"{domain}/{LAUNCH_AGENT}"
        if subprocess.run([launchctl, "print", service], capture_output=True).returncode == 0:
            subprocess.run([launchctl, "bootout", service], check=True)
        restore_mac_cli()
        atomic_write(plist, plistlib.dumps({
            "Label": LAUNCH_AGENT, "ProgramArguments": args,
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
            "Umask": 0o077, "WorkingDirectory": str(APP),
        }), 0o600)
        stop()
        subprocess.run([launchctl, "enable", service], check=True)
        subprocess.run([launchctl, "bootstrap", domain, str(plist)], check=True)
    elif sys.platform.startswith("linux"):
        manager = ["systemctl", "--user"]
        subprocess.run([*manager, "show-environment"], stdout=subprocess.DEVNULL, check=True)
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        folder = config_home / "systemd" / "user"
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        command = " ".join(json.dumps(arg.replace("%", "%%"), ensure_ascii=False) for arg in args)
        unit = (
            "[Unit]\nDescription=Codex Session Monitor\n\n[Service]\n"
            f"ExecStart=:{command}\nRestart=on-failure\nRestartSec=10\nUMask=0077\n\n"
            "[Install]\nWantedBy=default.target\n"
        )
        atomic_write(folder / SYSTEMD_UNIT, unit.encode(), 0o600)
        stop()
        subprocess.run([*manager, "daemon-reload"], check=True)
        subprocess.run([*manager, "enable", SYSTEMD_UNIT], check=True)
        subprocess.run([*manager, "restart", SYSTEMD_UNIT], check=True)
    else:
        raise RuntimeError("Windows에서는 platforms/windows/setup.ps1로 자동 시작을 등록합니다.")
    for _ in range(36):
        if health():
            print("로그인 자동 시작을 등록했고 수집기가 실행 중입니다.")
            return
        time.sleep(0.2)
    raise RuntimeError(f"자동 시작을 등록했습니다. 수집기 실행 기록은 {RUNTIME / 'server.log'}에 있습니다.")


def configured_section():
    key = key_file().read_text().strip()
    if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
        raise RuntimeError("로컬 수집기 키를 읽을 수 없습니다.")
    lines = [START.decode(), "[otel]"]
    for setting, path in (("exporter", "logs"), ("trace_exporter", "traces")):
        lines.append(
            f'{setting} = {{ otlp-http = {{ endpoint = "{URL}v1/{path}", protocol = "json", '
            f'headers = {{ "x-codex-monitor-key" = "{key}" }} }} }}'
        )
    lines.extend([
        "log_user_prompt = false", "log_agent_responses = false",
        "log_guardian_assessments = false", "log_sse_deltas = false",
        "tool_result = { max_bytes = 0 }", END.decode(), "",
    ])
    return "\n".join(lines).encode()


def configure():
    path = CONFIG.resolve()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    original = path.read_bytes() if path.exists() else b""
    current = tomllib.loads(original.decode())
    block = configured_section()
    if block in original:
        print("로컬 수집기가 이미 설정되어 있습니다.")
        return
    if "otel" in current or START in original or END in original:
        raise RuntimeError("기존 [otel] 설정이 있습니다. 현재 설정을 덮어쓰지 않았습니다.")
    updated = original + (b"\n" if original.endswith(b"\n") else b"\n\n") + block
    tomllib.loads(updated.decode())
    backup = RUNTIME / f"config-before-monitor-{time.time_ns()}.toml"
    atomic_write(backup, original, 0o600)
    if (path.read_bytes() if path.exists() else b"") != original:
        raise RuntimeError("설정 파일이 동시에 변경되었습니다. 다시 실행해 주세요.")
    atomic_write(path, updated, stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600)
    print("로컬 메트릭 수집을 설정했습니다. 열려 있던 Codex 앱에서는 다음 재시작부터 적용됩니다.")


def disconnect():
    path = CONFIG.resolve()
    if not path.exists():
        return
    original = path.read_bytes()
    start_at = original.find(START)
    if start_at < 0:
        print("대시보드 수집기 설정이 없습니다.")
        return
    expected = configured_section()
    if original[start_at:start_at + len(expected)] != expected:
        raise RuntimeError("대시보드 수집기 설정이 수정되었습니다. 설정 파일을 그대로 두었습니다.")
    prefix = original[:start_at]
    if prefix.endswith(b"\n\n"):
        prefix = prefix[:-1]
    updated = prefix + original[start_at + len(expected):]
    tomllib.loads(updated.decode())
    if path.read_bytes() != original:
        raise RuntimeError("설정 파일이 동시에 변경되었습니다. 다시 실행해 주세요.")
    atomic_write(path, updated, stat.S_IMODE(path.stat().st_mode))
    print("수집기 설정을 제거했습니다. Codex를 재시작하면 내보내기가 중지됩니다.")


def is_monitor_launcher(value):
    if not value:
        return False
    path = Path(value)
    if not path.is_absolute() or path.name != "codex-capture":
        return False
    bundled = APP / "codex-capture"
    if path in (bundled, RUNTIME.resolve() / "app" / "codex-capture"):
        return True
    try:
        # Upgrades can use a different directory from the original installation.
        if path.is_file():
            return (path.stat().st_size == bundled.stat().st_size
                    and path.read_bytes() == bundled.read_bytes())
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT}.plist"
        previous = plistlib.loads(plist.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False
    if isinstance(previous, dict) and previous.get("Label") == LAUNCH_AGENT:
        args = previous.get("ProgramArguments")
        return (isinstance(args, list) and "--register-capture" in args
                and str(path.parent / "manage.py") in args)
    return False


def restore_mac_cli():
    if sys.platform != "darwin":
        return
    result = subprocess.run(
        ["/bin/launchctl", "getenv", "CODEX_CLI_PATH"], capture_output=True, text=True, check=False,
    )
    if is_monitor_launcher(result.stdout.rstrip("\n")):
        subprocess.run(["/bin/launchctl", "unsetenv", "CODEX_CLI_PATH"], check=True)
        print("이전 수집기가 등록한 Codex 실행 경로를 해제했습니다.")


def launch_codex():
    if sys.platform != "darwin":
        raise RuntimeError("macOS에서 사용하는 명령입니다. 이 장비의 실행 안내를 확인하세요.")
    app = mac_gui()
    if app is None:
        raise RuntimeError("Codex GUI의 앱 번들 경로를 CODEX_MONITOR_MAC_APP에 지정하세요.")
    restore_mac_cli()
    start(False)
    env = os.environ.copy()
    if is_monitor_launcher(env.get("CODEX_CLI_PATH")):
        del env["CODEX_CLI_PATH"]
    subprocess.run(["/usr/bin/open", "-a", str(app)], env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choices = parser.add_subparsers(dest="operation", required=True)
    start_command = choices.add_parser("start")
    start_command.add_argument("--open", action="store_true")
    run_command = choices.add_parser("run")
    run_command.add_argument("--codex-home", type=Path, default=CODEX_HOME)
    run_command.add_argument("--data-dir", type=Path, default=RUNTIME)
    run_command.add_argument("--register-capture", action="store_true", help=argparse.SUPPRESS)
    choices.add_parser("enable-autostart")
    dashboard = choices.add_parser("local-dashboard")
    dashboard.add_argument("state", choices=("on", "off"))
    choices.add_parser("configure")
    choices.add_parser("disconnect")
    choices.add_parser("stop")
    choices.add_parser("launch-codex")
    connection = choices.add_parser("connect-site")
    connection.add_argument("--url", required=True)
    connection.add_argument("--name", required=True)
    connection.add_argument("--token-stdin", action="store_true")
    choices.add_parser("site-status")
    choices.add_parser("disconnect-site")
    args = parser.parse_args()
    try:
        if args.operation == "start":
            start(args.open)
        elif args.operation == "run":
            run_collector(args.codex_home, args.data_dir, args.register_capture)
        elif args.operation == "enable-autostart":
            enable_autostart()
        elif args.operation == "local-dashboard":
            marker = RUNTIME / LOCAL_DASHBOARD_OFF
            if args.state == "off":
                RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
                atomic_write(marker, b"", 0o600)
            else:
                marker.unlink(missing_ok=True)
            print(f"로컬 대시보드 {args.state.upper()}")
        elif args.operation == "configure":
            restore_mac_cli()
            start(False)
            configure()
        elif args.operation == "disconnect":
            disconnect()
            restore_mac_cli()
        elif args.operation == "stop":
            stop()
        elif args.operation == "launch-codex":
            launch_codex()
        elif args.operation == "connect-site":
            token = sys.stdin.readline() if args.token_stdin else getpass("Sites 서비스 접속 키: ")
            connect(RUNTIME, args.url, args.name, token)
            start(False)
            print("공통 대시보드에 장비를 연결했습니다.")
        elif args.operation == "site-status":
            settings = read_connection(RUNTIME)
            local = health()
            status = {"collectorRunning": bool(local), "localDashboard": bool(local and local.get("localDashboard"))}
            if settings:
                status_path = RUNTIME / SYNC_STATUS
                receipt = json.loads(status_path.read_text()) if status_path.exists() else {"status": "waiting"}
                status.update({"url": settings["url"], "label": settings["label"], **receipt})
            print(json.dumps(status, ensure_ascii=False))
        elif args.operation == "disconnect-site":
            (RUNTIME / CONNECTION).unlink(missing_ok=True)
            (RUNTIME / SYNC_STATUS).unlink(missing_ok=True)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
