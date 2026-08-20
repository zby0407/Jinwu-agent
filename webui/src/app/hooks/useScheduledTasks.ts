"use client";

import { useCallback, useEffect, useState } from "react";
import type { Cron, Run, Thread } from "@langchain/langgraph-sdk";
import { Client } from "@langchain/langgraph-sdk";
import { getConfig } from "@/lib/config";
import {
  browserTimezone,
  classifyScheduledRunStatus,
  createTaskKey,
  finalScheduledFeedback,
  initialScheduledPrompt,
  legacyTaskKeyForPrompt,
  needsScheduledTaskMigration,
  scheduledPromptFromRun,
  type DisplayRunStatus,
} from "@/lib/scheduledTaskUtils.js";

const SCHEDULED_RUN_KIND = "scheduled_task";
const SCHEDULER_GRAPH_ID = "scheduler";
const TASK_KEY_FIELD = "scheduled_task_key";

interface ExtendedCron extends Cron {
  timezone?: string | null;
  on_run_completed?: "delete" | "keep";
  enabled?: boolean;
}

interface SchedulerValues {
  messages?: unknown[];
}

function deploymentSettings(): {
  apiUrl: string;
  headers: Record<string, string>;
} | null {
  const config = getConfig();
  if (!config) return null;
  const apiKey =
    config.langsmithApiKey || process.env.NEXT_PUBLIC_LANGSMITH_API_KEY || "";
  return {
    apiUrl: config.deploymentUrl.replace(/\/+$/, ""),
    headers: apiKey ? { "X-Api-Key": apiKey } : {},
  };
}

function makeClient(): Client | null {
  const settings = deploymentSettings();
  if (!settings) return null;
  return new Client({
    apiUrl: settings.apiUrl,
    defaultHeaders: settings.headers,
  });
}

