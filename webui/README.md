# 金乌 WebUI

金乌 WebUI 是 JW 的浏览器工作台，提供流式对话、模型切换、工具审批、子智能体状态、工作区文件、知识库、长期记忆和定时任务等界面。

WebUI 是主项目的一部分，位于统一仓库的 `webui/` 目录，不使用独立 Git 仓库。

## 本地开发

```bash
cd webui
npm install
npm run dev
```

默认地址为 <http://localhost:4716>。后端默认连接 `http://127.0.0.1:6174`，也可以在界面配置中修改部署地址。

## 生产构建

```bash
npm run lint
npm run build
npm run start:dist
```

也可以从项目根目录直接启动集成界面：

```bash
uv run jw --ui webui
```

## 配置

- `NEXT_PUBLIC_LANGSMITH_API_KEY`：连接需要认证的 LangGraph 部署时使用。
- 浏览器本地配置键为 `jw-config`。
- 主图 ID 固定为 `JW`。

## License

JW WebUI 原创代码采用 Apache License 2.0，详见 [LICENSE](./LICENSE)。第三方代码的
版权归属和许可证全文见 [NOTICE](./NOTICE)。
