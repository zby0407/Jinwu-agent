import { homedir } from "os";
import { join, resolve } from "path";

/** Expand a leading `~` without touching the filesystem. */
function expandHome(value: string): string {
  return value.startsWith("~") ? join(homedir(), value.slice(1)) : resolve(value);
}

/**
 * JW configuration directory, matching Python's `get_config_dir()`:
 * `XDG_CONFIG_HOME/jw` when set, otherwise `~/.config/jw`.
 * The desktop supervisor sets `XDG_CONFIG_HOME` inside its per-user data root.
 */
export function jwConfigDir(): string {
  const xdg = process.env.XDG_CONFIG_HOME;
  const base = xdg && xdg.trim() ? xdg : join(homedir(), ".config");
  return join(base, "jw");
}

export function jwConfigPath(): string {
  return join(jwConfigDir(), "config.yaml");
}

/**
 * Global application data directory, matching Python's `paths.DATA_DIR`.
 * `JW_DATA_DIR` is the supported desktop override; `agent_DATA_DIR` is kept as
 * a compatibility fallback for older WebUI deployments.
 */
export function jwDataDir(): string {
  const env = process.env.JW_DATA_DIR || process.env.agent_DATA_DIR;
  if (env && env.trim()) return expandHome(env);
  return join(homedir(), ".jw");
}

/** Sidecar written by `langgraph_dev.manager` under the config directory. */
export function workspaceSidecarPath(): string {
  return join(jwConfigDir(), "langgraph_dev.workspace.json");
}
