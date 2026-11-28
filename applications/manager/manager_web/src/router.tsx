import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, type MutableRefObject } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';

/** 当前所在的顶层 URI 段(basename,如 ``/manager``)。空串=根。 */
const BasenameContext = createContext('');
const NavigationGuardContext = createContext<MutableRefObject<((target: string) => boolean) | null> | null>(null);

/**
 * 在某个顶层 URI 段(basename)内提供"子路径相对路由"。
 * 底层基于 react-router 的 history;**保留旧 ``useRouter`` API**(path/params/navigate)
 * 以零改动复用既有管理面页面——它们仍用 ``/instances`` 等子路径,basename 自动补 ``/manager``。
 */
export function RouterProvider({ children, basename = '' }: { children: ReactNode; basename?: string }) {
  const guard = useRef<((target: string) => boolean) | null>(null);
  const location = useLocation();
  const acceptedEntry = useRef({ index: window.history.state?.idx as number | undefined });
  useEffect(() => { acceptedEntry.current = { index: window.history.state?.idx }; }, [location]);
  useEffect(() => {
    let restoring = false;
    const protectHistory = (event: PopStateEvent) => {
      if (restoring) { restoring = false; event.stopImmediatePropagation(); return; }
      const pathname = window.location.pathname;
      const sub = basename && (pathname === basename || pathname.startsWith(basename + '/')) ? pathname.slice(basename.length) || '/' : pathname;
      if (!guard.current || guard.current(sub + window.location.search)) return;
      const from = acceptedEntry.current.index;
      const to = event.state?.idx as number | undefined;
      if (typeof from === 'number' && typeof to === 'number' && from !== to) {
        event.stopImmediatePropagation();
        restoring = true;
        window.history.go(from - to);
      }
    };
    // Capture before BrowserRouter's popstate listener so a cancelled Back keeps drafts mounted.
    window.addEventListener('popstate', protectHistory, true);
    return () => window.removeEventListener('popstate', protectHistory, true);
  }, [basename]);
  return <BasenameContext.Provider value={basename}><NavigationGuardContext.Provider value={guard}>{children}</NavigationGuardContext.Provider></BasenameContext.Provider>;
}

/** Lets a workspace protect unsaved drafts for all links using this router. */
export function useNavigationGuard(check: (target: string) => boolean) {
  const guard = useContext(NavigationGuardContext);
  const latest = useRef(check);
  latest.current = check;
  useEffect(() => {
    if (!guard) return;
    const callback = (target: string) => latest.current(target);
    guard.current = callback;
    return () => { if (guard.current === callback) guard.current = null; };
  }, [guard]);
}

interface RouterValue {
  /** 去掉 basename 后的子路径(如 ``/instances``;在 basename 根时为 ``/``)。 */
  path: string;
  params: URLSearchParams;
  /** 接收子路径(如 ``/instances``),自动补上 basename 后跳转。 */
  navigate: (path: string, options?: { replace?: boolean }) => void;
}

export function useRouter(): RouterValue {
  const basename = useContext(BasenameContext);
  const guard = useContext(NavigationGuardContext);
  const location = useLocation();
  const navigateRR = useNavigate();
  const [searchParams] = useSearchParams();

  let path = location.pathname;
  if (basename && (path === basename || path.startsWith(`${basename}/`))) {
    path = path.slice(basename.length) || '/';
  }

  const navigate = useCallback(
    (target: string, options?: { replace?: boolean }) => {
      const sub = target.startsWith('/') ? target : `/${target}`;
      if (guard?.current && !guard.current(sub)) return;
      navigateRR(`${basename}${sub}`, options);
    },
    [basename, navigateRR, guard],
  );

  return { path, params: searchParams, navigate };
}

/** 匹配 ``/a/:foo/b`` 风格的路径，返回 params 字典或 null。(纯工具) */
export function matchRoute(pattern: string, path: string): Record<string, string> | null {
  const ps = pattern.replace(/^\//, '').split('/');
  const xs = path.replace(/^\//, '').split('/');
  if (ps.length !== xs.length) return null;
  const out: Record<string, string> = {};
  for (let i = 0; i < ps.length; i++) {
    const p = ps[i];
    const v = xs[i];
    if (p.startsWith(':')) {
      out[p.slice(1)] = decodeURIComponent(v);
    } else if (p !== v) {
      return null;
    }
  }
  return out;
}
