import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext';
import { isPlatformAdmin } from '../services/api';
import { SetupJourney, useSetupJourney } from './SetupJourney';
import { useRouter } from '../router';
import { readSetupGuide, setupGuideRouteCluster } from '../utils/setupGuide';

export function ConfigGuideMenu() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { path, params } = useRouter();
  const routeCluster = setupGuideRouteCluster(path, params);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelClose = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };
  const scheduleClose = () => {
    cancelClose();
    // Allow crossing the small gap between the trigger and the dropdown.
    closeTimer.current = setTimeout(() => setOpen(false), 180);
  };

  useEffect(() => () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
  }, []);

  useEffect(() => { setOpen(false); }, [path]);

  useEffect(() => {
    setOpen(false);
    const key = `manager:setup-journey:${user?.user_id || ''}`;
    let selection = readSetupGuide(null);
    try {
      selection = readSetupGuide(localStorage.getItem(key));
    } catch { /* Unavailable or invalid storage must not block navigation. */ }
    useSetupJourney.setState(selection);
    return useSetupJourney.subscribe(state => {
      try { localStorage.setItem(key, JSON.stringify({ version: 2, stage: state.stage, clusterId: state.clusterId, showOptional: state.showOptional })); }
      catch { /* Keep in-memory progress when storage is unavailable. */ }
    });
  }, [user?.user_id]);

  useEffect(() => {
    if (routeCluster) useSetupJourney.getState().selectCluster(routeCluster);
  }, [routeCluster, user?.user_id]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setOpen(false); triggerRef.current?.focus(); }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  if (!isPlatformAdmin(user)) return null;
  return <div className="relative guide-menu-root" ref={rootRef}
    onPointerEnter={cancelClose}
    onPointerLeave={(event) => { if (event.pointerType === 'mouse' || event.pointerType === 'pen') scheduleClose(); }}
    onBlur={(event) => { if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false); }}>
    <button ref={triggerRef} type="button" className={`guide-trigger ${open ? 'guide-trigger--open' : ''}`}
      aria-expanded={open} aria-controls="manager-setup-guide"
      onPointerEnter={(event) => { if (event.pointerType !== 'touch') { cancelClose(); setOpen(true); } }}
      onClick={() => { cancelClose(); setOpen(true); }}>
      <span aria-hidden="true">▦</span>{t('guide.configGuide')}<span aria-hidden="true">⌄</span>
    </button>
    {open && <div id="manager-setup-guide" className="guide-menu guide-menu--journey">
      <div className="guide-menu__journey"><SetupJourney compact onNavigate={() => setOpen(false)} /></div>
    </div>}
  </div>;
}
