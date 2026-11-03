import { useTranslation } from 'react-i18next';
import { useRouter } from '../../router';
import { clusterPagePath, type ClusterWorkspaceSection } from '../../utils/clusterNavigation';

/** 集群详情中尚未接入数据接口的模块，仍提供完整的上下文和下一步入口。 */
export function InstancePlaceholderPanel({
  titleKey,
  subtitleKey,
  emptyKey = 'instanceDetail.placeholder.empty',
  instanceId,
}: {
  titleKey: string;
  subtitleKey: string;
  emptyKey?: string;
  instanceId?: string;
}) {
  const { t } = useTranslation();
  const { navigate, params } = useRouter();
  const title = t(titleKey);
  const subtitle = t(subtitleKey);
  const isAudit = titleKey.includes('.audit');
  const isCost = titleKey.includes('.cost');
  const isTokenQuota = titleKey.includes('.tokenQuota');
  const moduleLabel = isAudit ? '审计记录' : isCost ? '成本分析' : isTokenQuota ? 'Token 配额' : title;
  const moduleDescription = isAudit
    ? '记录集群配置、准入和资源变更，便于定位谁在什么时候做了什么。'
    : isCost
      ? '汇总模型调用和运行资源消耗，帮助团队建立可追踪的使用成本视图。'
      : isTokenQuota
        ? '限制成员或组织在当前集群中的 Token 使用量，避免资源被意外耗尽。'
        : subtitle;
  const backendState = '接口准备中';
  const go = (section: ClusterWorkspaceSection) => instanceId && navigate(clusterPagePath(instanceId, section, params));

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header items-start">
        <div>
          <div className="text-xs uppercase tracking-[0.14em] text-muted">集群工作台 · {moduleLabel}</div>
          <div className="page-title text-base mt-1">{title}</div>
          <div className="page-subtitle">{moduleDescription}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn sm" onClick={() => go('status')} disabled={!instanceId}>查看状态</button>
          <button type="button" className="btn primary sm" onClick={() => go('cluster-config')} disabled={!instanceId}>返回配置工作台</button>
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(16rem,0.7fr)]">
        <section className="card instance-placeholder-card">
          <div className="card-header">
            <div>
              <div className="card-title">{moduleLabel}</div>
              <p className="text-sm text-muted mt-1">{moduleDescription}</p>
            </div>
            <span className="tag">{backendState}</span>
          </div>
          <div className="instance-placeholder-state">
            <div className="instance-placeholder-state__icon" aria-hidden="true">{isAudit ? '◌' : isCost ? '↗' : '◫'}</div>
            <div>
              <h3>{t(emptyKey, { defaultValue: '当前暂无可展示数据' })}</h3>
              <p>当前版本尚未接入该模块的数据接口，暂不能读取或保存相关数据。完成其他配置不会自动生成这里的数据，不影响已支持的集群配置功能。</p>
            </div>
          </div>
        </section>
        <aside className="card instance-placeholder-side">
          <div className="card-title">你现在可以做什么</div>
          <p className="text-sm text-muted mt-1">按照集群配置依赖逐项完成，避免在空页面里反复寻找入口。</p>
          <div className="flex flex-col gap-2 mt-4">
            <button type="button" className="btn w-full justify-between" onClick={() => go('access')} disabled={!instanceId}>配置集群准入 <span>→</span></button>
            <button type="button" className="btn w-full justify-between" onClick={() => go('service-resources')} disabled={!instanceId}>配置运行资源 <span>→</span></button>
            <button type="button" className="btn w-full justify-between" onClick={() => go('agent-resources')} disabled={!instanceId}>绑定 Agent <span>→</span></button>
          </div>
        </aside>
      </div>
    </div>
  );
}
