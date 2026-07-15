import useSWRInfinite from "swr/infinite";
import type { Thread } from "@langchain/langgraph-sdk";
import { Client } from "@langchain/langgraph-sdk";
import { getConfig } from "@/lib/config";

export interface ThreadItem {
  id: string;
  updatedAt: Date;
  status: Thread["status"];
  title: string;
  description: string;
  assistantId?: string;
  pinned: boolean;
  /** True when any of the thread's pending interrupts is an `ask_user` —
   *  i.e. the agent is asking the user a question that auto-approve can't
   *  resolve. Used by the sidebar to keep these threads in "Requiring
   *  Attention" even when auto-approve is on. */
  needsUserInput: boolean;
}

const DEFAULT_PAGE_SIZE = 20;

export function useThreads(props: {
  status?: Thread["status"];
  limit?: number;
}) {
  const pageSize = props.limit || DEFAULT_PAGE_SIZE;

  return useSWRInfinite(
    (pageIndex: number, previousPageData: ThreadItem[] | null) => {
      const config = getConfig();
      const apiKey =
        config?.langsmithApiKey ||
        process.env.NEXT_PUBLIC_LANGSMITH_API_KEY ||
        "";

      if (!config) {
        return null;
      }

      // If the previous page returned no items, we've reached the end
      if (previousPageData && previousPageData.length === 0) {
        return null;
      }

      return {
        kind: "threads" as const,
        pageIndex,
        pageSize,
        deploymentUrl: config.deploymentUrl,
        assistantId: config.assistantId,
        apiKey,
        status: props?.status,
      };
    },
    async ({
      deploymentUrl,
      assistantId,
      apiKey,
      status,
      pageIndex,
      pageSize,
    }: {
      kind: "threads";
      pageIndex: number;
      pageSize: number;
      deploymentUrl: string;
      assistantId: string;
      apiKey: string;
      status?: Thread["status"];
    }) => {
      const client = new Client({
        apiUrl: deploymentUrl,
        defaultHeaders: apiKey ? { "X-Api-Key": apiKey } : {},
      });

      // Always scope the thread list to the selected assistant so we never
      // show threads spawned by async sub-agents (e.g. writing-agent,
      // data-analysis-agent) that share the same deployment store.
      //
      // Every thread carries both `graph_id` (the graph name) and
      // `assistant_id` (a deterministic UUID per graph) in its metadata.
      // A deployed UUID is matched against `assistant_id`; a graph name
      // (the local-dev case, e.g. "EvoScientist") is matched against
      // `graph_id`.
      const isUUID =
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
          assistantId
        );
      const metadata = isUUID
        ? { assistant_id: assistantId }
        : { graph_id: assistantId };

      const threads = await client.threads.search({
        limit: pageSize,
        offset: pageIndex * pageSize,
        sortBy: "updated_at" as const,
        sortOrder: "desc" as const,
        status,
        metadata,
      });

      return threads.map((thread): ThreadItem => {
        let title = "Untitled Thread";
        let description = "";

        try {
          if (thread.values && typeof thread.values === "object") {
            const values = thread.values as any;
            const messages: any[] = Array.isArray(values.messages)
              ? values.messages
              : [];
            // Extract readable text from a string OR an array of content blocks
            // (the latter is common for multi-part / attachment messages).
            const textOf = (content: any): string => {
              if (typeof content === "string") return content;
              if (Array.isArray(content))
                return content
                  .map((p: any) => (typeof p?.text === "string" ? p.text : ""))
                  .join("");
              return "";
            };
            const firstHumanMessage = messages.find(
              (m: any) => m.type === "human"
            );
            const humanText = textOf(firstHumanMessage?.content).trim();
            if (humanText) {
              title =
                humanText.slice(0, 50) + (humanText.length > 50 ? "…" : "");
            }
            // Preview = the first AI message that actually has text. Agentic
            // threads often open with tool-call-only AI messages (empty
            // content), so picking the literal first AI message would leave the
            // row blank (looking like an "empty" thread). Also join all text
            // parts rather than just content[0].
            for (const m of messages) {
              if (m?.type !== "ai") continue;
              const t = textOf(m.content).trim();
              if (t) {
                description = t.slice(0, 100);
                break;
              }
            }
            // If the first human message yielded no text (odd/attachment-only
            // shape), fall back to the AI preview so the row isn't an opaque
            // "Untitled Thread".
            if (title === "Untitled Thread" && description) {
              title =
                description.slice(0, 50) + (description.length > 50 ? "…" : "");
            }
          }
        } catch {
          // Fallback to thread ID
          title = `Thread ${thread.thread_id.slice(0, 8)}`;
        }

        // A user-set custom title (stored in metadata via rename) always wins.
        const customTitle = (
          thread.metadata as Record<string, unknown> | undefined
        )?.title;
        if (typeof customTitle === "string" && customTitle.trim()) {
          title = customTitle.trim();
        }

        // Pinned state is stored in thread metadata (like the custom title),
        // so it persists across reloads/devices via the backend store.
        const pinned =
          (thread.metadata as Record<string, unknown> | undefined)?.pinned ===
          true;

        // Walk `thread.interrupts` (Record<task_id, Interrupt[]>) and flag any
        // value with `type: "ask_user"`. The auto-approver can't resolve those,
        // so the sidebar should keep the row in "Requiring Attention" even when
        // auto-approve is on for the thread.
        let needsUserInput = false;
        const interrupts = thread.interrupts as
          | Record<string, Array<{ value?: unknown }>>
          | undefined;
        if (interrupts && typeof interrupts === "object") {
          for (const list of Object.values(interrupts)) {
            if (!Array.isArray(list)) continue;
            for (const ir of list) {
              const value = ir?.value as { type?: unknown } | undefined;
              if (value && value.type === "ask_user") {
                needsUserInput = true;
                break;
              }
            }
            if (needsUserInput) break;
          }
        }

        return {
          id: thread.thread_id,
          updatedAt: new Date(thread.updated_at),
          status: thread.status,
          title,
          description,
          assistantId,
          pinned,
          needsUserInput,
        };
      });
    },
    {
      revalidateFirstPage: true,
      revalidateOnFocus: true,
    }
  );
}

