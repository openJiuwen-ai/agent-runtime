import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

globalThis.window = { location: { protocol: 'http:', origin: 'http://localhost:5273' } };
globalThis.document = { cookie: '' };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });

try {
  const {
    toDatetimeLocalValue,
    splitDatetimeLocal,
    joinDatetimeLocal,
  } = await server.ssrLoadModule('/src/utils/datetimeLocal.ts');

  await test('toDatetimeLocalValue: empty and invalid → empty string', () => {
    assert.equal(toDatetimeLocalValue(null), '');
    assert.equal(toDatetimeLocalValue(undefined), '');
    assert.equal(toDatetimeLocalValue(''), '');
    assert.equal(toDatetimeLocalValue('not-a-date'), '');
  });

  await test('toDatetimeLocalValue: ISO → local YYYY-MM-DDTHH:mm', () => {
    const iso = '2027-01-02T03:04:05.123Z';
    const date = new Date(iso);
    const expected = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    assert.equal(toDatetimeLocalValue(iso), expected);
  });

  await test('splitDatetimeLocal / joinDatetimeLocal round-trip', () => {
    assert.deepEqual(splitDatetimeLocal(''), { date: '', time: '' });
    assert.deepEqual(splitDatetimeLocal('  '), { date: '', time: '' });
    assert.deepEqual(splitDatetimeLocal('bad'), { date: '', time: '' });
    assert.deepEqual(splitDatetimeLocal('2028-02-03T14:27'), { date: '2028-02-03', time: '14:27' });
    assert.equal(joinDatetimeLocal('', ''), '');
    assert.equal(joinDatetimeLocal('', '14:27'), '');
    assert.equal(joinDatetimeLocal('2028-02-03', ''), '2028-02-03T00:00');
    assert.equal(joinDatetimeLocal('2028-02-03', '14:27'), '2028-02-03T14:27');
    const value = '2028-02-03T14:27';
    const { date, time } = splitDatetimeLocal(value);
    assert.equal(joinDatetimeLocal(date, time), value);
  });
} finally {
  await server.close();
}
