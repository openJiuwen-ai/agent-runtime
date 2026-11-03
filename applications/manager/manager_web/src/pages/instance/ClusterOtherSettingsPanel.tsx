import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../auth/AuthContext';
import { useRouter } from '../../router';
import { hasPermission, isPlatformAdmin, isWorkspaceQuotaEnabled } from '../../services/api';
import { isMockInstanceId } from '../../config/mockManager';
import { clusterSettingSections, type ClusterSettingSection } from '../../utils/clusterNavigation';
import { LogMaskingTab } from './instanceConfigPanel/LogMaskingTab';
import { LoggingTab } from './instanceConfigPanel/LoggingTab';
import { AuditLogTab } from './instanceConfigPanel/AuditLogTab';
import { CronPolicyTab } from './instanceConfigPanel/CronPolicyTab';
import { WorkspaceQuotaPanel } from './instanceQuotaPanel/WorkspaceQuotaPanel';
import { TeamQuotaPanel } from './instanceQuotaPanel/TeamQuotaPanel';
import './ClusterOtherSettingsPanel.css';

type Setting = { id: ClusterSettingSection; title: string; description: string; unavailable?: string; content?: ReactNode };
type SettingGroup = { id: string; title: string; description: string; items: Setting[] };

function ReadOnlyExample({ rows }: { rows: [string, string][] }) {
  return <dl className="cluster-settings-example">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

/** One mounted editor per section: navigating the directory never discards edits. */
export function ClusterOtherSettingsPanel({ instanceId }: { instanceId: string }) {
  const { i18n } = useTranslation();
  const { user } = useAuth();
  const { path, params, navigate } = useRouter();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const demo = isMockInstanceId(instanceId);
  const admin = isPlatformAdmin(user);
  const quotaRead = hasPermission(user, 'quota:read');
  const workspaceEnabled = isWorkspaceQuotaEnabled(user);
  const requested = params.get('setting');
  const initial = clusterSettingSections.includes(requested as ClusterSettingSection) ? requested as ClusterSettingSection : 'log-masking';
  const [active, setActive] = useState<ClusterSettingSection>(initial);
  const viewport = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const sections = useRef<Partial<Record<ClusterSettingSection, HTMLElement>>>({});
  const pendingAnchor = useRef<ClusterSettingSection | null>(requested ? initial : null);
  const emittedQuery = useRef<string | null | undefined>(undefined);
  const route = useRef({ path, params, navigate });
  route.current = { path, params, navigate };

  const writeAnchor = useCallback((id: ClusterSettingSection, replace: boolean) => {
    const current = route.current;
    if (current.params.get('setting') === id) return;
    const query = new URLSearchParams(current.params);
    query.set('setting', id);
    emittedQuery.current = id;
    current.navigate(`${current.path}?${query}`, { replace });
  }, []);

  const alignAnchor = useCallback((id: ClusterSettingSection) => {
    const container = viewport.current;
    const section = sections.current[id];
    if (!container || !section) return;
    const top = section.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop;
    container.scrollTo({ top: Math.max(0, top - 16), behavior: 'auto' });
  }, []);

  const locate = (id: ClusterSettingSection) => {
    pendingAnchor.current = id;
    setActive(id);
    alignAnchor(id);
    writeAnchor(id, false);
  };

  useEffect(() => {
    if (requested === emittedQuery.current) { emittedQuery.current = undefined; return; }
    if (!requested) {
      pendingAnchor.current = null;
      setActive('log-masking');
      viewport.current?.scrollTo({ top: 0, behavior: 'auto' });
      return;
    }
    const id = clusterSettingSections.includes(requested as ClusterSettingSection) ? requested as ClusterSettingSection : 'log-masking';
    pendingAnchor.current = id;
    setActive(id);
    alignAnchor(id);
  }, [requested, alignAnchor]);

  useEffect(() => {
    const container = viewport.current;
    const body = content.current;
    if (!container || !body) return;
    let frame = 0;
    const manualScroll = () => { pendingAnchor.current = null; };
    const onKey = (event: KeyboardEvent) => {
      if (event.target === container && ['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) manualScroll();
    };
    const onScroll = () => {
      if (pendingAnchor.current) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const top = container.getBoundingClientRect().top + 40;
        let current: ClusterSettingSection = 'log-masking';
        for (const id of clusterSettingSections) {
          if (sections.current[id] && sections.current[id]!.getBoundingClientRect().top <= top) current = id;
        }
        if (container.scrollTop + container.clientHeight >= container.scrollHeight - 4) current = 'business-audit';
        setActive(current);
        writeAnchor(current, true);
      });
    };
    const resize = new ResizeObserver(() => { if (pendingAnchor.current) alignAnchor(pendingAnchor.current); });
    resize.observe(body);
    container.addEventListener('wheel', manualScroll, { passive: true });
    container.addEventListener('touchstart', manualScroll, { passive: true });
    container.addEventListener('pointerdown', manualScroll);
    container.addEventListener('keydown', onKey);
    container.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      resize.disconnect(); cancelAnimationFrame(frame);
      container.removeEventListener('wheel', manualScroll);
      container.removeEventListener('touchstart', manualScroll);
      container.removeEventListener('pointerdown', manualScroll);
      container.removeEventListener('keydown', onKey);
      container.removeEventListener('scroll', onScroll);
    };
  }, [alignAnchor, writeAnchor]);

  const adminOnly = !admin ? tr('需要平台管理员权限', 'Platform administrator access required') : undefined;
  const quotaOnly = !quotaRead ? tr('需要配额读取权限', 'Quota read access required') : undefined;
  const comingSoon = tr('待开放', 'Coming soon');
  const groups: SettingGroup[] = [
    { id: 'logs', title: tr('日志配置', 'Logging'), description: tr('管理敏感信息处理、日志详细程度和结构化审计字段。', 'Manage masking, verbosity and structured audit fields.'), items: [
      { id: 'log-masking', title: tr('日志脱敏', 'Log masking'), description: tr('通过匹配规则隐藏日志中的敏感信息，可设置替换内容、指纹和优先级。', 'Mask sensitive data using matching rules, replacements, fingerprints and priorities.'), unavailable: adminOnly, content: demo ? <ReadOnlyExample rows={[[tr('示例规则', 'Example rule'), tr('手机号脱敏', 'Phone number masking')], [tr('替换效果', 'Replacement'), '138****1234'], [tr('优先级', 'Priority'), '10']]} /> : admin && <LogMaskingTab instanceId={instanceId} /> },
      { id: 'logging', title: tr('日志级别', 'Log levels'), description: tr('分别设置默认、控制台和各组件日志级别，按需调整排障信息量。', 'Set default, console and component log levels for troubleshooting.'), unavailable: adminOnly, content: demo ? <ReadOnlyExample rows={[[tr('默认级别', 'Default'), 'INFO'], ['Gateway / Channel', 'INFO'], ['AgentServer / Full', 'INFO']]} /> : admin && <LoggingTab instanceId={instanceId} /> },
      { id: 'audit-log', title: tr('高级审计日志', 'Advanced audit logging'), description: tr('配置结构化安全审计日志的字段与运行标识；日志查询在业务审计中另行提供。', 'Configure structured security audit fields and runtime identifiers. Event search belongs to business audit.'), unavailable: adminOnly, content: demo ? <ReadOnlyExample rows={[[tr('运行标识', 'Runtime identifiers'), 'data_center · system_code · node'], [tr('日志字段', 'Log fields'), 'header · content · required'], [tr('采集配置', 'Telemetry'), tr('由部署环境管理', 'Managed by deployment')]]} /> : admin && <AuditLogTab instanceId={instanceId} /> },
    ] },
    { id: 'quotas', title: tr('配额管理', 'Quotas'), description: tr('按使用对象限制资源用量，策略和用量在同一配置区内查看。', 'Set usage limits by scope and inspect policies and usage in place.'), items: [
      { id: 'cron-quota', title: tr('定时任务配额', 'Scheduled task quota'), description: tr('限制每位用户在 Web 面板中启用且未过期的任务数量；0 表示不允许继续启用新任务。', 'Limit active, unexpired tasks per user in the Web panel; 0 blocks enabling additional tasks.'), unavailable: adminOnly, content: demo ? <ReadOnlyExample rows={[[tr('每用户任务上限', 'Task limit per user'), '5'], [tr('作用范围', 'Scope'), tr('Web 面板启用且未过期任务', 'Active, unexpired Web panel tasks')]]} /> : admin && <CronPolicyTab instanceId={instanceId} /> },
      { id: 'workspace-quota', title: tr('用户空间配额', 'Workspace quotas'), description: tr('按用户、组织或 Agent 范围设置空间容量、提醒阈值和使用上限。', 'Set capacity, warning thresholds and limits by user, organization or Agent scope.'), unavailable: quotaOnly || (!workspaceEnabled ? tr('用户空间配额未启用', 'Workspace quotas are disabled') : undefined), content: demo ? <ReadOnlyExample rows={[[tr('策略范围', 'Scope'), tr('已准入用户', 'Admitted users')], [tr('示例容量', 'Example capacity'), '10 GiB'], [tr('提醒 / 限制阈值', 'Soft / hard threshold'), '80% / 100%']]} /> : quotaRead && workspaceEnabled && <WorkspaceQuotaPanel instanceId={instanceId} /> },
      { id: 'team-quota', title: tr('团队配额', 'Team quotas'), description: tr('设置每用户、每组织的团队数量上限，以及每个团队的成员规模。', 'Limit teams per user or organization and members per team.'), unavailable: quotaOnly, content: demo ? <ReadOnlyExample rows={[[tr('每用户团队数', 'Teams per user'), '5'], [tr('每团队成员数', 'Members per team'), '20'], [tr('每组织团队数', 'Teams per organization'), '10']]} /> : quotaRead && <TeamQuotaPanel instanceId={instanceId} /> },
      { id: 'token-quota', title: tr('Token配额', 'Token quotas'), description: tr('用于设置模型 Token 使用额度，当前尚未接入可读写接口。', 'Model token allowances. A read/write service is not available yet.'), unavailable: comingSoon },
    ] },
    { id: 'cost', title: tr('成本统计', 'Cost statistics'), description: tr('查看集群的资源消耗与成本。', 'Review cluster resource consumption and costs.'), items: [
      { id: 'cost', title: tr('成本统计', 'Cost statistics'), description: tr('待统计服务接入后提供成本与用量分析，当前不展示实际金额。', 'Cost and usage analysis will appear when the service is available. No actual costs are displayed.'), unavailable: comingSoon },
    ] },
    { id: 'business-audit', title: tr('业务审计', 'Business audit'), description: tr('追溯集群中的业务操作与事件。', 'Trace business operations and events in this cluster.'), items: [
      { id: 'business-audit', title: tr('业务审计', 'Business audit'), description: tr('审计事件查询服务尚未接入；高级审计日志中的字段配置可独立使用。', 'Audit event search is not connected yet. Advanced audit log configuration remains available separately.'), unavailable: comingSoon },
    ] },
  ];

  return <section className="cluster-settings" aria-label={tr('其他配置', 'Other settings')}>
    <div className="page-header"><div><h2 className="page-title text-base">{tr('其他配置', 'Other settings')}</h2><p className="page-subtitle">{tr('按目录快速定位，每项配置独立保存。', 'Jump to a section. Save each setting independently.')}</p></div>{demo && <span className="tag">{tr('只读演示', 'Read-only demo')}</span>}</div>
    <div className="cluster-settings-layout">
      <nav className="cluster-settings-directory" aria-label={tr('其他配置目录', 'Settings directory')}>{groups.map(group => <div className="cluster-settings-directory__group" key={group.id}>{group.items.length > 1 && <h3>{group.title}</h3>}{group.items.map(item => <button key={item.id} className={`cluster-settings-directory__item ${active === item.id ? 'active' : ''}`} aria-current={active === item.id ? 'location' : undefined} aria-controls={`cluster-setting-${item.id}`} onClick={() => locate(item.id)}><span>{item.title}</span>{item.unavailable && <span className="cluster-settings-directory__status">{item.unavailable === comingSoon ? comingSoon : tr('受限', 'Restricted')}</span>}</button>)}</div>)}</nav>
      <div className="cluster-settings-scroll" ref={viewport} tabIndex={0} role="region" aria-label={tr('配置内容，可滚动', 'Scrollable settings')}>
        <div className="cluster-settings-content" ref={content}>{groups.map(group => <div className="cluster-settings-group" key={group.id}>
          {group.items.length > 1 && <header className="cluster-settings-group__header"><h3>{group.title}</h3><p>{group.description}</p></header>}
          {group.items.map(item => <section id={`cluster-setting-${item.id}`} key={item.id} ref={node => { if (node) sections.current[item.id] = node; }} className="cluster-setting-card" aria-labelledby={`cluster-setting-title-${item.id}`}>
            <header className="cluster-setting-card__header"><div><h4 id={`cluster-setting-title-${item.id}`}>{item.title}</h4><p>{item.description}</p></div><span className={`tag ${item.unavailable ? '' : 'cluster-setting-card__badge'}`}>{item.unavailable || (demo ? tr('只读示例', 'Read-only example') : tr('独立保存', 'Save independently'))}</span></header>
            <div className="cluster-setting-card__body">{item.unavailable ? <div className="cluster-settings-unavailable"><span aria-hidden="true">{item.unavailable === comingSoon ? '◷' : '⊘'}</span><div><strong>{item.unavailable}</strong><p>{item.unavailable === comingSoon ? tr('相关服务接入后，将在此开放相应功能。', 'This section will be available when the supporting service is connected.') : item.id === 'workspace-quota' && !workspaceEnabled ? tr('此集群环境尚未开启用户空间配额能力，团队配额可单独使用。', 'Workspace quotas are disabled for this environment. Team quotas remain independent.') : tr('请联系管理员确认当前账号的配置权限。', 'Ask an administrator about the required access.')}</p></div></div> : item.content}</div>
          </section>)}
        </div>)}</div>
      </div>
    </div>
  </section>;
}
