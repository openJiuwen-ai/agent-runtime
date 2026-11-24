import { useEffect, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { useRouter } from '../router';
import {
  admissionCatalogPath, admissionDraftKey, admissionTab, clearAdmissionDraft,
  emptyAdmissionDraft, readAdmissionDraft, writeAdmissionDraft,
  type AdmissionDraft, type AdmissionTab,
} from '../utils/admissionNavigation';

/** Preparing an identity is separate from granting it admission to this cluster. */
export function useAdmissionHandoff(instanceId: string, tab: AdmissionTab, open: () => void) {
  const { user } = useAuth();
  const { path, params, navigate } = useRouter();
  const key = admissionDraftKey(user?.user_id ?? 'anonymous', instanceId, tab);
  const [draft, setDraft] = useState<AdmissionDraft>({ ...emptyAdmissionDraft, ids: [] });

  useEffect(() => {
    if (params.get('admissionResume') !== '1' || admissionTab(params) !== tab) return;
    setDraft(readAdmissionDraft(sessionStorage, key));
    open();
    const query = new URLSearchParams(params);
    query.delete('admissionResume');
    navigate(`${path}${query.size ? `?${query}` : ''}`, { replace: true });
    // open is the caller's setShowAdd callback; routing is the handoff trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, params, path, tab, navigate]);

  const query = new URLSearchParams(params);
  query.set('admissionResume', '1');
  return {
    initialDraft: draft,
    managePath: admissionCatalogPath(tab, query, instanceId),
    prepare: (value: AdmissionDraft) => writeAdmissionDraft(sessionStorage, key, value),
    clear: () => {
      clearAdmissionDraft(sessionStorage, key);
      setDraft({ ...emptyAdmissionDraft, ids: [] });
    },
  };
}
