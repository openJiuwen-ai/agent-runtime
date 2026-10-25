import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { AuthApi, clearTokens } from '../services/api';
import { consumeLogoutCallback } from '../auth/federation/client';

export function FederationLogoutPage({ callback = false }: { callback?: boolean }) {
  const { connectionId = '' } = useParams();
  const { t } = useTranslation();
  const started = useRef(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void (async () => {
      if (callback) {
        try {
          await consumeLogoutCallback(connectionId);
        } catch {
          clearTokens();
          setFailed(true);
          return;
        }
        clearTokens();
        window.location.replace('/auth');
      } else {
        const redirecting = await AuthApi.logout();
        // signoutRedirect navigates the document; only local sessions fall back.
        if (!redirecting) window.location.replace('/auth');
      }
    })();
  }, [callback, connectionId]);
  return <div className="flex flex-col items-center justify-center h-screen gap-3">
    <div>{t(failed ? 'auth.federationFailed' : 'auth.loading')}</div>
    {failed && <a className="btn" href="/auth">{t('auth.backToLogin')}</a>}
  </div>;
}
