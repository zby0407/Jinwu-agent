/**
 * pi extension that overrides built-in read/bash tools and forwards execution
 * to the EvoScientist Python backend via a Unix domain socket.
 *
 * Additionally exposes EvoScientist capabilities: full filesystem tools
 * (write, edit, ls, grep, glob), memory observation tools, schedule tools,
 * and skill management.
 */

const net = require("net");
const { Type } = require("typebox");

const SOCKET_PATH = process.env.EVOSCIENTIST_PI_TOOL_SOCKET;

function sendRequest(request) {
  return new Promise((resolve, reject) => {
    if (!SOCKET_PATH) {
      reject(new Error("EVOSCIENTIST_PI_TOOL_SOCKET is not set"));
      return;
    }
    const client = net.createConnection(SOCKET_PATH, () => {
      client.write(JSON.stringify(request) + "\n");
    });
    let buffer = "";
    client.on("data", (data) => {
      buffer += data.toString("utf-8");
    });
    client.on("end", () => {
      try {
        resolve(JSON.parse(buffer.trim()));
      } catch (err) {
        reject(new Error(`Invalid JSON from tool server: ${err.message}`));
      }
    });
    client.on("error", reject);
  });
}

async function executeTool(name, toolCallId, params) {
  const response = await sendRequest({
    id: toolCallId,
    tool: name,
    args: params,
  });
  if (!response.success) {
    return { content: response.error || "Tool execution failed", isError: true };
  }
  return response.result;
}

function registerBridgeTool(api, name, parameters, description) {
  api.registerTool({
    name,
    label: name,
    description,
    parameters,
    executionMode: "blocking",
    execute: async (toolCallId, params, _signal, _onUpdate, _ctx) => {
      return executeTool(name, toolCallId, params);
    },
  });
}

