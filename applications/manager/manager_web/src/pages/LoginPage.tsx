import { FormEvent, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext';
import { ApiError, AuthApi, FederationConnection } from '../services/api';

export function LoginPage() {
  const { t } = useTranslation();
  const { completeFederatedLogin, login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [connections, setConnections] = useState<FederationConnection[]>([]);
  const exchangeStarted = useRef(false);

  useEffect(() => {
    let cancelled = false;
    void AuthApi.federationConnections()
      .then((result) => {
        if (!cancelled) setConnections(result.connections);
      })
      .catch(() => {
        if (!cancelled) setConnections([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get('federation_code');
    if (!code || exchangeStarted.current) return;
    exchangeStarted.current = true;
    setSubmitting(true);
    window.history.replaceState({}, '', '/auth');
    void completeFederatedLogin(code)
      .catch(() => setError(t('auth.federationFailed')))
      .finally(() => setSubmitting(false));
  }, [completeFederatedLogin, t]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      // 后端用稳定 code(auth_bad_credentials / auth_disabled)→ 映射到双语文案;
      // 未知错误回退到通用句子,保证始终是规整句子。
      const codeMsg: Record<string, string> = {
        auth_bad_credentials: t('auth.errBadCredentials'),
        auth_disabled: t('auth.errDisabled'),
      };
      const detail = err instanceof ApiError ? err.detail : '';
      setError(codeMsg[detail] ?? t('auth.loginFailed'));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex items-center justify-center h-screen p-8">
      <form className="card" style={{ width: 360 }} onSubmit={onSubmit}>
        <div className="flex items-center gap-2 mb-4">
          <img src="/logo.png" alt="JiuwenSwarm" style={{ height: 28 }} />
          <div className="card-title">{t('auth.loginTitle')}</div>
        </div>

        <label className="label" htmlFor="login-username">{t('auth.username')}</label>
        <input
          id="login-username"
          className="input"
          autoFocus
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />

        <label className="label mt-3" htmlFor="login-password">{t('auth.password')}</label>
        <input
          id="login-password"
          className="input"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        {error && <div className="text-danger text-sm mt-3">{error}</div>}

        <button className="btn primary mt-4" type="submit" disabled={submitting || !username || !password} style={{ width: '100%' }}>
          {submitting ? t('auth.loggingIn') : t('auth.login')}
        </button>

        {connections.length > 0 && (
          <div className="mt-4">
            <div className="text-muted text-sm" style={{ textAlign: 'center' }}>
              {t('auth.orFederated')}
            </div>
            {connections.map((connection) => (
              <button
                className="btn mt-3"
                key={connection.connection_id}
                onClick={() => AuthApi.beginFederatedLogin(connection.connection_id)}
                type="button"
                disabled={submitting}
                style={{ width: '100%' }}
              >
                {t('auth.signInWith', { name: connection.name })}
              </button>
            ))}
          </div>
        )}
      </form>
    </div>
  );
}
