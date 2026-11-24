import { useCallback, useEffect, useRef, type ReactNode } from 'react';
import { useRouter } from '../../../router';
import { useGuideStore } from '../../../stores/guideStore';
import { admissionTab, type AdmissionTab } from '../../../utils/admissionNavigation';
import './InstanceAdmissionSections.css';

/** Keep legacy accessTab links as section anchors without hiding either list. */
export function InstanceAdmissionSections({ organizations, users }: { organizations: ReactNode; users: ReactNode }) {
  const { params } = useRouter();
  const openTarget = useGuideStore(s => s.openTarget);
  const requested = admissionTab(params);
  const organizationSection = useRef<HTMLElement>(null);
  const userSection = useRef<HTMLElement>(null);
  const pendingAnchor = useRef<AdmissionTab | null>(null);

  const alignAnchor = useCallback(() => {
    const section = pendingAnchor.current === 'users' ? userSection.current
      : pendingAnchor.current === 'orgs' ? organizationSection.current : null;
    section?.scrollIntoView({ block: 'nearest', behavior: 'auto' });
  }, []);

  useEffect(() => {
    pendingAnchor.current = requested;
    const frame = requestAnimationFrame(alignAnchor);
    return () => cancelAnimationFrame(frame);
  }, [requested, alignAnchor]);

  useEffect(() => {
    if (openTarget !== 'accessUsers' && openTarget !== 'accessOrgs') return;
    pendingAnchor.current = openTarget === 'accessUsers' ? 'users' : 'orgs';
    alignAnchor();
  }, [openTarget, alignAnchor]);

  useEffect(() => {
    const container = organizationSection.current?.closest('main');
    const releaseAnchor = () => { pendingAnchor.current = null; };
    // Organization rows load asynchronously above the user section. Keep the
    // incoming anchor visible until the user starts interacting with the page.
    const observer = new ResizeObserver(alignAnchor);
    if (organizationSection.current) observer.observe(organizationSection.current);
    if (userSection.current) observer.observe(userSection.current);
    const events = ['wheel', 'touchstart', 'pointerdown', 'keydown'] as const;
    events.forEach(event => container?.addEventListener(event, releaseAnchor, { passive: true }));
    return () => {
      observer.disconnect();
      events.forEach(event => container?.removeEventListener(event, releaseAnchor));
    };
  }, [alignAnchor]);

  return (
    <div className="cluster-admission">
      <section ref={organizationSection} className="cluster-admission-section" aria-labelledby="cluster-admission-orgs-title">
        {organizations}
      </section>
      <section ref={userSection} className="cluster-admission-section" aria-labelledby="cluster-admission-users-title">
        {users}
      </section>
    </div>
  );
}
