/**
 * 可读错误页渲染（无内联脚本，纯静态 HTML + 内联样式）。
 *
 * 页面上的操作通过自定义 `jinwu-action:` 链接实现，由主进程拦截：
 * - 重试启动：jinwu-action:retry
 * - 打开日志目录：jinwu-action:open-logs
 * - 打开工作区：jinwu-action:open-workspace
 * - 退出：jinwu-action:quit
 */

/** HTML 转义，防止日志/路径中的特殊字符破坏页面。 */
export function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

/**
 * 渲染启动失败页。
 *
 * @param {object} options
 * @param {string} [options.title]
 * @param {string} options.summary 一句话说明失败原因
 * @param {string[]} [options.details] 额外细节（端口、运行时版本等）
 * @param {{backend?: string, webui?: string, desktop?: string, directory?: string}} [options.logFiles]
 * @param {string[]} [options.tailLines] 最近日志行（会被转义并截断）
 * @param {boolean} [options.canRetry]
 * @param {boolean} [options.isDev]
 */
export function renderErrorPage({
  title = '金乌启动失败',
  summary = '启动过程中出现错误。',
  details = [],
  logFiles = {},
  tailLines = [],
  canRetry = true,
  isDev = false,
} = {}) {
  const detailItems = details
    .filter((item) => item !== undefined && item !== null && String(item).length > 0)
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join('\n');

  const logItems = Object.entries(logFiles)
    .filter(([, value]) => typeof value === 'string' && value.length > 0)
    .map(
      ([name, value]) =>
        `<li><span class="label">${escapeHtml(logName(name))}</span><code>${escapeHtml(value)}</code></li>`,
    )
    .join('\n');

  const tail = tailLines.length
    ? `<pre>${escapeHtml(tailLines.slice(-30).join('\n'))}</pre>`
    : '';

  const actions = [
    canRetry
      ? '<a class="primary" href="jinwu-action:retry">重试启动</a>'
      : '',
    '<a class="secondary" href="jinwu-action:open-logs">打开日志目录</a>',
    '<a class="secondary" href="jinwu-action:open-workspace">打开工作区</a>',
    '<a class="secondary" href="jinwu-action:quit">退出</a>',
  ]
    .filter(Boolean)
    .join('\n      ');

  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:;" />
  <title>${escapeHtml(title)}</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 48px 28px;
      background: #0d1117;
      color: #e6edf3;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      line-height: 1.6;
    }
    main { width: min(760px, 100%); }
    h1 { margin: 0 0 12px; font-size: 26px; color: #ffb454; }
    p.summary { margin: 0 0 24px; font-size: 15px; color: #c9d1d9; }
    section { margin-bottom: 22px; }
    h2 { margin: 0 0 10px; font-size: 14px; text-transform: uppercase; letter-spacing: .04em; color: #8b949e; }
    ul { margin: 0; padding-left: 20px; }
    li { margin-bottom: 6px; word-break: break-all; }
    .label { display: inline-block; min-width: 72px; color: #8b949e; }
    code, pre {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12.5px;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 6px;
    }
    code { padding: 2px 6px; }
    pre { margin: 0; padding: 14px; overflow: auto; max-height: 240px; color: #b9c0c8; white-space: pre-wrap; }
    .actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 28px; }
    .actions a {
      display: inline-block;
      padding: 9px 18px;
      border-radius: 8px;
      text-decoration: none;
      font-size: 14px;
      border: 1px solid #30363d;
      color: #e6edf3;
      background: #21262d;
    }
    .actions a.primary { background: #1f6feb; border-color: #1f6feb; color: #fff; }
    .actions a:hover { filter: brightness(1.12); }
    .hint { margin-top: 26px; font-size: 12.5px; color: #6e7681; }
    .hint code { background: transparent; border: none; padding: 0; }
    ${isDev ? '.dev-note { color: #d29922; }' : ''}
  </style>
</head>
<body>
  <main>
    <h1>${escapeHtml(title)}</h1>
    <p class="summary">${escapeHtml(summary)}</p>
    ${detailItems ? `<section><h2>详情</h2><ul>\n${detailItems}\n</ul></section>` : ''}
    ${logItems ? `<section><h2>日志文件</h2><ul>\n${logItems}\n</ul></section>` : ''}
    ${tail ? `<section><h2>最近日志</h2>${tail}</section>` : ''}
    <div class="actions">
      ${actions}
    </div>
    <p class="hint">若问题持续存在，请把日志目录中的 backend.log / webui.log / desktop.log 一并反馈。${isDev ? '（开发模式）' : ''}</p>
  </main>
</body>
</html>`;
}

function logName(key) {
  const names = {
    backend: '后端日志',
    webui: '前端日志',
    desktop: '桌面日志',
    directory: '日志目录',
  };
  return names[key] ?? key;
}
