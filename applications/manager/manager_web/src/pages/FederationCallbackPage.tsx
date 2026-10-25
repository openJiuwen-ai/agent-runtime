import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext';
import { consumeBrowserCallback, discardBrowserSession, rememberBrowserSession } from '../auth/federation/client';

export function FederationCallbackPage() {
  const { connectionId = '' } = useParams();
  const { completeBrowserLogin } = useAuth();
  const { t } = useTranslation();
  const started = useRef(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void (async () => {
      try {
        const token = await consumeBrowserCallback(connectionId);
        window.history.replaceState({}, '', window.location.pathname);
        await completeBrowserLogin(connectionId, token);
        rememberBrowserSession(connectionId);
        window.location.replace('/auth');
      } catch {
        window.history.replaceState({}, '', window.location.pathname);
        await discardBrowserSession(connectionId).catch(() => undefined);
        setFailed(true);
      }
    })();
  }, [connectionId, completeBrowserLogin]);
  return <div className="flex flex-col items-center justify-center h-screen gap-3">
    <div>{t(failed ? 'auth.federationFailed' : 'auth.loggingIn')}</div>
    {failed && <a className="btn" href="/auth">{t('auth.backToLogin')}</a>}
  </div>;
}
