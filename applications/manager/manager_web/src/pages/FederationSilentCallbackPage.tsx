import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { consumeSilentCallback } from '../auth/federation/client';

export function FederationSilentCallbackPage() {
  const { connectionId = '' } = useParams();
  const { t } = useTranslation();
  const started = useRef(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void consumeSilentCallback(connectionId).catch(() => setFailed(true));
  }, [connectionId]);
  return failed ? <div>{t('auth.federationFailed')}</div> : null;
}
