import type { ChangeEvent, CompositionEvent, KeyboardEvent } from 'react';

interface LimitedTextInputProps {
  ariaLabel?: string;
  value: string;
  onChange: (value: string) => void;
  maxLength: number;
  type?: 'text' | 'password';
  className?: string;
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  /** 关联 `<datalist id=…>`，用于可输入下拉建议 */
  list?: string;
  onFocus?: () => void;
  onBlur?: () => void;
  onKeyDown?: (event: KeyboardEvent<HTMLInputElement>) => void;
  autoComplete?: string;
  autoFocus?: boolean;
}

function truncate(value: string, limit: number): string {
  return value.length <= limit ? value : value.slice(0, limit);
}

function duringImeComposition(event: ChangeEvent<HTMLInputElement>): boolean {
  const native = event.nativeEvent;
  return native instanceof InputEvent && native.isComposing;
}

export function LimitedTextInput({
  ariaLabel,
  value,
  onChange,
  maxLength,
  type = 'text',
  className,
  placeholder,
  disabled,
  required,
  list,
  onFocus,
  onBlur,
  onKeyDown,
  autoComplete,
  autoFocus,
}: LimitedTextInputProps) {
  const atLimit = value.length >= maxLength;

  const syncValue = (event: ChangeEvent<HTMLInputElement>) => {
    const raw = event.target.value;
    onChange(duringImeComposition(event) ? raw : truncate(raw, maxLength));
  };

  const syncAfterIme = (event: CompositionEvent<HTMLInputElement>) => {
    onChange(truncate(event.currentTarget.value, maxLength));
  };

  return (
    <div className="flex items-center gap-2">
      <input
        className={`input flex-1 min-w-0 ${className ?? ''}`}
        type={type}
        aria-label={ariaLabel}
        value={value}
        maxLength={maxLength}
        placeholder={placeholder}
        disabled={disabled}
        required={required}
        list={list}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        onFocus={onFocus}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        onChange={syncValue}
        onCompositionEnd={syncAfterIme}
      />
      <span
        className={`text-[11px] tabular-nums shrink-0 ${atLimit ? 'text-danger' : 'text-muted'}`}
        aria-live="polite"
      >
        {value.length}/{maxLength}
      </span>
    </div>
  );
}
