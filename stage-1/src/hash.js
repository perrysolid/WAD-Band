'use strict';
// Password hashing (D14): scrypt on the libuv threadpool, never on the event loop.
const crypto = require('node:crypto');

const API_COST = { N: 1 << 14, r: 8, p: 1 };
const SEED_COST = { N: 1 << 12, r: 8, p: 1 };
const SEED_COST_BULK = { N: 1 << 10, r: 8, p: 1 }; // many distinct seeded passwords
const BULK_THRESHOLD = 500;
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
  const cost = distinct.length > BULK_THRESHOLD ? SEED_COST_BULK : SEED_COST;
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

module.exports = { hashPassword, verifyPassword, hashSeedPasswords, tokenDigest, newToken };
