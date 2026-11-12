#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
case "$(uname -s)" in
    Darwin)
        exec /bin/zsh "$script_dir/connector.sh" "$1" serve
        ;;
    MINGW*|MSYS*)
        windows_script=$(cygpath -w "$script_dir/connector.ps1")
        exec env MSYS2_ARG_CONV_EXCL='*' powershell.exe -NoLogo -NoProfile -NonInteractive \
            -ExecutionPolicy Bypass -File "$windows_script" -Provider "$1" -Mode Serve
        ;;
    *)
        printf '%s\n' 'ru-connectors requires macOS or native Windows with Git for Windows.' >&2
        exit 20
        ;;
esac
