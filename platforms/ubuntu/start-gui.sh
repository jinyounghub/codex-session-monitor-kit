#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
if [ "$#" -lt 1 ]; then
  echo "Usage: $0 GUI_EXECUTABLE [GUI_ARGS...]" >&2
  exit 2
fi

python=${CODEX_MONITOR_PYTHON:-python3}
if ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "Python 3.11 or newer is required. Set CODEX_MONITOR_PYTHON to its executable." >&2
  exit 1
fi
# Old launch shortcuts may supply our now-retired wrapper in the environment.
case "${CODEX_CLI_PATH:-}" in
  */codex-capture)
    if [ "$CODEX_CLI_PATH" = "$repo/codex-capture" ] || cmp -s "$CODEX_CLI_PATH" "$repo/codex-capture"; then
      unset CODEX_CLI_PATH
    fi
    ;;
esac
"$python" -B "$repo/manage.py" configure
exec "$@"
