export interface DeploymentConfig {
  deploymentUrl: string;
  assistantId: string;
  langsmithApiKey?: string;
}

// The UI always talks to the 金乌 main agent. writing-agent and
// data-analysis-agent are internal sub-agents and are intentionally not
// user-selectable, so the assistant is fixed rather than configurable.
export const DEFAULT_ASSISTANT_ID = "EvoScientist";

const CONFIG_KEY = "jinwu-config";

export function getConfig(): DeploymentConfig | null {
  if (typeof window === "undefined") return null;

  const stored = localStorage.getItem(CONFIG_KEY);
  if (!stored) return null;

  try {
    const parsed = JSON.parse(stored) as DeploymentConfig;
    // A stored config with no usable deployment URL would make the SDK send
    // requests to the app's own origin (404s). Treat it as unconfigured so
    // the config dialog reappears instead.
    if (!parsed.deploymentUrl?.trim()) return null;
    // Always pin the assistant to the 金乌 main agent.
    return { ...parsed, assistantId: DEFAULT_ASSISTANT_ID };
  } catch {
    return null;
  }
}

export function saveConfig(config: DeploymentConfig): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(CONFIG_KEY, JSON.stringify(config));
}
