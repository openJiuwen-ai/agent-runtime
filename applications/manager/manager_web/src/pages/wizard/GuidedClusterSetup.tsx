import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { useNavigationGuard, useRouter } from '../../router';
import { InstanceApi } from '../../services/api';
import type { InstanceDetail } from '../../types';
import { guidedClusterWizardPath, type GuidedClusterStage } from '../../utils/clusterWizard';
import { WizardAdmissionStep } from './WizardAdmissionStep';
import { WizardRuntimeStep } from './WizardRuntimeStep';
import { WizardAgentStep } from './WizardAgentStep';
import { WizardVerificationStep } from './WizardVerificationStep';
import { WizardConnectionStep } from './WizardConnectionStep';
import './GuidedClusterSetup.css';

export function GuidedClusterSetup({ instanceId, stage }: { instanceId: string; stage: GuidedClusterStage }) {
  const { i18n } = useTranslation();
  const { navigate, params } = useRouter();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const instance = useAsync(() => InstanceApi.get(instanceId), [instanceId]);
  const [lastInstance, setLastInstance] = useState<InstanceDetail | null>(null);
  const [visited, setVisited] = useState<Set<number>>(() => new Set([stage]));
  const [pending, setPending] = useState<Record<number, boolean>>({});
  const [revision, setRevision] = useState(0);
  const pendingRef = useRef(pending);
  const stageRef = useRef(stage);
  const mounted = useRef(true);
  pendingRef.current = pending;
  stageRef.current = stage;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const updatePending = useCallback((step: number, busy: boolean) => setPending(value => value[step] === busy ? value : { ...value, [step]: busy }), []);
  const pending1 = useCallback((busy: boolean) => updatePending(1, busy), [updatePending]);
  const pending2 = useCallback((busy: boolean) => updatePending(2, busy), [updatePending]);
  const pending3 = useCallback((busy: boolean) => updatePending(3, busy), [updatePending]);
  const pending4 = useCallback((busy: boolean) => updatePending(4, busy), [updatePending]);
  useEffect(() => { if (instance.data) setLastInstance(instance.data); }, [instance.data]);
  const currentInstance = instance.data ?? lastInstance;
  useNavigationGuard(target => {
    const url = new URL(target, window.location.origin);
    if (url.pathname === '/configuration-wizard' && url.searchParams.get('instance') === instanceId) return true;
    if (!Object.values(pendingRef.current).some(Boolean)) return true;
    return window.confirm(tr('离开将结束本次编辑。已保存的用户、组织、模板和集群配置会保留，未保存的身份编辑将丢弃。确认离开？', 'Leave this editing session? Saved identities, templates and cluster settings remain. Unsaved identity edits will be discarded.'));
  });

  useEffect(() => { setVisited(value => new Set([...value, stage])); }, [stage]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (Object.values(pendingRef.current).some(Boolean)) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, []);

  function go(next: GuidedClusterStage) {
    setVisited(value => new Set([...value, next]));
    navigate(guidedClusterWizardPath(instanceId, next, params), { replace: true });
  }
  function saved(source: GuidedClusterStage, next: GuidedClusterStage) {
    if (!mounted.current) return;
    updatePending(source, false);
    setRevision(value => value + 1);
    void instance.reload();
    // A slow save must not pull the user away from a different step they opened meanwhile.
    if (stageRef.current === source) go(next);
  }
  function leave() {
    navigate('/instances/' + encodeURIComponent(instanceId) + '/cluster-config');
  }
  const steps = [
    [tr('连接已有服务', 'Connect services'), tr('确认命名空间与服务地址', 'Namespace and service endpoints')],
    [tr('集群准入', 'Cluster admission'), tr('配置用户、组织与进入权限', 'Users, organizations and entry access')],
    [tr('运行资源', 'Runtime resources'), tr('选择或定制运行时模板', 'Select or customize a runtime template')],
    [tr('Agent 绑定', 'Agent bindings'), tr('配置能力并绑定使用范围', 'Capabilities and access scope')],
    [tr('检查并验证', 'Check and verify'), tr('连接、权限与真实对话', 'Connectivity, access and a real conversation')],
  ];

  return <div className="wizard-workspace guided-cluster">
    <header className="page-header wizard-header">
      <div><button type="button" className="btn ghost sm" onClick={leave}>← {tr('配置工作台', 'Configuration workspace')}</button><h1 className="page-title mt-3">{tr('配置集群', 'Configure cluster')} · {currentInstance?.jiuwenclaw_name || instanceId}</h1></div>
      <span className="tag">{tr(`第 ${stage} / 5 步`, `Step ${stage} of 5`)}</span>
    </header>
    <div className="wizard-layout">
      <aside className="card wizard-steps">
        <h2 className="text-sm font-semibold">{tr('集群配置流程', 'Cluster setup flow')}</h2>
        <ol className="mt-3 flex flex-col gap-1" aria-label={tr('集群配置流程', 'Cluster setup flow')}>{steps.map(([title, description], index) => <li key={index}><button type="button" className={'wizard-step ' + (stage === index + 1 ? 'current' : '')} aria-current={stage === index + 1 ? 'step' : undefined} onClick={() => go(index + 1 as GuidedClusterStage)}><span className="wizard-step__num">{index + 1}</span><span><strong>{title}</strong><small>{description}</small>{pending[index + 1] && <small className="text-accent">{tr('有未保存配置', 'Unsaved settings')}</small>}</span></button></li>)}</ol>
      </aside>
      <main className="wizard-main">
        {instance.loading && !currentInstance ? <section className="card wizard-card" role="status">{tr('正在读取集群…', 'Loading cluster…')}</section> : !currentInstance ? <section className="card wizard-card"><p role="alert" className="text-danger">{instance.error || tr('集群不存在或无权访问。', 'Cluster unavailable.')}</p><button className="btn mt-3" onClick={() => void instance.reload()}>{tr('重试', 'Retry')}</button></section> : <>
          {instance.error && <div className="card mb-4" role="alert"><p className="text-danger">{tr('刷新集群信息失败，当前编辑草稿已保留：', 'Failed to refresh the cluster; your editing draft is retained: ')}{instance.error}</p><button type="button" className="btn sm mt-2" onClick={() => void instance.reload()}>{tr('重新读取', 'Retry')}</button></div>}
          {visited.has(1) && <div hidden={stage !== 1}><WizardConnectionStep instance={currentInstance} onSaved={() => saved(1, 2)} onBusyChange={pending1} /></div>}
          {visited.has(2) && <div hidden={stage !== 2}><WizardAdmissionStep instanceId={instanceId} active={stage === 2} revision={revision} onSaved={() => saved(2, 3)} onDeferred={() => go(3)} onBusyChange={pending2} /></div>}
          {visited.has(3) && <div hidden={stage !== 3}><WizardRuntimeStep instanceId={instanceId} active={stage === 3} revision={revision} onSaved={() => saved(3, 4)} onBusyChange={pending3} /></div>}
          {visited.has(4) && <div hidden={stage !== 4}><WizardAgentStep instanceId={instanceId} active={stage === 4} revision={revision} initialTemplateId={params.get('template') || params.get('templateId') || undefined} onSaved={() => saved(4, 5)} onBusyChange={pending4} /></div>}
          {stage === 5 && <WizardVerificationStep instanceId={instanceId} revision={revision} onStage={go} onFinish={leave} />}
          {stage > 1 && <div className="guided-cluster__back"><button type="button" className="btn ghost sm" onClick={() => go(stage - 1 as GuidedClusterStage)}>← {tr('上一步', 'Previous step')}</button><span>{tr('可切换步骤继续编辑，完成后再保存。', 'Switch steps to edit, then save your changes.')}</span></div>}
        </>}
      </main>
    </div>
  </div>;
}
