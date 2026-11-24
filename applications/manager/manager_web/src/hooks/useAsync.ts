import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../services/api';

interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): AsyncState<T> & { reload: () => Promise<void> } {
  const [state, setState] = useState<AsyncState<T>>({ data: null, loading: true, error: null });

  const requestId = useRef(0);

  const reload = useCallback(async () => {
    const current = ++requestId.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await fn();
      if (current !== requestId.current) return;
      setState({ data, loading: false, error: null });
    } catch (e) {
      if (current !== requestId.current) return;
      const message = e instanceof ApiError ? e.detail || e.message : (e as Error).message;
      setState({ data: null, loading: false, error: message });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    void reload();
    return () => { requestId.current += 1; };
  }, [reload]);

  return { ...state, reload };
}
