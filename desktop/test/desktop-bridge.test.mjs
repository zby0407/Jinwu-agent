import assert from 'node:assert/strict';
import test from 'node:test';

import {
  BRIDGE_MODELS_PATH,
  BRIDGE_PATH,
  BRIDGE_TICKET_HEADER,
  startDesktopBridge,
} from '../electron/lib/desktop-bridge.mjs';

async function withBridge(handler, run) {
  const bridge = await startDesktopBridge({ handler });
  try {
    await run(bridge);
  } finally {
    await bridge.close();
  }
}

test('startDesktopBridge binds loopback and exposes a ready URL', async () => {
  await withBridge(async () => ({ status: 200, body: { ok: true } }), async (bridge) => {
    assert.match(bridge.url, /^http:\/\/127\.0\.0\.1:\d+\/model-config$/);
    assert.equal(bridge.url.endsWith(BRIDGE_PATH), true);
    assert.equal(bridge.ticket.length, 48);
    assert.equal(bridge.port > 0, true);
  });
});

test('requests without the ticket are rejected', async () => {
  await withBridge(
    async () => ({ status: 200, body: { ok: true } }),
    async (bridge) => {
      const response = await fetch(bridge.url);
      assert.equal(response.status, 401);
      const body = await response.json();
      assert.equal(body.ok, false);
    },
  );
});

test('a wrong ticket is rejected', async () => {
  await withBridge(
    async () => ({ status: 200, body: { ok: true } }),
    async (bridge) => {
      const response = await fetch(bridge.url, {
        headers: { [BRIDGE_TICKET_HEADER]: 'not-the-ticket' },
      });
      assert.equal(response.status, 401);
    },
  );
});

test('GET and POST forward the ticket-protected request to the handler', async () => {
  const calls = [];
  await withBridge(
    async ({ path, method, body }) => {
      calls.push({ path, method, body });
      return { status: 200, body: { ok: true, method, path } };
    },
    async (bridge) => {
      const headers = { [BRIDGE_TICKET_HEADER]: bridge.ticket };
      const getResponse = await fetch(bridge.url, { headers });
      assert.equal(getResponse.status, 200);
      assert.deepEqual(await getResponse.json(), {
        ok: true,
        method: 'GET',
        path: BRIDGE_PATH,
      });

      const postResponse = await fetch(bridge.url, {
        method: 'POST',
        headers: { ...headers, 'content-type': 'application/json' },
        body: JSON.stringify({ provider: 'deepseek', model: 'deepseek-v4-pro' }),
      });
      assert.equal(postResponse.status, 200);
      assert.deepEqual(await postResponse.json(), {
        ok: true,
        method: 'POST',
        path: BRIDGE_PATH,
      });

      const modelsResponse = await fetch(
        bridge.url.replace(BRIDGE_PATH, BRIDGE_MODELS_PATH),
        { headers },
      );
      assert.equal(modelsResponse.status, 200);
      assert.deepEqual(await modelsResponse.json(), {
        ok: true,
        method: 'GET',
        path: BRIDGE_MODELS_PATH,
      });
    },
  );
  assert.deepEqual(calls, [
    { path: BRIDGE_PATH, method: 'GET', body: {} },
    {
      path: BRIDGE_PATH,
      method: 'POST',
      body: { provider: 'deepseek', model: 'deepseek-v4-pro' },
    },
    { path: BRIDGE_MODELS_PATH, method: 'GET', body: {} },
  ]);
});

test('unknown paths and methods are rejected', async () => {
  await withBridge(
    async () => ({ status: 200, body: { ok: true } }),
    async (bridge) => {
      const headers = { [BRIDGE_TICKET_HEADER]: bridge.ticket };
      const notFound = await fetch(bridge.url.replace('/model-config', '/other'), {
        headers,
      });
      assert.equal(notFound.status, 404);

      const put = await fetch(bridge.url, { method: 'PUT', headers });
      assert.equal(put.status, 405);
    },
  );
});

test('oversized bodies are rejected with 413', async () => {
  await withBridge(
    async () => ({ status: 200, body: { ok: true } }),
    async (bridge) => {
      const response = await fetch(bridge.url, {
        method: 'POST',
        headers: {
          [BRIDGE_TICKET_HEADER]: bridge.ticket,
          'content-type': 'application/json',
        },
        body: JSON.stringify({ padding: 'x'.repeat(70 * 1024) }),
      });
      assert.equal(response.status, 413);
    },
  );
});

test('malformed JSON is rejected with 400', async () => {
  await withBridge(
    async () => ({ status: 200, body: { ok: true } }),
    async (bridge) => {
      const response = await fetch(bridge.url, {
        method: 'POST',
        headers: {
          [BRIDGE_TICKET_HEADER]: bridge.ticket,
          'content-type': 'application/json',
        },
        body: 'not json',
      });
      assert.equal(response.status, 400);
    },
  );
});

test('handler failures become JSON 500 responses without leaking details', async () => {
  await withBridge(
    async () => {
      throw new Error('synthetic-secret-detail');
    },
    async (bridge) => {
      const response = await fetch(bridge.url, {
        headers: { [BRIDGE_TICKET_HEADER]: bridge.ticket },
      });
      assert.equal(response.status, 500);
      const text = await response.text();
      assert.equal(text.includes('synthetic-secret-detail'), false);
      assert.equal(JSON.parse(text).ok, false);
    },
  );
});
