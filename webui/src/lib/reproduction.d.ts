export interface ReproductionRun {
  case_id: "H1" | "H2";
  thread_id: string;
  run_id: string;
  workspace: string;
  prompt_sha256: string;
}

export interface ReproductionError {
  case_id?: string;
  stage?: string;
  message?: string;
}

export interface ReproductionLaunch {
  schema_version: "jw-reproduction-launch-v1";
  suite_id: "solar-h1-h2-v1";
  batch_id: string;
  status: "submitted" | "partial" | "failed";
  model: { name: string; provider: string };
  runs: ReproductionRun[];
  errors: ReproductionError[];
}

export const REPRODUCTION_SUITE_ID: "solar-h1-h2-v1";
export const REPRODUCTION_SCHEMA_VERSION: "jw-reproduction-launch-v1";

export function launchSolarH1H2(options: {
  deploymentUrl: string;
  apiKey?: string;
  fetchImpl?: typeof fetch;
}): Promise<ReproductionLaunch>;
