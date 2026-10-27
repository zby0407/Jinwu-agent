import assert from 'node:assert/strict';
import test from 'node:test';

import { escapeHtml, renderErrorPage } from '../electron/lib/error-page.mjs';

test('escapeHtml 转义全部危险字符', () => {
  assert.equal(
    escapeHtml(`<script>alert("x&y'")</script>`),
    '&lt;script&gt;alert(&quot;x&amp;y&#39;&quot;)&lt;/script&gt;',
  );
  assert.equal(escapeHtml(null), '');
});

test('错误页包含日志路径、重试与退出操作', () => {
  const html = renderErrorPage({
    summary: '后端服务在 120 秒内未就绪',
    details: ['后端端口：6174；前端端口：4716'],
    logFiles: {
      directory: '/Users/tester/Library/Application Support/Jinwu/logs',
      backend: '/Users/tester/Library/Application Support/Jinwu/logs/backend.log',
      webui: '/Users/tester/Library/Application Support/Jinwu/logs/webui.log',
      desktop: '/Users/tester/Library/Application Support/Jinwu/logs/desktop.log',
    },
  });
  assert.match(html, /后端服务在 120 秒内未就绪/);
  assert.match(html, /backend\.log/);
  assert.match(html, /webui\.log/);
  assert.match(html, /desktop\.log/);
  assert.match(html, /jinwu-action:retry/);
  assert.match(html, /jinwu-action:quit/);
  assert.match(html, /jinwu-action:open-logs/);
  assert.match(html, /jinwu-action:open-workspace/);
  assert.match(html, /后端端口：6174/);
});

test('错误页没有内联脚本', () => {
  const html = renderErrorPage({ summary: '失败' });
  assert.doesNotMatch(html, /<script/i);
});

test('错误页对内容做 HTML 转义', () => {
  const html = renderErrorPage({
    summary: '<img src=x onerror=alert(1)>',
    logFiles: { backend: '/tmp/<bad>&.log' },
  });
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /&lt;bad&gt;&amp;\.log/);
  assert.doesNotMatch(html, /<img src=x/);
});

test('canRetry=false 时不显示重试操作', () => {
  const html = renderErrorPage({ summary: '不支持当前平台', canRetry: false });
  assert.doesNotMatch(html, /jinwu-action:retry/);
  assert.match(html, /jinwu-action:quit/);
});
