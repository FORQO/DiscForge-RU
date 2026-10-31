#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
    candidates=("${DISKFORGE_PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 python3)
    selected=""
    for candidate in "${candidates[@]}"; do
        if [[ -n "$candidate" ]] && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3,11))' 2>/dev/null; then
            selected="$candidate"
            break
        fi
    done
    if [[ -z "$selected" ]]; then
        echo 'Требуется Python 3.11+. Установите его с python.org или через brew install python@3.11.'
        exit 1
    fi
    "$selected" -m venv .venv
fi
.venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3,11))'
.venv/bin/python -m pip install -r requirements.txt
if [[ "${1:-}" != '--prepare-only' ]]; then
    exec .venv/bin/python main.py
fi
