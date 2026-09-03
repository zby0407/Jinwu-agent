# Third-party dependency licenses

This inventory covers the direct Python runtime dependencies used by JW,
including the `solar` extra, and the direct WebUI runtime dependencies. Exact
resolved transitive versions are recorded in `uv.lock` and
`webui/package-lock.json`; each dependency's distributed license text remains
authoritative.

Generated from the locked environment on 2026-09-03. Development-only and
optional channel integrations are not required for the H1/H2 reproduction.

## Python runtime

| Package | Locked version | License |
|---|---:|---|
| astropy | 8.0.1 | BSD-3-Clause |
| deepagents | 0.6.12 | MIT |
| filelock | 3.29.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| jsonschema | 4.26.0 | MIT |
| langchain | 1.3.11 | MIT |
| langchain-anthropic | 1.4.8 | MIT |
| langchain-google-genai | 4.2.5 | MIT |
| langchain-mcp-adapters | 0.2.2 | MIT |
| langchain-nvidia-ai-endpoints | 1.2.1 | MIT |
| langchain-ollama | 1.1.0 | MIT |
| langchain-openai | 1.2.1 | MIT |
| langchain-openrouter | 0.2.5 | MIT |
| langgraph-checkpoint-sqlite | 3.0.3 | MIT |
| langgraph-cli | 0.4.29 | MIT |
| lazy-loader | 0.5 | BSD-3-Clause |
| markdownify | 1.2.2 | MIT |
| matplotlib | 3.11.0 | Matplotlib License (PSF-compatible) |
| nest-asyncio | 1.6.0 | BSD |
| numpy | 2.4.4 | BSD-3-Clause; bundled components also include 0BSD, MIT, Zlib and CC0-1.0 |
| pandas | 3.0.3 | BSD-3-Clause |
| prompt-toolkit | 3.0.52 | BSD-3-Clause |
| psutil | 7.2.2 | BSD-3-Clause |
| pypdf | 6.15.0 | BSD-3-Clause |
| python-docx | 1.2.0 | MIT |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT |
| questionary | 2.1.1 | MIT |
| rich | 15.0.0 | MIT |
| scikit-learn | 1.9.0 | BSD-3-Clause |
| tavily-python | 0.7.24 | MIT |
| textual | 8.2.5 | MIT |
| tree-sitter | 0.26.0 | MIT |
| tree-sitter-bash | 0.25.1 | MIT |
| typer | 0.25.1 | MIT |
| tzlocal | 5.3.1 | MIT |

## WebUI runtime

| Package | Locked version | License |
|---|---:|---|
| @langchain/core | 1.1.19 | MIT |
| @langchain/langgraph-sdk | 1.0.3 | MIT |
| @radix-ui/react-dialog | 1.1.15 | MIT |
| @radix-ui/react-label | 2.1.8 | MIT |
| @radix-ui/react-scroll-area | 1.2.10 | MIT |
| @radix-ui/react-select | 2.2.6 | MIT |
| @radix-ui/react-slot | 1.2.4 | MIT |
| @types/react-syntax-highlighter | 15.5.13 | MIT |
| @types/uuid | 9.0.8 | MIT |
| class-variance-authority | 0.7.1 | Apache-2.0 |
| clsx | 1.2.1 | MIT |
| katex | 0.16.47 | MIT |
| lucide-react | 0.539.0 | ISC |
| mermaid | 11.15.0 | MIT |
| next | 16.2.6 | MIT |
| nuqs | 2.8.9 | MIT |
| react | 19.1.0 | MIT |
| react-dom | 19.1.0 | MIT |
| react-markdown | 9.1.0 | MIT |
| react-resizable-panels | 3.0.6 | MIT |
| react-syntax-highlighter | 15.6.6 | MIT |
| rehype-katex | 7.0.1 | MIT |
| rehype-raw | 7.0.0 | MIT |
| rehype-sanitize | 6.0.0 | MIT |
| remark-gfm | 4.0.1 | MIT |
| remark-math | 6.0.0 | MIT |
| sonner | 2.0.7 | MIT |
| swr | 2.4.1 | MIT |
| tailwind-merge | 2.6.1 | MIT |
| use-stick-to-bottom | 1.1.6 | MIT |
| uuid | 9.0.1 | MIT |

## Notice

This file is an attribution and reproducibility inventory, not legal advice.
For redistribution, consult the license files shipped with every resolved
package and preserve all notices required by those licenses.
