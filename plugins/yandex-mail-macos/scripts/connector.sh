#!/bin/zsh
set -euo pipefail

script_dir=${0:A:h}
mode=${1:-serve}

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
        print -u2 'Run the connect-yandex-mail skill to set up Python.'
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

check="$script_dir/check_connection.py"
case "$mode" in
    serve) exec "$python_exe" -E -s -B "$script_dir/../server/yandex_mail_mcp.py" ;;
    check) exec "$python_exe" -I "$check" ;;
    read-attachment)
        [[ $# == 2 ]] || exit 64
        exec "$python_exe" -I "$script_dir/read_attachment_file.py" -- "$2"
        ;;
    setup)
        if [[ ${2:-} != --reconnect ]]; then
            code=0
            status=$("$python_exe" -I "$check") || code=$?
            if [[ $code != 3 ]]; then
                print -r -- "$status"
                exit "$code"
            fi
        fi
        "$python_exe" -I "$script_dir/setup_credentials.py"
        exec "$python_exe" -I "$check"
        ;;
    *) exit 64 ;;
esac
