// Worker 认证模块单测（node --test）
import { test } from 'node:test';
import assert from 'node:assert';
import { signToken, verifyToken, parseCookie, buildCookie } from '../worker/auth.js';

const SECRET = 'test-secret-do-not-use';

test('signToken + verifyToken 往返成功', async () => {
  const token = await signToken({ exp: Date.now() + 60000 }, SECRET);
  const payload = await verifyToken(token, SECRET);
  assert.ok(payload, '应验签通过');
  assert.ok(payload.exp > Date.now());
});

test('过期 token 被拒绝', async () => {
  const token = await signToken({ exp: Date.now() - 1000 }, SECRET);
  assert.equal(await verifyToken(token, SECRET), null);
});

test('篡改 token 被拒绝', async () => {
  const token = await signToken({ exp: Date.now() + 60000 }, SECRET);
  const tampered = token.slice(0, -2) + 'xx';
  assert.equal(await verifyToken(tampered, SECRET), null);
});

test('错误密钥被拒绝', async () => {
  const token = await signToken({ exp: Date.now() + 60000 }, SECRET);
  assert.equal(await verifyToken(token, 'wrong-secret'), null);
});

test('parseCookie 提取指定 cookie', () => {
  assert.equal(parseCookie('a=1; decide_auth=abc.def; b=2', 'decide_auth'), 'abc.def');
  assert.equal(parseCookie('a=1', 'decide_auth'), null);
  assert.equal(parseCookie(null, 'decide_auth'), null);
});

test('buildCookie 含安全属性', () => {
  const c = buildCookie('tok', 604800);
  assert.ok(c.includes('HttpOnly'));
  assert.ok(c.includes('Secure'));
  assert.ok(c.includes('SameSite=Strict'));
  assert.ok(c.includes('Max-Age=604800'));
});

import { readAuth } from '../worker/auth.js';

function fakeRequest(cookieHeader) {
  return {
    headers: {
      get(name) {
        return name.toLowerCase() === 'cookie' ? cookieHeader : null;
      },
    },
  };
}

test('readAuth 无 cookie 返回 null', async () => {
  assert.equal(await readAuth(fakeRequest(null), { DECIDE_SECRET: SECRET }), null);
});

test('readAuth 有效 cookie 返回 payload', async () => {
  const token = await signToken({ user: 'alice', exp: Date.now() + 60000 }, SECRET);
  const payload = await readAuth(fakeRequest(`decide_auth=${token}`), { DECIDE_SECRET: SECRET });
  assert.ok(payload);
  assert.equal(payload.user, 'alice');
});

test('readAuth 过期 token 返回 null', async () => {
  const token = await signToken({ user: 'alice', exp: Date.now() - 1000 }, SECRET);
  assert.equal(await readAuth(fakeRequest(`decide_auth=${token}`), { DECIDE_SECRET: SECRET }), null);
});
