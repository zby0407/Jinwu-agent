#!/bin/sh

set -eu

jw_install_source=${JW_INSTALL_SOURCE:-https://github.com/zby0407/Jinwu-agent/archive/refs/heads/main.tar.gz}
uv_installer=https://astral.sh/uv/install.sh
jw_webui_ready=0
jw_stage_dir=
jw_source_dir=
jw_previous_source=
jw_managed_source=0
jw_install_complete=0

say() {
    printf '%s\n' "[jw] $*"
}

fail() {
    printf '%s\n' "[jw] error: $*" >&2
    exit 1
}

cleanup() {
    jw_status=$?
    trap - EXIT HUP INT TERM
    if [ "${jw_managed_source}" -eq 1 ]; then
        if [ "${jw_install_complete}" -eq 0 ]; then
            rm -rf "${jw_source_dir}"
            if [ -n "${jw_previous_source}" ] && [ -d "${jw_previous_source}" ]; then
                mv "${jw_previous_source}" "${jw_source_dir}"
            fi
        elif [ -n "${jw_previous_source}" ] && [ -d "${jw_previous_source}" ]; then
            rm -rf "${jw_previous_source}"
        fi
    fi
    if [ -n "${jw_stage_dir}" ] && [ -d "${jw_stage_dir}" ]; then
        rm -rf "${jw_stage_dir}"
    fi
    exit "${jw_status}"
}

trap cleanup EXIT
trap 'exit 1' HUP INT TERM

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

if [ -d "${jw_install_source}" ]; then
    jw_source_dir=$(cd "${jw_install_source}" && pwd)
else
    command -v tar >/dev/null 2>&1 || fail 'tar is required to unpack JW.'
    if [ -n "${JW_INSTALL_DIR:-}" ]; then
        jw_install_root=${JW_INSTALL_DIR}
    elif [ -n "${XDG_DATA_HOME:-}" ]; then
        jw_install_root=${XDG_DATA_HOME}/jw-agent
    else
        [ -n "${HOME:-}" ] || fail 'HOME is not set, so JW cannot be installed for this user.'
        jw_install_root=${HOME}/.local/share/jw-agent
    fi

    mkdir -p "${jw_install_root}"
    jw_stage_dir=$(mktemp -d "${jw_install_root}/download.XXXXXX")
    jw_archive=${jw_stage_dir}/jw.tar.gz
    jw_staged_source=${jw_stage_dir}/source
    mkdir -p "${jw_staged_source}"

    say "downloading JW from ${jw_install_source}..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf "${jw_install_source}" -o "${jw_archive}"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "${jw_archive}" "${jw_install_source}"
    else
        fail 'curl or wget is required to download JW.'
    fi
    tar -xzf "${jw_archive}" -C "${jw_staged_source}" --strip-components=1

    # Keep the complete repository because research contracts and WebUI files
    # are runtime resources. The previous source is restored if setup fails.
    jw_source_dir=${jw_install_root}/source
    jw_previous_source=${jw_install_root}/source.previous
    rm -rf "${jw_previous_source}"
    if [ -e "${jw_source_dir}" ]; then
        mv "${jw_source_dir}" "${jw_previous_source}"
    fi
    mv "${jw_staged_source}" "${jw_source_dir}"
    jw_managed_source=1
fi

if [ "${JW_SKIP_WEBUI_BUILD:-0}" != "1" ]; then
    if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
        jw_node_major=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || printf '0')
        case "${jw_node_major}" in
            ''|*[!0-9]*) jw_node_major=0 ;;
        esac
        if [ "${jw_node_major}" -ge 20 ]; then
            say 'building the JW WebUI...'
            (
                cd "${jw_source_dir}/webui"
                npm ci
                npm run build
            )
            if [ "${jw_managed_source}" -eq 1 ]; then
                rm -rf "${jw_source_dir}/webui/node_modules" "${jw_source_dir}/webui/.next"
            fi
            jw_webui_ready=1
        else
            say "Node.js 20+ is required for WebUI; found $(node --version)."
        fi
    else
        say 'Node.js 20+ and npm were not found; installing CLI/TUI without WebUI.'
    fi
fi

say "installing JW from ${jw_source_dir}..."
"${jw_uv}" tool install --reinstall --editable "${jw_source_dir}"
jw_install_complete=1

# Persist uv's tool directory on PATH when the shell is supported. Failure here
# is non-fatal because the installation itself has already completed.
"${jw_uv}" tool update-shell >/dev/null 2>&1 || true
jw_bin_dir=$("${jw_uv}" tool dir --bin)

say 'installation complete.'
if [ "${jw_webui_ready}" -eq 1 ]; then
    say 'WebUI is ready.'
fi
case ":${PATH}:" in
    *":${jw_bin_dir}:"*)
        say 'run: jw onboard'
        ;;
    *)
        say "open a new terminal, then run: jw onboard"
        say "for this shell only, run: export PATH=\"${jw_bin_dir}:\$PATH\""
        ;;
esac
