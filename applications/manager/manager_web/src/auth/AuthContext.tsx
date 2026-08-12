import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
} from 'react';
import { AuthApi, AuthUser, clearTokens, hasSession, setUnauthorizedHandler } from '../services/api';

interface AuthContextValue {
  user: AuthUser | null;
  ready: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

type SessionState = {
  account: AuthUser | null;
  bootstrapped: boolean;
};

type SessionAction =
  | { kind: 'hydrate'; account: AuthUser | null }
  | { kind: 'signed-in'; account: AuthUser }
  | { kind: 'cleared' };

const EMPTY_SESSION: SessionState = { account: null, bootstrapped: false };

function reduceSession(state: SessionState, action: SessionAction): SessionState {
  switch (action.kind) {
    case 'hydrate':
      return { account: action.account, bootstrapped: true };
    case 'signed-in':
      return { ...state, account: action.account };
    case 'cleared':
      return { ...state, account: null };
    default:
      return state;
  }
}

async function restoreAccount(): Promise<AuthUser | null> {
  if (!hasSession()) return null;
  try {
    return await AuthApi.me();
  } catch {
    clearTokens();
    return null;
  }
}

const ManagerSession = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, dispatch] = useReducer(reduceSession, EMPTY_SESSION);

  useEffect(() => {
    let cancelled = false;
    void restoreAccount().then((account) => {
      if (!cancelled) dispatch({ kind: 'hydrate', account });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearTokens();
      dispatch({ kind: 'cleared' });
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const account = await AuthApi.login(username, password);
    dispatch({ kind: 'signed-in', account });
  }, []);

  const logout = useCallback(async () => {
    await AuthApi.logout();
    dispatch({ kind: 'cleared' });
  }, []);

  const sessionApi = useMemo<AuthContextValue>(
    () => ({
      user: session.account,
      ready: session.bootstrapped,
      login,
      logout,
    }),
    [session.account, session.bootstrapped, login, logout],
  );

  return <ManagerSession.Provider value={sessionApi}>{children}</ManagerSession.Provider>;
}

export function useAuth(): AuthContextValue {
  const session = useContext(ManagerSession);
  if (session === undefined) {
    throw new Error('Manager session is not mounted');
  }
  return session;
}
