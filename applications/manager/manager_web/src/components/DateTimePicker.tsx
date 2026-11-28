import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { joinDatetimeLocal, splitDatetimeLocal } from '../utils/datetimeLocal';

/** Monday-first weekday order (matches zh Ant Design calendar). */
const WEEKDAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const;
const HOURS = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0'));
const MINUTES = Array.from({ length: 60 }, (_, i) => String(i).padStart(2, '0'));
const PANEL_WIDTH = 400;

interface DateTimePickerProps {
  value: string;
  onChange: (v: string) => void;
  className?: string;
  /** Empty-state text inside the trigger; defaults to i18n format hint. */
  placeholder?: string;
  'aria-label'?: string;
}

type DayCell = {
  year: number;
  month: number;
  day: number;
  inMonth: boolean;
};

function calendarDate(value: string): Date {
  const [year, month, day] = value.split('-').map(Number);
  return new Date(year!, month! - 1, day!);
}

function todayLocalDate(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function formatCellDate(year: number, month: number, day: number): string {
  return `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

/** Build calendar cells including adjacent-month days; week starts Monday; only complete weeks. */
function buildMonthCells(year: number, month: number): DayCell[] {
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

/** Combined local datetime editor; value is `YYYY-MM-DDTHH:mm` or empty. */
export function DateTimePicker({
  value,
  onChange,
  className = '',
  placeholder,
  'aria-label': ariaLabel,
}: DateTimePickerProps) {
  const { t } = useTranslation();
  const { date: selectedDate, time: selectedTime } = splitDatetimeLocal(value);
  const initial = selectedDate ? calendarDate(selectedDate) : new Date();
  const todayStr = todayLocalDate();

  const [open, setOpen] = useState(false);
  const [panelView, setPanelView] = useState<'day' | 'year'>('day');
  const [cursor, setCursor] = useState({ year: initial.getFullYear(), month: initial.getMonth() });
  const [yearPageStart, setYearPageStart] = useState(initial.getFullYear() - 5);
  const [panelStyle, setPanelStyle] = useState<CSSProperties>({});
  const hostRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const hourBtnRef = useRef<HTMLButtonElement>(null);
  const minuteBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!selectedDate) return;
    const d = calendarDate(selectedDate);
    setCursor({ year: d.getFullYear(), month: d.getMonth() });
  }, [selectedDate]);

  useEffect(() => {
    if (!open) {
      setPanelView('day');
    }
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !hostRef.current) return;
    const sync = () => {
      const rect = hostRef.current?.getBoundingClientRect();
      if (!rect) return;
      const panelW = Math.min(PANEL_WIDTH, Math.max(rect.width, 340));
      const maxH = 380;
      const spaceBelow = window.innerHeight - rect.bottom - 8;
      const openUp = spaceBelow < Math.min(maxH, 160) && rect.top > spaceBelow;
      const left = Math.min(rect.left, window.innerWidth - panelW - 8);
      setPanelStyle({
        position: 'fixed',
        left: Math.max(8, left),
        width: panelW,
        zIndex: 1100,
        maxHeight: Math.min(maxH, openUp ? rect.top - 8 : spaceBelow),
        ...(openUp
          ? { bottom: window.innerHeight - rect.top + 4 }
          : { top: rect.bottom + 4 }),
      });
    };
    sync();
    window.addEventListener('resize', sync);
    window.addEventListener('scroll', sync, true);
    return () => {
      window.removeEventListener('resize', sync);
      window.removeEventListener('scroll', sync, true);
    };
  }, [open, panelView]);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      const path = event.composedPath();
      if (hostRef.current && path.includes(hostRef.current)) return;
      if (panelRef.current && path.includes(panelRef.current)) return;
      setOpen(false);
    };
    window.addEventListener('pointerdown', onDown, true);
    return () => window.removeEventListener('pointerdown', onDown, true);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      event.preventDefault();
      if (panelView === 'year') {
        setPanelView('day');
        return;
      }
      setOpen(false);
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open, panelView]);

  useLayoutEffect(() => {
    if (!open || panelView !== 'day') return;
    hourBtnRef.current?.scrollIntoView({ block: 'nearest' });
    minuteBtnRef.current?.scrollIntoView({ block: 'nearest' });
  }, [open, panelView, selectedTime]);

  const cells = buildMonthCells(cursor.year, cursor.month);
  const [hour, minute] = selectedTime ? selectedTime.split(':') : ['', ''];

  function pickCell(cell: DayCell) {
    if (!cell.inMonth) {
      setCursor({ year: cell.year, month: cell.month });
    }
    const nextDate = formatCellDate(cell.year, cell.month, cell.day);
    onChange(joinDatetimeLocal(nextDate, selectedTime || '00:00'));
  }

  function pickHour(hh: string) {
    const date = selectedDate || todayLocalDate();
    onChange(joinDatetimeLocal(date, `${hh}:${minute || '00'}`));
  }

  function pickMinute(mm: string) {
    const date = selectedDate || todayLocalDate();
    onChange(joinDatetimeLocal(date, `${hour || '00'}:${mm}`));
  }

  function clear() {
    onChange('');
    setPanelView('day');
    setOpen(false);
  }

  function pickToday() {
    const today = todayLocalDate();
    const d = calendarDate(today);
    setCursor({ year: d.getFullYear(), month: d.getMonth() });
    setPanelView('day');
    onChange(joinDatetimeLocal(today, selectedTime || '00:00'));
  }

  function shiftMonth(delta: number) {
    setCursor((c) => {
      const d = new Date(c.year, c.month + delta, 1);
      return { year: d.getFullYear(), month: d.getMonth() };
    });
  }

  function openYearView() {
    setYearPageStart(cursor.year - 5);
    setPanelView('year');
  }

  function pickYear(year: number) {
    setCursor((c) => ({ ...c, year }));
    setPanelView('day');
  }

  const emptyText = placeholder ?? t('dateTimePicker.placeholder');
  const display = value ? value.replace('T', ' ') : emptyText;
  const label = ariaLabel ?? t('dateTimePicker.pickDateTime');

  const panelNode = open
    ? createPortal(
        <div
          ref={panelRef}
          className="flex flex-col overflow-hidden rounded-lg border border-border bg-card shadow-lg"
          style={panelStyle}
          role="dialog"
          aria-label={label}
        >
          {panelView === 'year' ? (
            <div className="flex min-h-0 flex-1 flex-col p-3">
              <div className="mb-3 flex items-center justify-between">
                <button
                  type="button"
                  title={t('dateTimePicker.prevYear')}
                  aria-label={t('dateTimePicker.prevYear')}
                  onClick={() => setYearPageStart((y) => y - 12)}
                  className="rounded p-1 text-muted hover:bg-bg-hover hover:text-text"
                >
                  <NavChevronLeft />
                </button>
                <span className="text-sm font-medium text-text">
                  {yearPageStart} – {yearPageStart + 11}
                </span>
                <button
                  type="button"
                  title={t('dateTimePicker.nextYear')}
                  aria-label={t('dateTimePicker.nextYear')}
                  onClick={() => setYearPageStart((y) => y + 12)}
                  className="rounded p-1 text-muted hover:bg-bg-hover hover:text-text"
                >
                  <NavChevronRight />
                </button>
              </div>
              <div className="grid grid-cols-3 gap-2">
                {Array.from({ length: 12 }, (_, i) => yearPageStart + i).map((y) => (
                  <button
                    key={y}
                    type="button"
                    onClick={() => pickYear(y)}
                    className={`rounded-sm py-2 text-sm transition-colors ${
                      y === cursor.year
                        ? 'bg-[#1677ff] text-white'
                        : 'text-text hover:bg-bg-hover'
                    }`}
                  >
                    {y}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            /* Height follows the calendar; time columns are pinned to that height and scroll. */
            <div className="relative flex">
              <div className="min-w-0 flex-1 p-3 pb-2">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <button
                    type="button"
                    className="inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-sm font-medium text-text hover:bg-bg-hover"
                    onClick={openYearView}
                  >
                    {t('dateTimePicker.yearMonthHeader', {
                      year: cursor.year,
                      month: cursor.month + 1,
                    })}
                    <CaretDown />
                  </button>
                  <div className="flex items-center gap-0.5">
                    <button
                      type="button"
                      title={t('dateTimePicker.prevMonth')}
                      aria-label={t('dateTimePicker.prevMonth')}
                      onClick={() => shiftMonth(-1)}
                      className="rounded p-1 text-muted hover:bg-bg-hover hover:text-text"
                    >
                      <NavChevronUp />
                    </button>
                    <button
                      type="button"
                      title={t('dateTimePicker.nextMonth')}
                      aria-label={t('dateTimePicker.nextMonth')}
                      onClick={() => shiftMonth(1)}
                      className="rounded p-1 text-muted hover:bg-bg-hover hover:text-text"
                    >
                      <NavChevronDown />
                    </button>
                  </div>
                </div>
                <div className="mb-1 grid grid-cols-7 text-center text-xs text-muted">
                  {WEEKDAY_KEYS.map((key) => (
                    <span key={key} className="py-1">
                      {t(`dateTimePicker.weekday.${key}`)}
                    </span>
                  ))}
                </div>
                <div className="grid grid-cols-7">
                  {cells.map((cell) => {
                    const cellDate = formatCellDate(cell.year, cell.month, cell.day);
                    const isSelected = selectedDate === cellDate;
                    const isToday = cellDate === todayStr;
                    return (
                      <button
                        key={cellDate}
                        type="button"
                        onClick={() => pickCell(cell)}
                        className={`mx-auto my-0.5 flex h-6 w-6 items-center justify-center rounded-sm text-sm transition-colors ${
                          isSelected
                            ? 'bg-[#1677ff] text-white'
                            : isToday
                              ? 'border border-[#1677ff] text-[#1677ff] hover:bg-[#1677ff14]'
                              : cell.inMonth
                                ? 'text-text hover:bg-bg-hover'
                                : 'text-muted hover:bg-bg-hover'
                        }`}
                      >
                        {cell.day}
                      </button>
                    );
                  })}
                </div>
              </div>
              <div className="w-[112px] shrink-0" aria-hidden />
              <div className="absolute inset-y-0 right-0 flex w-[112px] border-l border-border">
                <div className="min-h-0 flex-1 overflow-y-auto">
                  {HOURS.map((hh) => {
                    const selected = hh === hour;
                    return (
                      <button
                        key={hh}
                        ref={selected ? hourBtnRef : undefined}
                        type="button"
                        onClick={() => pickHour(hh)}
                        className={`block w-full py-1 text-center text-sm ${
                          selected
                            ? 'bg-[#1677ff] text-white'
                            : 'text-text hover:bg-bg-hover'
                        }`}
                      >
                        {hh}
                      </button>
                    );
                  })}
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto border-l border-border">
                  {MINUTES.map((mm) => {
                    const selected = mm === minute;
                    return (
                      <button
                        key={mm}
                        ref={selected ? minuteBtnRef : undefined}
                        type="button"
                        onClick={() => pickMinute(mm)}
                        className={`block w-full py-1 text-center text-sm ${
                          selected
                            ? 'bg-[#1677ff] text-white'
                            : 'text-text hover:bg-bg-hover'
                        }`}
                      >
                        {mm}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
          <div className="flex shrink-0 items-center justify-between border-t border-border px-3 py-1.5">
            <button
              type="button"
              className="text-sm text-[#1677ff] hover:text-[#4096ff]"
              onClick={clear}
            >
              {t('dateTimePicker.clear')}
            </button>
            <button
              type="button"
              className="text-sm text-[#1677ff] hover:text-[#4096ff]"
              onClick={pickToday}
            >
              {t('dateTimePicker.today')}
            </button>
          </div>
        </div>,
        document.body,
      )
    : null;

  return (
    <div className={`relative ${className}`} ref={hostRef}>
      <button
        type="button"
        className="input mt-0 flex w-full cursor-pointer items-center gap-1.5 text-left"
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={`min-w-0 flex-1 truncate ${value ? 'text-text' : 'text-muted'}`}>
          {display}
        </span>
        <CalendarIcon />
      </button>
      {panelNode}
    </div>
  );
}

function CalendarIcon() {
  return (
    <svg
      className="h-3.5 w-3.5 shrink-0 text-muted"
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
      strokeWidth={2}
      aria-hidden
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
      />
    </svg>
  );
}

function CaretDown() {
  return (
    <svg className="h-3 w-3 text-muted" fill="currentColor" viewBox="0 0 24 24" aria-hidden>
      <path d="M7 10l5 5 5-5H7z" />
    </svg>
  );
}

function NavChevronLeft() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
    </svg>
  );
}

function NavChevronRight() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
    </svg>
  );
}

function NavChevronUp() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
    </svg>
  );
}

function NavChevronDown() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
    </svg>
  );
}
