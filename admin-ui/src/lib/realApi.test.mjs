import assert from 'node:assert/strict';
import { test } from 'node:test';
import { realApi } from './realApi.ts';

for (const query of ['skip_completed=1', 'confirmed=1&skip_completed=0']) {
  test('手动推送透传附加提醒 · ' + query, async (t) => {
    const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({ ok: true, queued: true }));
    const path = '/api/admin/templates/test-template/push-now?' + query;
    const body = { push_note: '请在截止前提交 {姓名}' };

    assert.deepEqual(await realApi('POST', path, body), { ok: true, queued: true });
    assert.equal(fetch.mock.callCount(), 1);
    const [url, request] = fetch.mock.calls[0].arguments;
    assert.equal(url, path);
    assert.equal(request.method, 'POST');
    assert.deepEqual(JSON.parse(request.body), body);
  });
}

test('修改模板仍使用原字段映射', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({ ok: true }));
  await realApi('PUT', '/api/admin/templates/test-template', { name: '模板', kind: 'upload', role_id: 'role-a' });
  const [, request] = fetch.mock.calls[0].arguments;
  assert.deepEqual(JSON.parse(request.body), { name: '模板', role_ids: ['role-a'], kind: 'form' });
});
