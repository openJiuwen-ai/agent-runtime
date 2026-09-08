import { useCallback, useRef, useState } from 'react';
import { useDismissWhenPointerLeaves } from '../hooks/useDismissWhenPointerLeaves';

export type ColumnFilterOption = {
  value: string;
  label: string;
};

type TableColumnFilterProps = {
  label: string;
  value: string;
  options: ColumnFilterOption[];
  onChange: (value: string) => void;
  /** 仅展示筛选图标，不展示 label 文本（仍用于 aria-label） */
  iconOnly?: boolean;
};

export function TableColumnFilter({
  label,
  value,
  options,
  onChange,
  iconOnly = false,
}: TableColumnFilterProps) {
  const [open, setOpen] = useState(false);
  const hostRef = useRef<HTMLDivElement>(null);
  const active = value !== '';
  const dismissMenu = useCallback(() => setOpen(false), []);
  useDismissWhenPointerLeaves(hostRef, open, dismissMenu);

  return (
    <div className="th-filter" ref={hostRef}>
      {!iconOnly && <span className="th-filter__label">{label}</span>}
      <button
        type="button"
        className={`th-filter__btn${active ? ' active' : ''}${open ? ' open' : ''}`}
        aria-label={label}
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <svg className="th-filter__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M3 4.5h18M7 9.75h10M10.5 15h3"
          />
        </svg>
      </button>
      {open && (
        <div className="th-filter__menu dropdown-menu-surface" role="menu">
          {options.map((opt) => (
            <button
              key={opt.value || '__all__'}
              type="button"
              role="menuitemradio"
              aria-checked={value === opt.value}
              className={`th-filter__item dropdown-menu-option${value === opt.value ? ' selected' : ''}`}
              onClick={() => {
                onChange(opt.value);
                setOpen(false);
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
