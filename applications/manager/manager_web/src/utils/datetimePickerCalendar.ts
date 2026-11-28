export type DayCell = {
  year: number;
  month: number;
  day: number;
  inMonth: boolean;
};

/** Format as local `YYYY-MM-DD`; `month` is 0-based. */
export function formatCellDate(year: number, month: number, day: number): string {
  return `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

/** Build calendar cells including adjacent-month days; week starts Monday; only complete weeks. */
export function buildMonthCells(year: number, month: number): DayCell[] {
  const firstWeekday = new Date(year, month, 1).getDay();
  const mondayOffset = (firstWeekday + 6) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const daysInPrev = new Date(year, month, 0).getDate();
  const prev = month === 0 ? { year: year - 1, month: 11 } : { year, month: month - 1 };
  const next = month === 11 ? { year: year + 1, month: 0 } : { year, month: month + 1 };

  const cells: DayCell[] = [];
  for (let i = 0; i < mondayOffset; i++) {
    cells.push({
      year: prev.year,
      month: prev.month,
      day: daysInPrev - mondayOffset + 1 + i,
      inMonth: false,
    });
  }
  for (let day = 1; day <= daysInMonth; day++) {
    cells.push({ year, month, day, inMonth: true });
  }
  let nextDay = 1;
  while (cells.length % 7 !== 0) {
    cells.push({
      year: next.year,
      month: next.month,
      day: nextDay++,
      inMonth: false,
    });
  }
  return cells;
}
