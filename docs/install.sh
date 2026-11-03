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
            # wget follows no redirect by itself: each hop is read from its answer and must stay on HTTPS.
            wget_url=https://astral.sh/uv/install.sh
            wget_hops=0
            while :; do
                wget_status=0
                wget_answer=$(wget --https-only --max-redirect=0 --server-response \
                    -O "$temporary/uv-install.sh" "$wget_url" 2>&1) || wget_status=$?
                [ "$wget_status" -eq 0 ] && break
                wget_next=
                while IFS= read -r wget_line; do
                    wget_line=${wget_line#"${wget_line%%[! ]*}"}
                    case "$wget_line" in
                        Location:\ *|location:\ *) wget_next=${wget_line#*: }; break ;;
                        *) ;;
                    esac
                done <<EOF
$wget_answer
EOF
                wget_next=${wget_next%"$(printf '\r')"}
                wget_url=${wget_next% \[following\]}
                wget_hops=$((wget_hops + 1))
                case "$wget_url" in
                    https://*) [ "$wget_hops" -le 3 ] || wget_url= ;;
                    *) wget_url= ;;
                esac
                if [ -z "$wget_url" ]; then
                    printf '%s\n' 'The download failed or tried to leave HTTPS. Install curl, then run this installer again.' >&2
                    exit 1
                fi
            done
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
