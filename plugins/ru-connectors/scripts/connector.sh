#!/bin/zsh
set -euo pipefail

script_dir=${0:A:h}
provider=${1:?missing provider}
mode=${2:-serve}
case "$provider" in
    yandex-mail) server=yandex_mail_mcp.py ;;
    mail-ru) server=mail_ru_mcp.py ;;
    *) exit 64 ;;
esac
provider_dir="$script_dir/../$provider"

find_python() {
    local candidate
    for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 $(command -v python3 || true) /usr/bin/python3; do
        [[ -x "$candidate" ]] || continue
        if [[ "$candidate" == /usr/bin/python3 ]] && ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        if "$candidate" -I -c 'import imaplib, smtplib, sqlite3, ssl, sys; assert sys.version_info >= (3, 9); ssl.create_default_context()' >/dev/null 2>&1; then
            print -r -- "$candidate"
            return 0
        fi
    done
    return 1
}

if ! python_exe=$(find_python); then
    if [[ "$mode" != setup ]]; then
        print -u2 'Run the connect-russian-mail skill to set up Python.'
        exit 20
    fi
    brew_exe=$(command -v brew || true)
    if [[ -z "$brew_exe" ]]; then
        for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
            if [[ -x "$candidate" ]]; then
                brew_exe=$candidate
                break
            fi
        done
    fi
    if [[ -z "$brew_exe" ]]; then
        print -u2 'Python 3.9+ is required. Install Python from https://www.python.org/downloads/macos/ or install Apple Command Line Tools, then rerun setup.'
        exit 20
    fi
    "$brew_exe" install python
    python_exe=$(find_python)
fi

check="$provider_dir/scripts/check_connection.py"
case "$mode" in
    serve) exec "$python_exe" -E -s -B "$provider_dir/server/$server" ;;
    check) exec "$python_exe" -I "$check" ;;
    read-attachment)
        [[ "$provider" == yandex-mail && $# == 3 ]] || exit 64
        exec "$python_exe" -I "$provider_dir/scripts/read_attachment_file.py" -- "$3"
        ;;
    setup)
        if [[ ${3:-} != --reconnect ]]; then
            code=0
            status=$("$python_exe" -I "$check") || code=$?
            if [[ $code != 3 ]]; then
                print -r -- "$status"
                exit "$code"
            fi
        fi
        "$python_exe" -I "$provider_dir/scripts/setup_macos.py"
        exec "$python_exe" -I "$check"
        ;;
    *) exit 64 ;;
esac
