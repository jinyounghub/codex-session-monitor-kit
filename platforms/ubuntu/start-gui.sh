#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
if [ "$#" -lt 1 ]; then
  echo "Usage: CODEX_MONITOR_REAL_CODEX=/path/to/codex $0 GUI_EXECUTABLE [GUI_ARGS...]" >&2
  exit 2
fi

python=${CODEX_MONITOR_PYTHON:-python3}
if ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "Python 3.11 or newer is required. Set CODEX_MONITOR_PYTHON to its executable." >&2
  exit 1
fi
CODEX_MONITOR_REAL_CODEX=${CODEX_MONITOR_REAL_CODEX:-$(command -v codex || true)}
if [ ! -x "$CODEX_MONITOR_REAL_CODEX" ]; then
  echo "Set CODEX_MONITOR_REAL_CODEX to the stock Codex executable used by your GUI." >&2
  exit 1
fi
export CODEX_MONITOR_REAL_CODEX CODEX_MONITOR_PYTHON="$python" CODEX_CLI_PATH="$repo/codex-capture"
"$python" -B "$repo/manage.py" configure
exec "$@"
