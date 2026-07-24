# 为金乌 WebUI 贡献

WebUI 位于主项目的 `webui/` 目录，与 Python 后端共用一个 Git 仓库。

## 开发

先在项目根目录启动后端：

```bash
uv run jw deploy
```

再启动前端：

```bash
cd webui
npm install
npm run dev
```

默认前端端口为 `4716`，后端端口为 `6174`。

## 提交前检查

```bash
npm run lint
npm run format:check
npm run build
```

请保持类型安全、路径校验和无障碍属性；涉及界面的改动应附截图或录屏。不要提交 `node_modules/`、`.next/`、`dist/` 或本地配置。
