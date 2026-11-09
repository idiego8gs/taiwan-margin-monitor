// Authenticated encryption for the portable seed. This file never logs key material.
import {createCipheriv, createDecipheriv, createHash, randomBytes} from 'node:crypto';
import {readFileSync, writeFileSync} from 'node:fs';

const [mode, input, output, keyFile] = process.argv.slice(2);
const secret = keyFile ? readFileSync(keyFile, 'utf8').trim() : process.env.SITE_STATE_KEY;
if (!secret || Buffer.from(secret, 'base64').length < 32 || !input || !output) {
  throw new Error('A random base64 key of at least 32 bytes and input/output paths are required.');
}
const key = createHash('sha256').update(secret).digest();
const header = Buffer.from('TWM1');
const source = readFileSync(input);

if (mode === 'seal') {
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  cipher.setAAD(header);
  const encrypted = Buffer.concat([cipher.update(source), cipher.final()]);
  writeFileSync(output, Buffer.concat([header, iv, cipher.getAuthTag(), encrypted]), {flag: 'wx'});
} else if (mode === 'open') {
  if (!source.subarray(0, 4).equals(header) || source.length < 32) {
    throw new Error('Unsupported encrypted seed format.');
  }
  const decipher = createDecipheriv('aes-256-gcm', key, source.subarray(4, 16));
  decipher.setAAD(header);
  decipher.setAuthTag(source.subarray(16, 32));
  const decrypted = Buffer.concat([decipher.update(source.subarray(32)), decipher.final()]);
  writeFileSync(output, decrypted, {flag: 'wx'});
} else {
  throw new Error('Expected seal or open.');
}
