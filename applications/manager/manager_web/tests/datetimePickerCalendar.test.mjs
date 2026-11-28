import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

globalThis.window = { location: { protocol: 'http:', origin: 'http://localhost:5273' } };
globalThis.document = { cookie: '' };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
const server = await createServer({
  configFile: false,
  server: { middlewareMode: true, hmr: false, ws: false },
  appType: 'custom',
});

try {
  const { buildMonthCells, formatCellDate } = await server.ssrLoadModule(
    '/src/utils/datetimePickerCalendar.ts',
  );

  await test('formatCellDate: zero-pads month and day', () => {
    assert.equal(formatCellDate(2024, 0, 1), '2024-01-01');
    assert.equal(formatCellDate(2024, 11, 31), '2024-12-31');
  });

  await test('buildMonthCells: Monday-first; Jan 2024 starts on Monday', () => {
    const cells = buildMonthCells(2024, 0);
    assert.equal(cells.length % 7, 0);
    assert.deepEqual(cells[0], { year: 2024, month: 0, day: 1, inMonth: true });
    const inMonth = cells.filter((c) => c.inMonth);
    assert.equal(inMonth.length, 31);
    assert.equal(inMonth[inMonth.length - 1].day, 31);
  });

  await test('buildMonthCells: pads prev/next month across year boundary', () => {
    // 2025-01-01 is Wednesday → Monday-first pads Dec 30–31 2024
    const cells = buildMonthCells(2025, 0);
    assert.equal(cells.length % 7, 0);
    assert.deepEqual(cells[0], { year: 2024, month: 11, day: 30, inMonth: false });
    assert.deepEqual(cells[1], { year: 2024, month: 11, day: 31, inMonth: false });
    assert.deepEqual(cells[2], { year: 2025, month: 0, day: 1, inMonth: true });
    const last = cells[cells.length - 1];
    assert.equal(last.inMonth, false);
    assert.equal(last.month, 1);
  });

  await test('buildMonthCells: leap February 2024 has 29 days', () => {
    const cells = buildMonthCells(2024, 1);
    const inMonth = cells.filter((c) => c.inMonth);
    assert.equal(inMonth.length, 29);
    assert.equal(inMonth[28].day, 29);
  });
} finally {
  await server.close();
}
