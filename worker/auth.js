// HMAC-SHA256 签名 token + Cookie 工具。Workers 原生 Web Crypto。
const enc = new TextEncoder();

function b64url(buf) {
  const bytes = new Uint8Array(buf);
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function b64urlDecode(str) {
  const pad = str.length % 4 === 0 ? '' : '='.repeat(4 - (str.length % 4));
  const s = atob(str.replace(/-/g, '+').replace(/_/g, '/') + pad);
  const bytes = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) bytes[i] = s.charCodeAt(i);
  return bytes;
}

async function hmacKey(secret) {
  return crypto.subtle.importKey(
    'raw', enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify'],
  );
}

export async function signToken(payload, secret) {
  const body = b64url(enc.encode(JSON.stringify(payload)));
  const key = await hmacKey(secret);
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(body));
  return `${body}.${b64url(sig)}`;
}

export async function verifyToken(token, secret) {
  if (!token || typeof token !== 'string') return null;
  const parts = token.split('.');
  if (parts.length !== 2) return null;
  const [body, sig] = parts;
  try {
    const key = await hmacKey(secret);
    const ok = await crypto.subtle.verify(
      'HMAC', key, b64urlDecode(sig), enc.encode(body),
    );
    if (!ok) return null;
    const payload = JSON.parse(new TextDecoder().decode(b64urlDecode(body)));
    if (!payload || typeof payload.exp !== 'number' || payload.exp < Date.now()) {
      return null;
    }
    return payload;
  } catch {
    return null;
  }
}

export function parseCookie(header, name) {
  if (!header) return null;
  for (const part of header.split(';')) {
    const idx = part.indexOf('=');
    if (idx < 0) continue;
    if (part.slice(0, idx).trim() === name) return part.slice(idx + 1).trim();
  }
  return null;
}

export function buildCookie(value, maxAge) {
  return `decide_auth=${value}; HttpOnly; Secure; SameSite=Strict; ` +
    `Path=/; Max-Age=${maxAge}`;
}

export function clearCookie() {
  return 'decide_auth=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0';
}
const COOKIE_NAME = 'decide_auth';

/** 读取并校验请求的登录态，返回 payload（含 user）或 null。 */
export async function readAuth(request, env) {
  if (!env || !env.DECIDE_SECRET) return null;
  const token = parseCookie(request.headers.get('Cookie'), COOKIE_NAME);
  if (!token) return null;
  return verifyToken(token, env.DECIDE_SECRET);
}
