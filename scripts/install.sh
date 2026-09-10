#!/bin/sh
# Run from a trusted source checkout; --source also accepts a built wheel or source archive.
set -eu

ag_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ag_source=$(CDPATH= cd -- "$ag_script_dir/.." && pwd)
ag_prefix=${AGENTAGON_INSTALL_PREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/agentagon/venv}
ag_bin_dir=${AGENTAGON_INSTALL_BIN_DIR:-$HOME/.local/bin}
ag_python=${AGENTAGON_INSTALL_PYTHON:-}
ag_codex=0
ag_claude=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source)
            [ "$#" -ge 2 ] || { echo '--source requires a checkout, archive, or wheel path' >&2; exit 2; }
            ag_source=$2
            shift 2
            ;;
        --host)
            [ "$#" -ge 2 ] || { echo '--host requires codex or claude-code' >&2; exit 2; }
            case "$2" in
                codex) ag_codex=1 ;;
                claude-code) ag_claude=1 ;;
                *) echo 'Supported hosts: codex, claude-code' >&2; exit 2 ;;
            esac
            shift 2
            ;;
        --help|-h)
            echo 'Usage: sh scripts/install.sh [--source CHECKOUT_OR_WHEEL] [--host codex|claude-code]...'
            echo 'Installs an isolated CLI and native plugins for selected or detected host CLIs.'
            echo 'Optional paths: AGENTAGON_INSTALL_PREFIX, AGENTAGON_INSTALL_BIN_DIR, AGENTAGON_INSTALL_PYTHON.'
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$ag_python" ]; then
    for ag_candidate in python3.13 python3.12 python3; do
        if command -v "$ag_candidate" >/dev/null 2>&1 &&
            "$ag_candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
            ag_python=$ag_candidate
            break
        fi
    done
fi
[ -n "$ag_python" ] || { echo 'Python 3.12+ is required.' >&2; exit 1; }
"$ag_python" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' || {
    echo 'Python 3.12+ is required.' >&2; exit 1;
}
[ -e "$ag_source" ] || { echo 'The source checkout, archive, or wheel does not exist.' >&2; exit 1; }
case "$ag_prefix:$ag_bin_dir" in
    /*:/*) ;;
    *) echo 'Install prefix and bin directory must be absolute paths.' >&2; exit 1 ;;
esac
if [ -L "$ag_prefix" ] || { [ -e "$ag_prefix" ] && [ ! -f "$ag_prefix/.agentagon-runtime" ]; }; then
    echo "Preserved unmanaged install destination: $ag_prefix" >&2
    exit 1
fi
if [ -e "$ag_bin_dir/agentagon" ] || [ -L "$ag_bin_dir/agentagon" ]; then
    if [ ! -L "$ag_bin_dir/agentagon" ] || [ "$(readlink "$ag_bin_dir/agentagon")" != "$ag_prefix/bin/agentagon" ]; then
        echo "Preserved existing command: $ag_bin_dir/agentagon; choose AGENTAGON_INSTALL_BIN_DIR." >&2
        exit 1
    fi
fi

if [ ! -e "$ag_prefix" ]; then
    mkdir -p "$(dirname -- "$ag_prefix")"
    mkdir "$ag_prefix"
fi
# Establish ownership before venv can leave a partial runtime behind.
: > "$ag_prefix/.agentagon-runtime"
"$ag_python" -m venv "$ag_prefix"
"$ag_prefix/bin/python" -m pip install --upgrade "$ag_source"
mkdir -p "$ag_bin_dir"
if [ ! -L "$ag_bin_dir/agentagon" ]; then
    ln -s "$ag_prefix/bin/agentagon" "$ag_bin_dir/agentagon"
fi
set --
[ "$ag_codex" -eq 0 ] || set -- "$@" --host codex
[ "$ag_claude" -eq 0 ] || set -- "$@" --host claude-code
"$ag_prefix/bin/agentagon" install "$@"
echo "Installed CLI: $ag_bin_dir/agentagon"
case ":$PATH:" in
    *":$ag_bin_dir:"*) ;;
    *) echo "Add $ag_bin_dir to PATH before starting a new coding-agent session." ;;
esac
