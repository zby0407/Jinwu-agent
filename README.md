<div align="center">
  <img alt="Jinwu JW" src="webui/public/jw-logo.jpg" width="104" align="middle">
  &nbsp;&nbsp;
  <img alt="Jinwu" src=".github/assets/logo.png" width="208" align="middle">

  <h3>From a research question to verifiable research results</h3>

  <p>
    Jinwu (JW) is an end-to-end autonomous research agent.<br>
    It strings research planning, literature search, code execution, data analysis, experiment verification and report writing into one complete workflow.
  </p>

  <p>
    <a href="https://github.com/zby0407/Jinwu-agent/actions/workflows/test.yml"><img alt="Tests" src="https://github.com/zby0407/Jinwu-agent/actions/workflows/test.yml/badge.svg"></a>
    <a href="https://github.com/zby0407/Jinwu-agent/blob/main/LICENSE"><img alt="License: Apache 2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
    <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  </p>

  <p>
    <a href="README.zh-CN.md">中文</a> ·
    <a href="#quick-start">Quick start</a> ·
    <a href="#core-capabilities">Core capabilities</a> ·
    <a href="docs/examples/survey-literature/README.md">Examples</a> ·
    <a href="docs/README.md">Documentation</a> ·
    <a href="CONTRIBUTING.md">Contributing</a>
  </p>
</div>

---

## Not just answering questions, but advancing research

Give Jinwu a research goal, and it decomposes the task, calls tools, runs analysis and saves the results; it keeps evidence at key steps and continuously manages hypotheses, experiments and context throughout the research.

- **Literature research**: discover, screen and read papers in a structured way, and produce a survey with citations.
- **Data analysis**: write and run code to process data, generate figures and verify results.
- **Experimental research**: manage hypotheses and experiments, record the process, review the evidence and iterate on the approach.
- **Writing**: distill the research process into reports, surveys and reproducible artifacts.

## Core capabilities

| Capability | Description |
| --- | --- |
| **End-to-end research orchestration** | Organizes the complete task around the research goal, from planning, retrieval and execution to verification, writing and delivery. |
| **Specialist sub-agents** | Coordinates specialist roles such as literature, code, data analysis, scientific hypothesis, experiment and evidence review to work together. |
| **Traceable research process** | Manages evidence and counter-evidence, keeps tool calls, task state and research results, and reduces "conclusions with no basis". |
| **Persistent memory and knowledge base** | Keeps research context across sessions and accumulates searchable, source-attributed project knowledge. |
| **Extensible skills and tools** | Install domain skills and connect MCP and third-party tools, so the agent adapts to your research workflow. |
| **Flexible model choice** | Supports Claude, GPT, Gemini, Qwen, DeepSeek, Kimi, GLM, Ollama, OpenRouter and custom compatible endpoints. |

## See how it works

The Jinwu desktop app integrates scientific research, domain skills and long-term memory into a single window. Without managing a Python or Node.js environment separately, you can start a research task directly.

<div align="center">
  <img alt="Jinwu desktop main interface" src=".github/assets/desktop-home.png" width="960">
  <p><sub>Jinwu desktop: start from a research question and let the agent plan and execute the research task</sub></p>
</div>

**[See the full example and generated results →](docs/examples/survey-literature/README.md)**

## Quick start

### Download the desktop app

No need to install Python, Node.js or other runtimes separately; choose your platform and download directly:

| Platform | Installer |
| --- | --- |
| **macOS (Apple Silicon)** | **[Download `.dmg`](https://github.com/zby0407/Jinwu-agent/releases/download/v0.3.5-desktop.1/Jinwu-0.3.5-mac-arm64.dmg)** |
| **Windows (x64)** | **[Download `.exe`](https://github.com/zby0407/Jinwu-agent/releases/download/v0.3.5-desktop.1/Jinwu-0.3.5-win-x64.exe)** |

[See release notes and SHA256 checksums →](https://github.com/zby0407/Jinwu-agent/releases/tag/v0.3.5-desktop.1)

> The current installers are not signed. On macOS, right-click the app and choose "Open" on first launch; if Windows shows a SmartScreen prompt, choose "Run anyway".

### Run from source

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/); Node.js 20+ is required to build the WebUI.

```bash
git clone https://github.com/zby0407/Jinwu-agent.git
cd Jinwu-agent
uv sync
uv run jw onboard
uv run jw
```

### Choose how you work

```bash
jw                         # default TUI
jw --ui cli                # interactive CLI
jw --ui webui              # browser WebUI
jw -p "survey this area and output a survey with citations"  # one-shot task
jw --resume                # resume a session
```

The default config is stored in `~/.config/jw/config.yaml`, and runtime data is stored in `~/.jw/`. You can also refer to [`.env.example`](.env.example) to provide model keys through environment variables.

## Three research modes

| Mode | When to use |
| --- | --- |
| `fast_answer` | Answer factual questions quickly; handle lightweight tasks. |
| `verified_analysis` | Tasks that require tool execution, data analysis and result verification. |
| `full_research` | Complete research tasks that require planning, retrieval, experiments, evidence review and writing. |

## Docker

```bash
cp .env.example .env
# Configure at least one model provider's API key in .env
docker compose build
docker compose run --rm jw
```

## Documentation and examples

- **[Documentation hub](docs/README.md)**: examples, deployment options and usage guides
- **[Full literature review example](docs/examples/survey-literature/README.md)**: a Chinese-English survey from a topic to 68 papers in 10 sections
- **[Project layout and data locations](docs/PROJECT_LAYOUT.md)**: source code, workspace and runtime artifacts
- **[WebUI guide](webui/README.md)**: features of the browser interface and how to develop it
- **[H1/H2 one-shot reproduction guide](docs/guides/solar-h1-h2-reproduction.md)**: a reproduction process with fixed models, inputs and prompts

## Contributing

Issues, pull requests, and new research skills or usage examples are welcome. Please read the [contributing guide](CONTRIBUTING.md) before you start.

```bash
# Backend checks
uv run ruff check --select F .
uv run ruff format --check jw tests
uv run pytest

# WebUI development
cd webui
npm install
npm run dev
```

## License

Jinwu's original code is licensed under the [Apache License 2.0](LICENSE). The project includes or adapts part of the code from other open-source projects; see [NOTICE](NOTICE) and the [third-party dependency license list](THIRD_PARTY_LICENSES.md) for copyright attribution and licenses.
