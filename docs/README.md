<h1 align="center">🍪 Examples & Recipes</h1>

<h3 align="center">Customize your JW — harness it, make it yours.</h3>

| Example                                                     | Description                                                                     |
|------------------------------------------------------------|---------------------------------------------------------------------------------|
| [Survey literature](https://github.com/zby0407/Jinwu-agent/tree/main/docs/examples/survey-literature#literature-survey)   | Run JW with the `paper-navigator` skill to produce a bilingual, conference-grade literature survey |


| Recipe                                                     | Description                                                                     |
|------------------------------------------------------------|---------------------------------------------------------------------------------|
| [macOS 24/7 Deployment](https://github.com/zby0407/Jinwu-agent/blob/main/docs/recipes/deployment-macos-24h.md#running-jw-247-on-macos-telegram-bot--stt--ccproxy)   | Run JW as an always-on service on macOS with OAuth + Telegram + STT   |


| Guide                                                      | Description                                                                     |
|------------------------------------------------------------|---------------------------------------------------------------------------------|
| [H1/H2 一次性复现指南](./guides/solar-h1-h2-reproduction.md) | 使用固定提示词和固定 DashScope 模型，从 CLI 或 WebUI 并发提交两个可审计、相互隔离的复现任务 |
| [`stream-json` output protocol](https://github.com/zby0407/Jinwu-agent/blob/main/docs/guides/stream-json.md#stream-json-output-protocol)   | Line-delimited JSON event stream (`--output-format stream-json`) for driving JW headlessly from SDK / programmatic clients |
| [Agent RL evolution plan](./2026-07-24-agent-rl-evolution-plan.md) | Introduce RL through verifier-first hypothesis graphs, operation-level policies, staged evaluation, and later multi-agent expansion |

## Contributing a Recipe

See the [Contributing Guide](../CONTRIBUTING.md) for general guidelines. When adding a new recipe:

- **Use `jw` CLI** — recipes should work with `jw serve`, `jw config`, or `jw onboard`
- **Pin dependencies** — specify JW extras (e.g., `pip install -e ".[telegram,stt]"`)
- **Include a README** with clear setup and usage instructions
- **Keep it focused** — each recipe should demonstrate one deployment or integration scenario
- **Add to the table** above so others can discover it
