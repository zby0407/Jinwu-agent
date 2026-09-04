#!/bin/sh

set -eu

jw_source=${JW_INSTALL_SOURCE:-https://github.com/zby0407/Jinwu-agent/archive/refs/heads/main.tar.gz}
uv_installer=https://astral.sh/uv/install.sh

say() {
    printf '%s\n' "[jw] $*"
}

fail() {
    printf '%s\n' "[jw] error: $*" >&2
    exit 1
}

# Reuse uv even when its user-level install directory is not on PATH yet.
if command -v uv >/dev/null 2>&1; then
    jw_uv=$(command -v uv)
elif [ -n "${UV_INSTALL_DIR:-}" ] && [ -x "${UV_INSTALL_DIR}/uv" ]; then
    jw_uv=${UV_INSTALL_DIR}/uv
elif [ -n "${HOME:-}" ] && [ -x "${HOME}/.local/bin/uv" ]; then
    jw_uv=${HOME}/.local/bin/uv
elif [ -n "${HOME:-}" ] && [ -x "${HOME}/.cargo/bin/uv" ]; then
    jw_uv=${HOME}/.cargo/bin/uv
else
    [ -n "${HOME:-}" ] || fail 'HOME is not set, so uv cannot be installed for this user.'

    if [ -n "${XDG_BIN_HOME:-}" ]; then
        jw_uv_dir=${XDG_BIN_HOME}
    else
        jw_uv_dir=${HOME}/.local/bin
    fi

    say 'uv was not found; installing it with the official Astral installer...'
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf "${uv_installer}" | env UV_INSTALL_DIR="${jw_uv_dir}" sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- "${uv_installer}" | env UV_INSTALL_DIR="${jw_uv_dir}" sh
    else
        fail 'curl or wget is required to download uv.'
    fi

    jw_uv=${jw_uv_dir}/uv
    [ -x "${jw_uv}" ] || fail "uv was installed but was not found at ${jw_uv}."
fi

say "installing JW from ${jw_source}..."
"${jw_uv}" tool install --reinstall "${jw_source}"

# Persist uv's tool directory on PATH when the shell is supported. Failure here
# is non-fatal because the installation itself has already completed.
"${jw_uv}" tool update-shell >/dev/null 2>&1 || true
jw_bin_dir=$("${jw_uv}" tool dir --bin)

say 'installation complete.'
case ":${PATH}:" in
    *":${jw_bin_dir}:"*)
        say 'run: jw onboard'
        ;;
    *)
        say "open a new terminal, then run: jw onboard"
        say "for this shell only, run: export PATH=\"${jw_bin_dir}:\$PATH\""
        ;;
esac