module.exports = function piBridgeExtension(api) {
  // -------------------------------------------------------------------------
  // Filesystem tools (routed to EvoScientist sandbox backend)
  // -------------------------------------------------------------------------

  registerBridgeTool(
    api,
    "read",
    Type.Object({
      path: Type.String({ description: "Path to the file to read" }),
      offset: Type.Optional(
        Type.Number({ description: "Line number to start reading from (1-indexed)" })
      ),
      limit: Type.Optional(
        Type.Number({ description: "Maximum number of lines to read" })
      ),
    }),
    "Read a file through the EvoScientist Python sandbox backend."
  );

  registerBridgeTool(
    api,
    "bash",
    Type.Object({
      command: Type.String({ description: "Shell command to execute" }),
      timeout: Type.Optional(
        Type.Number({ description: "Timeout in seconds" })
      ),
    }),
    "Execute a shell command through the EvoScientist Python sandbox backend."
  );

  registerBridgeTool(
    api,
    "write",
    Type.Object({
      path: Type.String({ description: "Path to the file to write" }),
      content: Type.String({ description: "Full file contents to write" }),
    }),
    "Write a file through the EvoScientist Python sandbox backend."
  );

  registerBridgeTool(
    api,
    "edit",
    Type.Object({
      path: Type.String({ description: "Path to the file to edit" }),
      old_string: Type.String({ description: "Text to replace" }),
      new_string: Type.String({ description: "Replacement text" }),
      replace_all: Type.Optional(
        Type.Boolean({ description: "Replace all occurrences", default: false })
      ),
    }),
    "Edit a file through the EvoScientist Python sandbox backend."
  );

  registerBridgeTool(
    api,
    "ls",
    Type.Object({
      path: Type.String({ description: "Directory path to list" }),
    }),
    "List directory contents through the EvoScientist Python sandbox backend."
  );

  registerBridgeTool(
    api,
    "glob",
    Type.Object({
      pattern: Type.String({ description: "Glob pattern" }),
      path: Type.Optional(
        Type.String({ description: "Base directory for the pattern" })
      ),
    }),
    "Find files matching a glob pattern through the EvoScientist Python sandbox backend."
  );

  registerBridgeTool(
    api,
    "grep",
    Type.Object({
      pattern: Type.String({ description: "Search pattern" }),
      path: Type.Optional(
        Type.String({ description: "File or directory to search" })
      ),
      glob: Type.Optional(
        Type.String({ description: "Glob filter for directory searches" })
      ),
    }),
    "Search file contents through the EvoScientist Python sandbox backend."
  );

  // -------------------------------------------------------------------------
  // EvoScientist memory observation tools
  // -------------------------------------------------------------------------

  registerBridgeTool(
    api,
    "search_observations",
    Type.Object({
      query: Type.String({ description: "Search keywords or regex pattern" }),
      mode: Type.Optional(
        Type.String({ description: "ranked or regex", default: "ranked" })
      ),
      scope: Type.Optional(
        Type.String({ description: "global, project, or omit for both" })
      ),
      memory_type: Type.Optional(
        Type.String({ description: "semantic, procedural, or episodic" })
      ),
      limit: Type.Optional(
        Type.Number({ description: "Maximum results", default: 8 })
      ),
    }),
    "Search EvoScientist memory observations."
  );

  registerBridgeTool(
    api,
    "read_memory",
    Type.Object({
      observation_id: Type.String({
        description: "Exact observation ID returned by search_observations",
      }),
    }),
    "Read a full EvoScientist memory observation by ID."
  );

  registerBridgeTool(
    api,
    "record_observation",
    Type.Object({
      memory_type: Type.String({
        description: "semantic, procedural, or episodic",
      }),
      summary: Type.String({
        description: "One-line summary for the observation index",
      }),
      observation: Type.String({
        description: "Concise reusable lesson, fact, or procedure",
      }),
      why_it_matters: Type.String({
        description: "Why this observation is valuable for future agents",
      }),
      scope: Type.Optional(
        Type.String({ description: "global or project", default: "global" })
      ),
      evidence: Type.Optional(
        Type.String({
          description: "Optional supporting evidence (URLs, paths, commands)",
        })
      ),
    }),
    "Record a structured observation into EvoScientist memory."
  );

  // -------------------------------------------------------------------------
  // EvoScientist schedule tools
  // -------------------------------------------------------------------------

  registerBridgeTool(
    api,
    "schedule_task",
    Type.Object({
      name: Type.String({ description: "Short human label for the task" }),
      cron: Type.String({
        description: "5-field cron expression (e.g. '0 9 * * 1')",
      }),
      prompt: Type.String({
        description: "Full self-contained instruction the scheduler runs",
      }),
      timezone: Type.Optional(
        Type.String({ description: "Optional IANA timezone" })
      ),
    }),
    "Create a recurring scheduled task in EvoScientist."
  );

  registerBridgeTool(
    api,
    "list_scheduled_tasks",
    Type.Object({}),
    "List recurring scheduled tasks in EvoScientist."
  );

  registerBridgeTool(
    api,
    "cancel_scheduled_task",
    Type.Object({
      cron_id: Type.String({
        description: "Task id or prefix shown by list_scheduled_tasks",
      }),
    }),
    "Cancel a recurring scheduled task in EvoScientist."
  );

  // -------------------------------------------------------------------------
  // EvoScientist skill manager
  // -------------------------------------------------------------------------

  registerBridgeTool(
    api,
    "skill_manager",
    Type.Object({
      action: Type.String({
        description: "install, list, uninstall, info, or browse",
      }),
      source: Type.Optional(
        Type.String({
          description: "Required for install: GitHub shorthand, URL, or local path",
        })
      ),
      name: Type.Optional(
        Type.String({ description: "Required for info and uninstall" })
      ),
      tag: Type.Optional(
        Type.String({ description: "Optional filter for browse" })
      ),
      include_system: Type.Optional(
        Type.Boolean({
          description: "Include built-in skills when listing",
          default: false,
        })
      ),
    }),
    "Install, list, or manage EvoScientist skills."
  );
};
