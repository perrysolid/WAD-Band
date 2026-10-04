'use strict';
// Password hashing (D14): scrypt on the libuv threadpool, never on the event loop.
const crypto = require('node:crypto');

const API_COST = { N: 1 << 14, r: 8, p: 1 };
// Seeded credentials (D14): N = 2^12 normally; for large fixtures with many
// distinct plaintexts N scales down so all hashing stays within about 3 s on
// 2 vCPU (one hash costs roughly N * 2 µs per core), well inside the 10 s reset limit.
const SEED_MAX_N = 1 << 12;
const SEED_MIN_N = 1 << 4;
const SEED_BUDGET = 3e6; // distinct * N must stay at or below this

function seedCost(distinct) {
  let N = SEED_MAX_N;
  while (N > SEED_MIN_N && distinct * N > SEED_BUDGET) N >>= 1;
  return { N, r: 8, p: 1 };
}
const KEYLEN = 32;

function scrypt(password, saltHex, cost) {
  return new Promise((resolve, reject) => {
    crypto.scrypt(password, Buffer.from(saltHex, 'hex'), KEYLEN,
      { N: cost.N, r: cost.r, p: cost.p, maxmem: 256 * cost.N * cost.r + (1 << 20) },
      (err, key) => (err ? reject(err) : resolve(key.toString('hex'))));
  });
}

async function hashPassword(password, cost = API_COST) {
  const salt = crypto.randomBytes(16).toString('hex');
  const hash = await scrypt(password, salt, cost);
  return { alg: 'scrypt', N: cost.N, r: cost.r, p: cost.p, salt, hash };
}

async function verifyPassword(password, cred) {
  if (!cred || cred.alg !== 'scrypt') return false;
  const hash = await scrypt(password, cred.salt, cred);
  const a = Buffer.from(hash, 'hex');
  const b = Buffer.from(cred.hash, 'hex');
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

// Hash every distinct plaintext once; returns Map plaintext -> credential.
async function hashSeedPasswords(passwords) {
  const distinct = [...new Set(passwords)];
  const cost = seedCost(distinct.length);
  const creds = await Promise.all(distinct.map((p) => hashPassword(p, cost)));
  const out = new Map();
  distinct.forEach((p, i) => out.set(p, creds[i]));
  return out;
}

function tokenDigest(token) {
  return crypto.createHash('sha256').update(token).digest('hex');
}

function newToken() {
  return crypto.randomBytes(32).toString('hex');
}

module.exports = { seedCost, hashPassword, verifyPassword, hashSeedPasswords, tokenDigest, newToken };
