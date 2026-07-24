# pi-mcp-bridge

An [MCP](https://modelcontextprotocol.io/) server that bridges [JW](https://github.com/zby0407/Jinwu-agent) with the [Pi coding agent](https://pi.dev/) via Pi's RPC mode.

It lets JW delegate coding-heavy tasks to Pi while keeping JW in charge of the overall research workflow, memory, and multi-agent orchestration.

## What it exposes

| Tool | Purpose |
|------|---------|
| `pi_code_assist` | Write, refactor, or modify code in the workspace |
| `pi_review_code` | Review code for bugs, style, performance, design |
| `pi_debug` | Diagnose an error and propose/apply a fix |
| `pi_explain` | Explain how a piece of code works |
| `pi_read_file` | Read a file and ask Pi a question about it |
| `pi_edit_file` | Edit a specific file via Pi (read → prompt → write back) |
| `pi_list_commands` | List Pi's available skills, templates, and extension commands |
| `pi_invoke_command` | Invoke a Pi skill/template/extension command by name |
| `pi_bash` | Run a shell command through Pi's bash tool |
| `pi_reset_session` | Start a fresh Pi session |

### Features

- **Image input**: Most tools accept `image_paths`, which the bridge base64-encodes and sends to Pi. Useful for reviewing plots, screenshots, or diagrams.
- **Pi skills/templates**: Use `pi_list_commands` to discover capabilities, then `pi_invoke_command` to run them.
- **Fine-grained file edits**: `pi_edit_file` reads the file, asks Pi for the new content, and writes it back automatically.

## Prerequisites

- Python 3.11+
- [Pi](https://pi.dev/) installed and available as `pi` (or configure `PI_MCP_BIN`)
- An LLM provider API key that Pi supports (e.g. `ANTHROPIC_API_KEY`)

## Installation

From the `pi-mcp-bridge` directory:

```bash
uv pip install -e .
# or
pip install -e .
```

## Usage with JW

Add the bridge to JW's MCP config (`~/.config/jw/mcp.yaml`):

```yaml
mcp_servers:
  pi-bridge:
    command: pi-mcp-bridge
    args: []
    env:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      PI_MCP_CWD: /path/to/your/workspace
      PI_MCP_PROVIDER: anthropic
      PI_MCP_MODEL: claude-sonnet-4-20250514
```

Or use the provided example:

```bash
cp examples/mcp.yaml ~/.config/jw/mcp.yaml
# edit the workspace path and provider settings
```

Then start JW. The `code-agent` and main agent can now call Pi tools.

## Configuration

You can configure the bridge via environment variables or CLI flags.

### Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PI_MCP_BIN` | Path to the `pi` executable | `pi` |
| `PI_MCP_CWD` | Working directory for Pi | current directory |
| `PI_MCP_PROVIDER` | LLM provider for Pi | Pi default |
| `PI_MCP_MODEL` | Model for Pi | Pi default |
| `PI_MCP_SESSION_DIR` | Pi session storage directory | Pi default |
| `PI_MCP_SESSION_NAME` | Pi session display name | `jw-pi-bridge` |
| `PI_MCP_NO_SESSION` | Disable Pi session persistence | `false` |
| `PI_MCP_EXTRA_ARGS` | Extra CLI args for `pi` | none |

### CLI flags

```bash
pi-mcp-bridge --cwd /path/to/workspace --provider anthropic --model claude-sonnet-4-20250514 --verbose
```

## Architecture

```text
JW (Python/DeepAgents/LangGraph)
    │
    ├── planner / research / analyze / writing agents
    │
    └── MCP client
            │
            └── pi-mcp-bridge (this package)
                    │
                    └── Pi RPC subprocess (pi --mode rpc)
                            └── coding agent runtime
```

The bridge maintains one long-lived Pi RPC session. Tool calls become `prompt` commands; the bridge waits for `agent_settled`, assembles the streamed response, and returns it to JW.

## Limitations

- Pi's interactive UI commands (`select`, `confirm`, etc.) are automatically cancelled in headless mode. Avoid Pi extensions that depend on modal UI.
- The bridge runs Pi with the same filesystem permissions as the JW process. Containerize if you need stronger isolation.
- Long-running Pi tasks may take minutes; JW's default tool timeouts may need to be increased for complex coding tasks.

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check .
```

## License

Apache-2.0
