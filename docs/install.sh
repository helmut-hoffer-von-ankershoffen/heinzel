#!/bin/sh
# Install prerequisites if needed, then open the verified Heinzelmännchen standard app.
# macOS and Linux; no administrator access or system Python required.
set -eu

if [ "${1:-}" = --help ]; then
    printf '%s\n' 'Install Heinzelmännchen on macOS or Linux, including uv if missing.' \
        'Usage: sh install.sh [arguments to heinzel]' \
        'Existing uv installations are reused. Default plugins install automatically.'
    exit 0
fi

case "$(uname -s)" in
    Darwin|Linux) ;;
    *) printf '%s\n' 'This installer supports macOS and Linux. Windows is not supported.' >&2; exit 1 ;;
esac

if command -v uv >/dev/null 2>&1; then
    uv_bin=$(command -v uv)
else
    if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
        printf '%s\n' 'Install curl or wget, then run this installer again.' >&2
        exit 1
    fi
    install_dir=${UV_INSTALL_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}
    case "$install_dir" in
        /*) ;;
        *) printf '%s\n' 'UV_INSTALL_DIR must be an absolute path.' >&2; exit 1 ;;
    esac
    temporary=$(mktemp -d "${TMPDIR:-/tmp}/heinzel-install.XXXXXX")
    trap 'rm -f "$temporary/uv-install.sh"; rmdir "$temporary"' 0
    trap 'exit 1' 1 2 15
    printf '%s\n' 'Installing uv using Astral’s official installer…' >&2
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh -o "$temporary/uv-install.sh"
    else
        wget -q https://astral.sh/uv/install.sh -O "$temporary/uv-install.sh"
    fi
    UV_INSTALL_DIR="$install_dir" sh "$temporary/uv-install.sh" >&2
    uv_bin=$install_dir/uv
    if [ ! -x "$uv_bin" ]; then
        printf '%s\n' 'uv did not install successfully. See https://docs.astral.sh/uv/getting-started/installation/' >&2
        exit 1
    fi
    rm -f "$temporary/uv-install.sh"
    rmdir "$temporary"
    trap - 0 1 2 15
fi

# Use the resolved uv immediately; no shell restart or separate Python installation is needed.
PATH="$(dirname "$uv_bin"):$PATH"
export PATH
exec "$uv_bin" tool run heinzel@latest "$@"
