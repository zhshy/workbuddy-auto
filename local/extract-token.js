#!/usr/bin/env node
/**
 * 从本机 WorkBuddy 客户端提取登录凭证（accessToken / refreshToken / uid）
 *
 * 用法（必须用 WorkBuddy.exe 以 Node 模式执行，不能直接 node 跑）：
 *   Windows: 双击 extract.bat
 *   macOS/Linux: ./extract.sh
 *
 * 原理：WorkBuddy 5.6+ 把 auth 文件里的敏感字段做成了 AES-256-GCM 信封
 * （$wbEncrypted + envelope），解密密钥不落盘，只能通过客户端自带的
 * native binding（electron_browser_workbuddy_storage）在进程内取到。
 * 本脚本复刻官方 buildAuthenticatedContextAad 的字节序列完成解密。
 *
 * 只读，不修改任何客户端文件，不联网。
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');

const OUT = path.join(__dirname, 'tokens.json');

function findAuthFile() {
  const rel = [
    'CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info',
  ];
  const roots = [];
  if (process.env.LOCALAPPDATA) roots.push(process.env.LOCALAPPDATA);          // Windows
  roots.push(path.join(os.homedir(), 'Library/Application Support'));         // macOS
  roots.push(path.join(os.homedir(), '.config'));                             // Linux
  roots.push(path.join(os.homedir(), '.local/share'));                        // Linux
  for (const r of roots) {
    for (const p of rel) {
      const full = path.join(r, p);
      if (fs.existsSync(full)) return full;
    }
  }
  return null;
}

// ---- 复刻官方 AAD 构造（见 app.asar 中 buildAuthenticatedContextAad）----
const FRAMING_CODE = { file: 1, field: 2, record: 3, stream: 4 };
const FORMAT_ID = { file: 'WBEF1', field: 'WBEV1', record: 'WBER1', stream: 'WBES1' };

function u32be(n) {
  const b = Buffer.alloc(4);
  b.writeUInt32BE(n);
  return b;
}

function lenPrefixed(s) {
  const b = Buffer.from(s, 'utf8');
  return Buffer.concat([u32be(b.length), b]);
}

function buildAad(keyId, suite, framing, scheme) {
  return Buffer.concat([
    Buffer.from('WB-AAD\0', 'ascii'),
    Buffer.from([1]),
    lenPrefixed(FORMAT_ID[framing]),
    lenPrefixed(scheme),
    u32be(suite),
    lenPrefixed(keyId),
    Buffer.from([FRAMING_CODE[framing]]),
    Buffer.from([0]), // encodeOptionalUint64(undefined)
    Buffer.from([0]), // final === undefined -> 0
  ]);
}

function getProtectorKey() {
  const binding = process._linkedBinding('electron_browser_workbuddy_storage');
  const payload = JSON.parse(binding.loggerGet());
  // key = sha256(utf8(base64字符串))，keyId = sha256(key).hex[:16]
  return crypto.createHash('sha256').update(payload.atRestSecretKey, 'utf8').digest();
}

function tryDecrypt(envelopeB64, key) {
  const env = JSON.parse(Buffer.from(envelopeB64, 'base64').toString('utf8'));
  for (const framing of ['field', 'file']) {
    try {
      const d = crypto.createDecipheriv(
        'aes-256-gcm',
        key,
        Buffer.from(env.nonce, 'base64'),
        { authTagLength: 16 }
      );
      d.setAAD(buildAad(env.keyId, env.suite, framing, 'sym-v1'));
      d.setAuthTag(Buffer.from(env.authTag, 'base64'));
      const out = Buffer.concat([
        d.update(Buffer.from(env.ciphertext, 'base64')),
        d.final(),
      ]);
      return out.toString('utf8');
    } catch (e) {
      /* 换下一种 framing 继续试 */
    }
  }
  return null;
}

function readField(auth, name, key) {
  const v = auth[name];
  if (typeof v === 'string' && v) return { value: v, encrypted: false };
  if (v && typeof v === 'object' && typeof v.envelope === 'string') {
    if (!key) return { value: null, encrypted: true, error: '缺少密钥' };
    const plain = tryDecrypt(v.envelope, key);
    return { value: plain, encrypted: true, error: plain ? null : '解密失败' };
  }
  return { value: null, encrypted: false, error: '字段不存在' };
}

function main() {
  const file = process.env.WB_AUTH_FILE || findAuthFile();
  if (!file || !fs.existsSync(file)) {
    console.error('✗ 找不到 WorkBuddy 登录态文件。');
    console.error('  预期位置：%LOCALAPPDATA%\\CodeBuddyExtension\\Data\\Public\\auth\\workbuddy-desktop.info');
    console.error('  请确认客户端已安装并登录过。也可用环境变量 WB_AUTH_FILE 指定路径。');
    process.exit(2);
  }

  const doc = JSON.parse(fs.readFileSync(file, 'utf8'));
  const auth = doc.auth || {};
  const account = doc.account || {};

  const accessRaw = auth.accessToken;
  const isEncrypted = accessRaw && typeof accessRaw === 'object';

  let key = null;
  if (isEncrypted) {
    try {
      key = getProtectorKey();
    } catch (e) {
      console.error('✗ 取不到解密密钥：' + e.message);
      console.error('  请确认 WorkBuddy 客户端正在运行，然后用 extract.bat 重新执行。');
      process.exit(3);
    }
  }

  const access = readField(auth, 'accessToken', key);
  const refresh = readField(auth, 'refreshToken', key);

  if (!access.value) {
    console.error('✗ accessToken 提取失败：' + (access.error || '未知原因'));
    process.exit(4);
  }

  const result = {
    uid: account.uid || '',
    access_token: access.value,
    refresh_token: refresh.value || '',
    domain: auth.domain || 'www.workbuddy.cn',
    expires_at: auth.expiresAt || 0,
    refresh_expires_at: auth.refreshExpiresAt || 0,
    extracted_at: new Date().toISOString(),
  };

  fs.writeFileSync(OUT, JSON.stringify(result, null, 2), { mode: 0o600 });

  const exp = result.expires_at ? new Date(result.expires_at).toLocaleString('zh-CN') : '未知';
  console.log('✓ 提取成功');
  console.log('  uid            : ' + result.uid);
  console.log('  domain         : ' + result.domain);
  console.log('  accessToken    : ' + access.value.length + ' 字符（' + (isEncrypted ? '已解密' : '明文') + '）');
  console.log('  refreshToken   : ' + (refresh.value ? refresh.value.length + ' 字符' : '无'));
  console.log('  accessToken 到期: ' + exp);
  console.log('');
  console.log('已写入：' + OUT);
  console.log('下一步：运行 push-secrets.bat 推到 GitHub Secrets，或手动复制到仓库设置。');
}

main();