// --- Thread mutations (used by the thread list's rename / delete actions) ---

function makeThreadsClient(): Client | null {
  const config = getConfig();
  if (!config) return null;
  const apiKey =
    config.langsmithApiKey || process.env.NEXT_PUBLIC_LANGSMITH_API_KEY || "";
  return new Client({
    apiUrl: config.deploymentUrl,
    defaultHeaders: apiKey ? { "X-Api-Key": apiKey } : {},
  });
}

/** Permanently delete a thread. Throws if no deployment is configured. */
export async function deleteThread(id: string): Promise<void> {
  const client = makeThreadsClient();
  if (!client) throw new Error("No 金乌 deployment configured.");
  await client.threads.delete(id);
}

async function updateThreadMetadata(
  client: Client,
  id: string,
  patch: Record<string, unknown>
): Promise<void> {
  const thread = await client.threads.get(id);
  const metadata = {
    ...((thread.metadata as Record<string, unknown> | undefined) ?? {}),
    ...patch,
  };
  await client.threads.update(id, { metadata });
}

/**
 * Rename a thread by storing a custom title in its metadata. The LangGraph
 * thread PATCH replaces metadata, so read + merge first to preserve graph_id /
 * assistant_id filter keys.
 */
export async function renameThread(id: string, title: string): Promise<void> {
  const client = makeThreadsClient();
  if (!client) throw new Error("No 金乌 deployment configured.");
  await updateThreadMetadata(client, id, { title });
}

/**
 * Pin or unpin a thread by storing a `pinned` flag in its metadata. Preserve
 * the rest of the metadata for the same reason as `renameThread`.
 */
export async function pinThread(id: string, pinned: boolean): Promise<void> {
  const client = makeThreadsClient();
  if (!client) throw new Error("No 金乌 deployment configured.");
  await updateThreadMetadata(client, id, { pinned });
}

/**
 * Persist (or clear) the per-thread model override. Pass `null` to remove the
 * key so the thread reverts to the deployment-default model. Reads on
 * subsequent runs flow through `useChat` → `stream.submit({ config: ... })`,
 * which the backend's `configurable_model` middleware resolves per request.
 */
export async function setThreadModelOverride(
  id: string,
  override: { model: string; model_provider?: string } | null
): Promise<void> {
  const client = makeThreadsClient();
  if (!client) throw new Error("No 金乌 deployment configured.");
  // Passing `null` here keeps the key present in metadata but explicitly
  // un-set, which matches how langgraph treats absence-vs-null in the
  // configurable middleware (`getattr(cfg, "model", None)` accepts both).
  await updateThreadMetadata(client, id, { model_override: override });
}

// Strip characters that are unsafe in filenames on Windows/macOS/Linux, then
// collapse whitespace. Keep this lenient — we just need a valid filename, not
// a slug.
function slugifyForFilename(input: string): string {
  return (
    input
      // Stripping control chars is the point — silence the lint rule.
      // eslint-disable-next-line no-control-regex
      .replace(/[\\/:*?"<>|\u0000-\u001F]+/g, "")
      .trim()
      .replace(/\s+/g, "-")
      .slice(0, 80)
  );
}

/**
 * Download the full thread state as JSON. We fetch via `client.threads.get`
 * which returns the thread plus its current `values` (every message including
 * tool calls, sub-agent output, and assistant thinking) — i.e. everything the
 * backend has, suitable for debugging.
 */
export async function exportThread(
  id: string,
  filenameHint?: string
): Promise<void> {
  const client = makeThreadsClient();
  if (!client) throw new Error("No 金乌 deployment configured.");
  const thread = await client.threads.get(id);
  const blob = new Blob([JSON.stringify(thread, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const safeName =
    (filenameHint && slugifyForFilename(filenameHint)) ||
    `thread-${id.slice(0, 8)}`;
  const a = document.createElement("a");
  a.href = url;
  a.download = `${safeName}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Defer URL revocation a tick so Safari/Firefox finish the download trigger.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
