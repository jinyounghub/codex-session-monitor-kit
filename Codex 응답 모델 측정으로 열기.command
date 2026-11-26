#!/bin/zsh
set -e
monitor_dir="${0:A:h}"
monitor_python="$(command -v python3.12 || true)"
if [[ -z "$monitor_python" && -x "$HOME/.local/bin/python3.12" ]]; then
  monitor_python="$HOME/.local/bin/python3.12"
fi
if [[ -z "$monitor_python" ]]; then
  monitor_python="$(command -v python3.11 || true)"
fi
if [[ -z "$monitor_python" ]] && python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  monitor_python="$(command -v python3)"
fi
if [[ -z "$monitor_python" ]]; then
  print -u2 "Python 3.11 이상이 필요합니다."
  exit 1
fi
exec "$monitor_python" "$monitor_dir/manage.py" launch-codex