async function cronRequest<T>(
  path: string,
  init: RequestInit
): Promise<T> {
  const settings = deploymentSettings();
  if (!settings) throw new Error("尚未配置金乌部署。");
  const response = await fetch(`${settings.apiUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...settings.headers,
      ...init.headers,
    },
  });
  if (!response.ok) {
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = typeof body.detail === "string" ? body.detail : "";
    } catch {
      detail = await response.text().catch(() => "");
    }
    throw new Error(
      detail || `定时任务请求失败（HTTP ${response.status}）。`
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export interface ScheduledTask {
  cron_id: string;
  task_key: string;
  name: string;
  prompt: string;
  schedule: string;
  timezone: string;
  enabled: boolean;
  on_run_completed: "delete" | "keep";
  archived: boolean;
  next_run_date: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduledTaskRun {
  run_id: string;
  thread_id: string;
  task_key: string | null;
  trigger: "manual" | "scheduled" | "unknown";
  started_at: string;
  completed_at: string | null;
  status: DisplayRunStatus;
  feedback: string;
}

export interface ScheduledTaskRunPage {
  records: ScheduledTaskRun[];
  hasMore: boolean;
}

function parseCron(cron: ExtendedCron): ScheduledTask {
  const meta = (cron.metadata ?? {}) as Record<string, unknown>;
  return {
    cron_id: cron.cron_id,
    task_key:
      typeof meta[TASK_KEY_FIELD] === "string"
        ? String(meta[TASK_KEY_FIELD])
        : "",
    name:
      typeof meta.name === "string" && meta.name ? meta.name : "未命名任务",
    prompt: typeof meta.prompt === "string" ? meta.prompt : "",
    schedule: cron.schedule,
    timezone:
      typeof cron.timezone === "string" && cron.timezone
        ? cron.timezone
        : "",
    enabled: cron.enabled !== false,
    on_run_completed:
      cron.on_run_completed === "keep" ? "keep" : "delete",
    archived: meta.archived === true,
    next_run_date: cron.next_run_date ?? null,
    created_at: cron.created_at,
    updated_at: cron.updated_at,
  };
}

function taskMetadata(params: {
  taskKey: string;
  name: string;
  prompt: string;
  archived?: boolean;
}): Record<string, unknown> {
  return {
    run_kind: SCHEDULED_RUN_KIND,
    [TASK_KEY_FIELD]: params.taskKey,
    name: params.name,
    prompt: params.prompt,
    scheduled_trigger: "scheduled",
    archived: params.archived === true,
  };
}

async function patchCron(
  cronId: string,
  payload: Record<string, unknown>
): Promise<ScheduledTask> {
  const cron = await cronRequest<ExtendedCron>(
    `/runs/crons/${encodeURIComponent(cronId)}`,
    { method: "PATCH", body: JSON.stringify(payload) }
  );
  return parseCron(cron);
}

async function migrateTask(
  cron: ExtendedCron,
  localTimezone: string
): Promise<ScheduledTask> {
  const existing = parseCron(cron);
  if (!needsScheduledTaskMigration(existing)) return existing;
  const meta = (cron.metadata ?? {}) as Record<string, unknown>;
  const taskKey = existing.task_key || createTaskKey();
  return patchCron(cron.cron_id, {
    timezone: existing.timezone || localTimezone,
    on_run_completed: "keep",
    enabled: cron.enabled !== false,
    metadata: {
      ...meta,
      ...taskMetadata({
        taskKey,
        name: existing.name,
        prompt: existing.prompt,
        archived: existing.archived,
      }),
    },
  });
}

export async function listScheduledTasks(): Promise<ScheduledTask[]> {
  const client = makeClient();
  if (!client) return [];
  const crons = (await client.crons.search({ limit: 200 })) as ExtendedCron[];
  const ownCrons = crons.filter(
    (cron) =>
      (cron.metadata as Record<string, unknown>)?.run_kind ===
      SCHEDULED_RUN_KIND
  );
  const timezone = browserTimezone();
  const tasks = await Promise.all(
    ownCrons.map((cron) => migrateTask(cron, timezone))
  );
  await findAndMigrateLegacyThreads(client, tasks);
  return tasks;
}

export async function createScheduledTask(params: {
  name: string;
  prompt: string;
  schedule: string;
  timezone: string;
}): Promise<ScheduledTask> {
  const taskKey = createTaskKey();
  const cron = await cronRequest<ExtendedCron>("/runs/crons", {
    method: "POST",
    body: JSON.stringify({
      assistant_id: SCHEDULER_GRAPH_ID,
      input: { messages: [{ role: "user", content: params.prompt }] },
      schedule: params.schedule,
      timezone: params.timezone,
      enabled: true,
      on_run_completed: "keep",
      metadata: taskMetadata({
        taskKey,
        name: params.name,
        prompt: params.prompt,
      }),
    }),
  });
  return parseCron(cron);
}

export async function archiveScheduledTask(
  task: ScheduledTask
): Promise<ScheduledTask> {
  return patchCron(task.cron_id, {
    enabled: false,
    metadata: taskMetadata({
      taskKey: task.task_key,
      name: task.name,
      prompt: task.prompt,
      archived: true,
    }),
  });
}

export async function updateScheduledTask(params: {
  cronId: string;
  taskKey: string;
  name: string;
  prompt: string;
  schedule: string;
  timezone: string;
}): Promise<ScheduledTask> {
  return patchCron(params.cronId, {
    schedule: params.schedule,
    timezone: params.timezone,
    input: { messages: [{ role: "user", content: params.prompt }] },
    on_run_completed: "keep",
    enabled: true,
    metadata: taskMetadata({
      taskKey: params.taskKey,
      name: params.name,
      prompt: params.prompt,
    }),
  });
}

export async function runScheduledTaskNow(
  task: ScheduledTask
): Promise<{ run_id: string; thread_id: string }> {
  const client = makeClient();
  if (!client) throw new Error("尚未配置金乌部署。");
  const metadata = {
    ...taskMetadata({
      taskKey: task.task_key,
      name: task.name,
      prompt: task.prompt,
    }),
    scheduled_trigger: "manual",
    scheduled_cron_id: task.cron_id,
  };
  const thread = await client.threads.create({
    graphId: SCHEDULER_GRAPH_ID,
    metadata,
  });
  const run = await client.runs.create(
    thread.thread_id,
    SCHEDULER_GRAPH_ID,
    {
      input: { messages: [{ role: "user", content: task.prompt }] },
      metadata,
    }
  );
  return { run_id: run.run_id, thread_id: thread.thread_id };
}

function threadTaskKey(thread: Thread<SchedulerValues>): string | null {
  const value = thread.metadata?.[TASK_KEY_FIELD];
  return typeof value === "string" && value ? value : null;
}

function threadTrigger(
  thread: Thread<SchedulerValues>
): ScheduledTaskRun["trigger"] {
  const trigger = thread.metadata?.scheduled_trigger;
  if (trigger === "manual" || trigger === "scheduled") return trigger;
  return "unknown";
}

async function recordFromThread(
  client: Client,
  thread: Thread<SchedulerValues>
): Promise<ScheduledTaskRun> {
  const runs = (await client.runs.list(thread.thread_id, {
    limit: 1,
  })) as Run[];
  const run = runs[0];
  const feedback = finalScheduledFeedback(thread.values);
  const status = classifyScheduledRunStatus(
    run?.status ?? thread.status,
    feedback
  );
  return {
    run_id: run?.run_id ?? `thread-${thread.thread_id}`,
    thread_id: thread.thread_id,
    task_key: threadTaskKey(thread),
    trigger: threadTrigger(thread),
    started_at: run?.created_at ?? thread.created_at,
    completed_at:
      status === "pending" || status === "running"
        ? null
        : run?.updated_at ?? thread.updated_at,
    status,
    feedback,
  };
}

async function findAndMigrateLegacyThreads(
  client: Client,
  tasks: ScheduledTask[]
): Promise<Thread<SchedulerValues>[]> {
  const candidates = await client.threads.search<SchedulerValues>({
    metadata: { graph_id: SCHEDULER_GRAPH_ID },
    limit: 200,
    sortBy: "created_at",
    sortOrder: "desc",
  });
  const migrated: Thread<SchedulerValues>[] = [];
  for (const thread of candidates) {
    if (threadTaskKey(thread)) continue;
    let prompt = initialScheduledPrompt(thread.values);
    if (!prompt) {
      const runs = await client.runs.list(thread.thread_id, { limit: 1 });
      prompt = scheduledPromptFromRun(runs[0]);
    }
    const taskKey = legacyTaskKeyForPrompt(tasks, prompt);
    if (!taskKey) continue;
    const task = tasks.find((candidate) => candidate.task_key === taskKey);
    if (!task) continue;
    const metadata = {
      ...thread.metadata,
      ...taskMetadata({
        taskKey,
        name: task.name,
        prompt: task.prompt,
        archived: task.archived,
      }),
      scheduled_trigger: "manual",
      scheduled_cron_id: task.cron_id,
      legacy_scheduled_run: true,
    };
    await client.threads.update(thread.thread_id, { metadata });
    migrated.push({ ...thread, metadata });
  }
  return migrated;
}

export async function listScheduledTaskRuns(params: {
  task: ScheduledTask;
  tasks: ScheduledTask[];
  offset?: number;
  limit?: number;
}): Promise<ScheduledTaskRunPage> {
  const client = makeClient();
  if (!client) return { records: [], hasMore: false };
  const offset = params.offset ?? 0;
  const limit = params.limit ?? 20;
  const [tagged, migrated] = await Promise.all([
    client.threads.search<SchedulerValues>({
      metadata: { [TASK_KEY_FIELD]: params.task.task_key },
      limit: 200,
      sortBy: "created_at",
      sortOrder: "desc",
    }),
    findAndMigrateLegacyThreads(client, params.tasks),
  ]);
  const unique = new Map<string, Thread<SchedulerValues>>();
  [...tagged, ...migrated]
    .filter((thread) => threadTaskKey(thread) === params.task.task_key)
    .forEach((thread) => unique.set(thread.thread_id, thread));
  const threads = [...unique.values()].sort(
    (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)
  );
  const page = threads.slice(offset, offset + limit);
  const records = await Promise.all(
    page.map((thread) => recordFromThread(client, thread))
  );
  return { records, hasMore: threads.length > offset + limit };
}

export async function listUnassignedScheduledRuns(
  tasks: ScheduledTask[],
  limit = 20
): Promise<ScheduledTaskRun[]> {
  const client = makeClient();
  if (!client) return [];
  const candidates = await client.threads.search<SchedulerValues>({
    metadata: { graph_id: SCHEDULER_GRAPH_ID },
    limit: 200,
    sortBy: "created_at",
    sortOrder: "desc",
  });
  const unassigned: Thread<SchedulerValues>[] = [];
  for (const thread of candidates) {
    if (threadTaskKey(thread)) continue;
    let prompt = initialScheduledPrompt(thread.values);
    if (!prompt) {
      const runs = await client.runs.list(thread.thread_id, { limit: 1 });
      prompt = scheduledPromptFromRun(runs[0]);
    }
    if (prompt && legacyTaskKeyForPrompt(tasks, prompt) === null) {
      unassigned.push(thread);
    }
  }
  return Promise.all(
    unassigned.slice(0, limit).map((thread) => recordFromThread(client, thread))
  );
}

export function useScheduledTasks(): {
  tasks: ScheduledTask[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
} {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rev, setRev] = useState(0);

  const refresh = useCallback(() => setRev((revision) => revision + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listScheduledTasks()
      .then((result) => {
        if (!cancelled) {
          setTasks(result);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "加载定时任务失败。"
          );
          setTasks([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [rev]);

  return { tasks, loading, error, refresh };
}
