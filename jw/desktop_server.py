"""Small desktop-only deploy entry; CLI commands and interactive UI stay unloaded."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")

    from ._winloop import ensure_proactor_event_loop_policy
    from .config import apply_config_to_env, get_effective_config
    from .langgraph_dev.manager import start_langgraph_dev, stop_langgraph_dev

    ensure_proactor_event_loop_policy()
    workspace = Path(args.workdir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace)
    config = get_effective_config()
    apply_config_to_env(config)
    logging.basicConfig(level=str(config.log_level).upper())
    shutdown = threading.Event()
    previous = {
        sig: signal.signal(sig, lambda *_: shutdown.set())
        for sig in (signal.SIGINT, signal.SIGTERM)
    }
    proc = None
    proxy = None
    try:
        if config.anthropic_auth_mode == "oauth" or config.openai_auth_mode == "oauth":
            from .ccproxy_manager import maybe_start_ccproxy

            proxy = maybe_start_ccproxy(config)
        proc = start_langgraph_dev(
            workspace_dir=workspace,
            port=args.port,
            deploy_mode=True,
            tunnel=False,
            file_persistence=config.langgraph_dev_file_persistence,
            jobs_per_worker=config.langgraph_dev_jobs_per_worker,
        )
        while not shutdown.wait(0.5):
            if proc.poll() is not None:
                return proc.returncode or 1
        return 0
    finally:
        if proc is not None:
            stop_langgraph_dev(proc)
        if proxy is not None:
            from .ccproxy_manager import stop_ccproxy

            stop_ccproxy(proxy)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
