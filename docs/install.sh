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
    install_dir=${UV_INSTALL_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}
    case "$install_dir" in
        /*) ;;
        *) printf '%s\n' 'UV_INSTALL_DIR must be an absolute path.' >&2; exit 1 ;;
    esac
    uv_bin=$install_dir/uv
    if [ ! -x "$uv_bin" ]; then
        # Plain HTTP must never deliver the script that is executed below: no redirect may leave HTTPS.
        # BusyBox wget cannot enforce that (no --https-only), so it does not count as a downloader.
        downloader=
        if command -v curl >/dev/null 2>&1; then
            downloader=curl
        elif command -v wget >/dev/null 2>&1; then
            case "$(wget --help 2>&1 || true)" in
                *--https-only*) downloader=wget ;;
                *) ;;
            esac
        fi
        if [ -z "$downloader" ]; then
            printf '%s\n' 'Install curl (or GNU wget), then run this installer again.' >&2
            exit 1
        fi
        temporary=$(mktemp -d "${TMPDIR:-/tmp}/heinzel-install.XXXXXX")
        trap 'rm -f "$temporary/uv-install.sh"; rmdir "$temporary"' 0
        trap 'exit 1' 1 2 15
        printf '%s\n' 'Installing uv using Astral’s official installer…' >&2
        if [ "$downloader" = curl ]; then
            curl --proto '=https' --proto-redir '=https' --tlsv1.2 -LsSf \
                https://astral.sh/uv/install.sh -o "$temporary/uv-install.sh"
        else
            wget -q --https-only https://astral.sh/uv/install.sh -O "$temporary/uv-install.sh"
        fi
        UV_INSTALL_DIR="$install_dir" sh "$temporary/uv-install.sh" >&2
        if [ ! -x "$uv_bin" ]; then
            printf '%s\n' 'uv did not install successfully. See https://docs.astral.sh/uv/getting-started/installation/' >&2
            exit 1
        fi
        rm -f "$temporary/uv-install.sh"
        rmdir "$temporary"
        trap - 0 1 2 15
    fi
fi

# Use the resolved uv immediately; no shell restart or separate Python installation is needed.
PATH="$(dirname "$uv_bin"):$PATH"
export PATH
# A source checkout must not shadow the selected SDK before its launcher can isolate itself.
unset PYTHONPATH PYTHONHOME
exec "$uv_bin" tool run heinzel@latest "$@"
