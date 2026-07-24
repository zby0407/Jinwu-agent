# 为金乌（JW）贡献

感谢参与金乌开发。本项目使用单仓库管理 Python 后端、WebUI 和科研工作流模块。

## 开发环境

```bash
git clone https://github.com/zby0407/Jinwu-agent.git
cd Jinwu-agent
uv sync --dev
```

WebUI 依赖：

```bash
cd webui
npm install
```

## 提交前检查

后端：

```bash
uv run ruff check .
uv run pytest
```

前端：

```bash
cd webui
npm run lint
npm run format:check
npm run build
```

## 约定

- 从 `main` 创建范围明确的分支。
- 功能改动需要测试；界面改动请附截图或录屏。
- 不提交 `.venv/`、`node_modules/`、`.next/`、`dist/`、运行工作区或本地密钥。
- Python 包名为 `jw`，CLI 命令为 `jw`，主图 ID 为 `JW`。
- 面向用户的品牌名称使用“金乌”，需要英文标识时使用“JW”。
- WebUI 位于 `webui/`，不创建嵌套 `.git`。

提交 Pull Request 时请说明问题、实现方式、验证结果和潜在兼容性影响。
