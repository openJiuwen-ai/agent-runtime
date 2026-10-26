import { useEffect } from 'react';
import { maintainBrowserSession } from '../auth/federation/client';

/** Optional same-origin OIDC lifecycle owner while the user console is active. */
export function FederationSessionPage() {
  useEffect(() => {
    let cancelled = false;
    let stop: (() => void) | undefined;
    void maintainBrowserSession().then((cleanup) => {
      if (cancelled) cleanup();
      else stop = cleanup;
    }).catch(() => undefined);
    return () => { cancelled = true; stop?.(); };
  }, []);
  return null;
}
