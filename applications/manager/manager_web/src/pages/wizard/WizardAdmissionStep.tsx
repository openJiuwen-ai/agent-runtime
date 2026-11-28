import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../auth/AuthContext';
import { ApiError, InstanceBindingApi, OrgApi, UserApi, type IamUser, type Org } from '../../services/api';
import { bumpGuideRevision } from '../../stores/guideStore';
import { collectPages } from '../../utils/overview';
import { isValidIdentityId, sanitizeIdentityIdInput } from '../../utils/identityId';
import { admissionKey, admissionTimestamp, grantDraft, hasActiveAdmission, hasAdmissionChanges, localAdmissionTime, sameAdmission, writeAdmissionChanges, writeOrganizationChanges, type AdmissionDraft, type AdmissionKind } from '../../utils/wizardAdmission';
import './WizardAdmissionStep.css';
import { WizardStepHeading } from './WizardStepHeading';

type Translate = (cn: string, en: string) => string;
type Editor = { kind: 'user'; value?: IamUser } | { kind: 'org'; value?: Org } | null;
const message = (error: unknown) => error instanceof ApiError ? error.detail : error instanceof Error ? error.message : String(error);
const toggleSet = (values: Set<string>, key: string) => {
  const next = new Set(values);
  next.has(key) ? next.delete(key) : next.add(key);
  return next;
};

export interface WizardAdmissionStepProps {
  instanceId: string;
  active?: boolean;
  revision?: number;
  onSaved: () => void;
  onDeferred?: () => void;
  onBusyChange?: (busy: boolean) => void;
}

/** Identity writes are global. Admission changes remain a local draft until this step is saved. */
export function WizardAdmissionStep({ instanceId, active = true, revision: savedRevision = 0, onSaved, onDeferred, onBusyChange }: WizardAdmissionStepProps) {
  const { i18n } = useTranslation();
  const { user: signedInUser } = useAuth();
  const tr: Translate = (cn, en) => i18n.language.startsWith('zh') ? cn : en;
  const [users, setUsers] = useState<IamUser[]>([]);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [baseline, setBaseline] = useState<AdmissionDraft[]>([]);
  const [draft, setDraft] = useState<AdmissionDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [selecting, setSelecting] = useState(false);
  const [tab, setTab] = useState<AdmissionKind>('user');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(new Set<string>());
  const [editor, setEditor] = useState<Editor>(null);
  const [expanded, setExpanded] = useState('');
  const [confirmDefer, setConfirmDefer] = useState(false);
  const savingRef = useRef(false);
  const refreshedRequest = useRef('');
  const onBusyRef = useRef(onBusyChange);
  onBusyRef.current = onBusyChange;
  const dirty = hasAdmissionChanges(baseline, draft);
  const currentUser = users.find(user => user.user_id === signedInUser?.user_id && user.status === 'active');
  const currentUserAdded = !!currentUser && draft.some(row => row.kind === 'user' && row.subjectId === currentUser.user_id);
  const usableAdmission = hasActiveAdmission(draft, new Set(users.filter(user => user.status === 'active').map(user => user.user_id)), new Set(orgs.filter(org => org.status === 'active').map(org => org.group_id)));

  // Resource scopes can add admission grants on the server. Re-entering this step
  // refreshes clean settings, while an in-progress identity or admission draft stays mounted.
  useEffect(() => { if (active) setRevision(value => value + 1); }, [active, savedRevision]);
  useEffect(() => {
    if (!active || dirty || busy || editor || selecting) return;
    const request = `${instanceId}:${revision}`;
    if (refreshedRequest.current === request) return;
    let cancelled = false;
    setLoading(true);
    setLoaded(false);
    setError('');
    void Promise.all([
      collectPages((page, page_size) => UserApi.list({ page, page_size })),
      collectPages((page, page_size) => OrgApi.list({ page, page_size })),
      InstanceBindingApi.listUsers(instanceId), InstanceBindingApi.listOrgs(instanceId),
    ]).then(([allUsers, allOrgs, userGrants, orgGrants]) => {
      if (cancelled) return;
      const rows = [...userGrants.items.map(row => grantDraft(row, 'user')), ...orgGrants.items.map(row => grantDraft(row, 'org'))];
      setUsers(allUsers); setOrgs(allOrgs); setBaseline(rows); setDraft(rows);
      setLoaded(true);
      refreshedRequest.current = request;
      setSelecting(false); setEditor(null); setSelected(new Set());
    }).catch(cause => { if (!cancelled) { refreshedRequest.current = request; setError(message(cause)); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [instanceId, active, revision, dirty, busy, editor, selecting]);

  useEffect(() => {
    // The shell uses this flag for a leave-page confirmation; step panels stay mounted.
    const editingIdentity = editor?.kind === 'org' || (editor?.kind === 'user' && !editor.value);
    onBusyRef.current?.(busy || editingIdentity || selecting || dirty);
  }, [busy, editor, selecting, dirty]);
  useEffect(() => () => onBusyRef.current?.(false), []);

  const upsertUser = (user: IamUser) => setUsers(rows => [...rows.filter(row => row.user_id !== user.user_id), user]);
  const upsertOrg = (org: Org) => setOrgs(rows => [...rows.filter(row => row.group_id !== org.group_id), org]);
  const label = (row: Pick<AdmissionDraft, 'kind' | 'subjectId'>) => row.kind === 'user'
    ? users.find(user => user.user_id === row.subjectId)?.display_name || row.subjectId
    : orgs.find(org => org.group_id === row.subjectId)?.display_name || row.subjectId;
  const update = (key: string, patch: Partial<AdmissionDraft>) => { setError(''); setDraft(rows => rows.map(row => admissionKey(row) === key ? { ...row, ...patch } : row)); };
  const save = async () => {
    if (savingRef.current || loading || !loaded) return;
    if (!usableAdmission) {
      setError(tr('请至少添加一条已启用、未过期且身份有效的“允许登录”规则；如暂不配置，可选择“稍后配置”。', 'Add at least one enabled, unexpired allow rule for an active identity, or choose Configure later.')); return;
    }
    for (const row of draft) {
      const previous = baseline.find(item => admissionKey(item) === admissionKey(row));
      if (previous && sameAdmission(previous, row)) continue;
      if (row.expires_at && (!Number.isFinite(admissionTimestamp(row.expires_at)) || admissionTimestamp(row.expires_at) <= Date.now())) {
        setError(tr('请为准入对象设置未来的过期时间，或清空时间以永久生效：', 'Set a future expiry or clear it for permanent access: ') + label(row)); return;
      }
    }
    savingRef.current = true; setBusy(true); setError('');
    try {
      const result = await writeAdmissionChanges(baseline, draft, {
        bind: (kind, id, options) => kind === 'user' ? InstanceBindingApi.bindUsers(instanceId, [id], options) : InstanceBindingApi.bindOrgs(instanceId, [id], options),
        update: (kind, id, options) => kind === 'user' ? InstanceBindingApi.updateUserGrant(instanceId, id, options) : InstanceBindingApi.updateOrgGrant(instanceId, id, options),
        remove: (kind, id) => kind === 'user' ? InstanceBindingApi.unbindUsers(instanceId, [id]) : InstanceBindingApi.unbindOrgs(instanceId, [id]),
      });
      setBaseline(result.saved);
      bumpGuideRevision();
      if (result.errors.length) {
        setError(tr('部分配置未保存，已成功的操作会保留。请重试：', 'Some changes could not be saved. Successful writes are retained. Retry: ') + result.errors.map(item => `${label(item.row)}: ${message(item.error)}`).join('; '));
        return;
      }
      onSaved();
    } catch (cause) { setError(message(cause)); }
    finally { savingRef.current = false; setBusy(false); }
  };

  const selectionRows = tab === 'user'
    ? users.map(user => ({ id: user.user_id, title: user.display_name, sub: user.username || user.user_id, disabled: user.status !== 'active' }))
    : orgs.map(org => ({ id: org.group_id, title: org.display_name, sub: org.group_id, disabled: org.status !== 'active' }));
  const keyword = search.trim().toLowerCase();
  const visibleRows = selectionRows.filter(row => `${row.title} ${row.sub} ${row.id}`.toLowerCase().includes(keyword));
  const closeEditor = () => setEditor(null);

  const heading = <WizardStepHeading step={tr('第 2 步 · 集群准入', 'Step 2 · Cluster admission')} title={tr('配置集群准入', 'Configure cluster admission')} description={tr('指定哪些用户或组织可以登录当前集群。组织准入对其成员生效，禁止规则优先于允许规则。', 'Choose who may sign in to this cluster. Organization rules apply to members. Deny rules take precedence over allow rules.')} />;

  if (loading) return <>{heading}<section className="card wizard-identity"><p role="status">{tr('正在读取用户、组织和集群准入配置…', 'Loading users, organizations and cluster admission…')}</p></section></>;

  return <>
    {!editor && !selecting && heading}
    <section className="card wizard-identity" aria-label={tr('集群准入配置', 'Cluster admission configuration')}>
    {editor?.kind === 'user' ? <InlineUserEditor user={editor.value} tr={tr} onCancel={closeEditor} onBusy={setBusy} onSaved={user => {
      upsertUser(user); if (!editor.value) setSelected(values => new Set(values).add(admissionKey({ kind: 'user', subjectId: user.user_id })));
      setEditor(null);
    }} /> : editor?.kind === 'org' ? <InlineOrgEditor org={editor.value} users={users} tr={tr} onUserSaved={upsertUser} onOrgCreated={upsertOrg} onCancel={closeEditor} onBusy={setBusy} onSaved={org => {
      upsertOrg(org); if (!editor.value) setSelected(values => new Set(values).add(admissionKey({ kind: 'org', subjectId: org.group_id })));
      setEditor(null);
    }} /> : selecting ? <>
      <EditorHeading title={tr('选择用户或组织', 'Choose users or organizations')} description={tr('从统一身份目录添加准入对象，也可在这里创建用户或组织。', 'Add identities from the shared directory, or create a user or organization here.')} />
      <div className="wizard-identity__toolbar">
        <div className="tabs-bar" role="tablist" aria-label={tr('对象类型', 'Identity type')}>
          {(['user', 'org'] as const).map(kind => <button key={kind} type="button" role="tab" aria-selected={tab === kind} className={`tab ${tab === kind ? 'active' : ''}`} onClick={() => { setTab(kind); setSearch(''); }}>{kind === 'user' ? tr('用户', 'Users') : tr('组织', 'Organizations')}</button>)}
        </div>
        <div className="wizard-identity__catalog-actions"><input className="input" value={search} onChange={event => setSearch(event.target.value)} aria-label={tr('搜索用户或组织', 'Search users or organizations')} placeholder={tr('搜索名称或标识', 'Search name or ID')} /><button type="button" className="btn sm" onClick={() => setEditor(tab === 'user' ? { kind: 'user' } : { kind: 'org' })}>+ {tab === 'user' ? tr('新建用户', 'New user') : tr('新建组织', 'New organization')}</button></div>
      </div>
      <div className="wizard-identity__catalog">
        {!visibleRows.length && <p className="text-muted">{tr('暂无匹配的对象，可点击上方按钮新建。', 'No matching identities. Create one using the button above.')}</p>}
        {visibleRows.map(row => {
          const key = admissionKey({ kind: tab, subjectId: row.id });
          const existing = draft.some(item => admissionKey(item) === key);
          return <label className="wizard-identity__choice" key={key}>
            <input type="checkbox" checked={existing || selected.has(key)} disabled={row.disabled || existing} onChange={() => setSelected(values => toggleSet(values, key))} />
            <span><strong>{row.title || row.id}</strong><small className="mono">{row.sub}</small></span>
            <small className="text-muted">{row.disabled ? tr('已停用', 'Disabled') : existing ? tr('已添加', 'Added') : ''}</small>
          </label>;
        })}
      </div>
      <p className="wizard-identity__note">{tr('新建身份会保存到全局目录；此处选择不会立即授予集群准入。', 'New identities are saved globally. Selecting them does not grant cluster admission yet.')}</p>
      <div className="wizard-identity__footer"><button type="button" className="btn" onClick={() => setSelecting(false)}>{tr('返回准入列表', 'Back to admission')}</button><button type="button" className="btn primary" onClick={() => {
        setDraft(rows => {
          const keys = new Set(rows.map(admissionKey));
          const additions: AdmissionDraft[] = [];
          for (const key of selected) {
            const [kind, subjectId] = JSON.parse(key) as [AdmissionKind, string];
            const active = kind === 'user' ? users.some(user => user.user_id === subjectId && user.status === 'active') : orgs.some(org => org.group_id === subjectId && org.status === 'active');
            if (!keys.has(key) && active) additions.push({ kind, subjectId, login_policy: 'allow', enabled: true, expires_at: null });
          }
          return [...rows, ...additions];
        }); setSelected(new Set()); setSelecting(false); setError('');
      }}>{tr('添加到准入列表', 'Add to admission list')} {selected.size > 0 ? `(${selected.size})` : ''}</button></div>
    </> : <>
      <div className="wizard-identity__toolbar"><strong>{tr('准入对象', 'Admission identities')} · {draft.length}{dirty && <span className="tag ml-2">{tr('有未保存修改', 'Unsaved changes')}</span>}</strong><div className="wizard-identity__row-actions">{currentUser && !currentUserAdded && <button type="button" className="btn sm" disabled={busy || !loaded} onClick={() => { setDraft(rows => [...rows, { kind: 'user', subjectId: currentUser.user_id, login_policy: 'allow', enabled: true, expires_at: null }]); setError(''); }}>{tr('添加当前账号', 'Add my account')}</button>}<button type="button" className="btn primary sm" disabled={busy || !loaded} onClick={() => { setSelecting(true); setSelected(new Set()); }}>+ {tr('选择用户或组织', 'Choose users or organizations')}</button></div></div>
      {error && <div role="alert" className="wizard-identity__error">{error}{!loaded && <button type="button" className="btn sm" onClick={() => setRevision(value => value + 1)}>{tr('重新读取', 'Reload')}</button>}</div>}
      {!draft.length ? <div className="wizard-identity__empty">{tr('尚未添加准入对象。可先选择已有身份，或新建用户与组织。', 'No admission identities yet. Select existing identities, or create users and organizations.')}</div> : <div className="wizard-identity__rows">{draft.map(row => {
        const key = admissionKey(row);
        const user = row.kind === 'user' ? users.find(item => item.user_id === row.subjectId) : undefined;
        const org = row.kind === 'org' ? orgs.find(item => item.group_id === row.subjectId) : undefined;
        const identityDisabled = row.kind === 'user' ? user?.status !== 'active' : org?.status !== 'active';
        const expired = !!row.expires_at && admissionTimestamp(row.expires_at) <= Date.now();
        return <article className="wizard-identity__grant" key={key}>
          <div className="wizard-identity__grant-heading"><div><strong>{label(row)}</strong><span className="tag">{row.kind === 'user' ? tr('用户', 'User') : tr('组织', 'Organization')}</span><small className="mono text-muted">{row.subjectId}</small></div><div className="wizard-identity__row-actions">
            <span className={`tag ${row.login_policy === 'deny' ? 'danger' : ''}`}>{!row.enabled ? tr('准入已停用', 'Rule disabled') : expired ? tr('已过期', 'Expired') : row.login_policy === 'deny' ? tr('禁止登录', 'Deny sign-in') : tr('允许登录', 'Allow sign-in')}</span>
            <button type="button" className="btn sm" disabled={busy} aria-expanded={expanded === key} onClick={() => setExpanded(expanded === key ? '' : key)}>{tr('准入设置', 'Admission settings')}</button>
            <button type="button" className="btn sm" disabled={busy || (!user && !org)} onClick={() => setEditor(row.kind === 'user' ? { kind: 'user', value: user } : { kind: 'org', value: org })}>{row.kind === 'user' ? tr('用户资料', 'User details') : tr('成员与资料', 'Members and details')}</button>
            <button type="button" className="btn ghost sm text-danger" disabled={busy} onClick={() => { setDraft(rows => rows.filter(item => admissionKey(item) !== key)); setError(''); }}>{tr('移除', 'Remove')}</button>
          </div></div>
          {identityDisabled && <p className="wizard-identity__note">{tr('身份已停用或不在当前目录中，允许规则不能使其恢复登录。', 'This identity is disabled or unavailable. An allow rule cannot restore sign-in.')}</p>}
          {expanded === key && <div className="wizard-identity__settings">
            <label className="label">{tr('登录权限', 'Sign-in policy')}<select className="select" value={row.login_policy} disabled={busy} onChange={event => update(key, { login_policy: event.target.value === 'deny' ? 'deny' : 'allow' })}><option value="allow">{tr('允许登录', 'Allow sign-in')}</option><option value="deny">{tr('禁止登录', 'Deny sign-in')}</option></select></label>
            <label className="label">{tr('过期时间（留空为永久）', 'Expires at (blank for permanent)')}<input className="input" type="datetime-local" value={localAdmissionTime(row.expires_at)} disabled={busy} onChange={event => update(key, { expires_at: event.target.value ? new Date(event.target.value).toISOString() : null })} /></label>
            <label className="wizard-identity__check"><input type="checkbox" checked={row.enabled} disabled={busy} onChange={event => update(key, { enabled: event.target.checked })} />{tr('启用此准入规则', 'Enable this rule')}</label>
          </div>}
        </article>;
      })}</div>}
      <p className="wizard-identity__note">{tr('移除仅取消当前集群准入，不删除用户或组织。集群准入与运行资源、Agent 的适用范围分别配置。', 'Removing a rule only revokes admission to this cluster. Users and organizations are retained. Runtime and Agent scopes are configured separately.')}</p>
      {!usableAdmission && loaded && <div className="wizard-identity__notice">{tr('尚无有效的允许登录规则。请明确选择允许进入的身份，系统不会自动为当前账号授权。', 'There is no active allow rule. Choose identities explicitly; your account is not granted access automatically.')}</div>}
      {confirmDefer && <div className="wizard-identity__notice" role="alert"><span>{tr('稍后配置不会提交当前准入草稿，也不会将本步标记为完成。用户在获得准入前无法进入集群。', 'Configuring later keeps this draft unsaved and leaves this step incomplete. Users need admission before entering the cluster.')}</span><button type="button" className="btn sm" onClick={() => setConfirmDefer(false)}>{tr('继续配置', 'Keep configuring')}</button><button type="button" className="btn sm" onClick={() => { setConfirmDefer(false); onDeferred?.(); }}>{tr('确认稍后配置 →', 'Configure later →')}</button></div>}
      <div className="wizard-identity__footer"><span className="text-muted text-sm">{tr('保存本步时才会提交准入变更', 'Admission changes are applied when you save this step')}</span><div className="wizard-identity__row-actions">{onDeferred && <button type="button" className="btn" disabled={busy || !loaded} onClick={() => setConfirmDefer(true)}>{tr('稍后配置 →', 'Configure later →')}</button>}<button type="button" className="btn primary" disabled={busy || !loaded} onClick={() => void save()}>{busy ? tr('正在保存…', 'Saving…') : tr('保存并配置运行资源 →', 'Save and configure runtime resources →')}</button></div></div>
    </>}
    </section>
  </>;
}

function EditorHeading({ title, description }: { title: string; description?: string }) {
  return <header className="wizard-identity__heading"><h2>{title}</h2>{description && <p>{description}</p>}</header>;
}

function CancelChanges({ dirty, busy, tr, onCancel, children }: { dirty: boolean; busy: boolean; tr: Translate; onCancel: () => void; children: ReactNode }) {
  const [confirm, setConfirm] = useState(false);
  return <>
    {confirm && <div className="wizard-identity__error" role="alert"><span>{tr('放弃尚未保存的编辑？已保存的全局身份不会删除。', 'Discard unsaved edits? Globally saved identities will be retained.')}</span><button type="button" className="btn sm" onClick={() => setConfirm(false)}>{tr('继续编辑', 'Keep editing')}</button><button type="button" className="btn sm danger" onClick={onCancel}>{tr('放弃编辑', 'Discard edits')}</button></div>}
    <div className="wizard-identity__footer"><button type="button" className="btn" disabled={busy} onClick={() => dirty ? setConfirm(true) : onCancel()}>{tr('取消并返回', 'Cancel and return')}</button>{children}</div>
  </>;
}

function InlineUserEditor({ user, tr, onSaved, onCancel, onBusy }: { user?: IamUser; tr: Translate; onSaved: (user: IamUser) => void; onCancel: () => void; onBusy: (value: boolean) => void }) {
  if (user) return <UserDetails user={user} tr={tr} onBack={onCancel} />;
  return <CreateUserForm tr={tr} onSaved={onSaved} onCancel={onCancel} onBusy={onBusy} />;
}

function UserDetails({ user, tr, onBack }: { user: IamUser; tr: Translate; onBack: () => void }) {
  return <div className="wizard-identity__editor">
    <EditorHeading title={tr('用户资料', 'User details')} description={tr('仅展示已有用户的身份资料，不支持在此修改。集群准入请返回准入列表配置。', 'Existing user details are read-only. Return to the admission list to configure cluster access.')} />
    <dl className="wizard-identity__details">
      <div><dt>{tr('登录名', 'Username')}</dt><dd className="mono">{user.username || user.user_id}</dd></div>
      <div><dt>{tr('显示名称', 'Display name')}</dt><dd>{user.display_name || '—'}</dd></div>
      <div><dt>{tr('用户标识', 'User ID')}</dt><dd className="mono">{user.user_id}</dd></div>
      <div><dt>{tr('账号状态', 'Account status')}</dt><dd>{user.status === 'active' ? tr('启用', 'Active') : tr('停用', 'Disabled')}</dd></div>
      <div><dt>{tr('身份来源', 'Identity source')}</dt><dd>{user.identity_provider === 'federated' ? tr('联合身份', 'Federated identity') : user.identity_provider && user.identity_provider !== 'local' ? user.identity_provider : tr('本地账号', 'Local account')}</dd></div>
    </dl>
    <div className="wizard-identity__footer"><button type="button" className="btn" onClick={onBack}>{tr('返回准入列表', 'Back to admission')}</button></div>
  </div>;
}

function CreateUserForm({ tr, onSaved, onCancel, onBusy }: { tr: Translate; onSaved: (user: IamUser) => void; onCancel: () => void; onBusy: (value: boolean) => void }) {
  const [username, setUsername] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);
  const dirty = !!username || !!name || !!password;
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending.current) return;
    if (!name.trim() || !isValidIdentityId(username) || !password) {
      setError(tr('请填写显示名称、有效的登录名及初始密码。登录名只支持英文、数字、下划线和连字符。', 'Enter a display name, valid username and initial password. Usernames accept letters, digits, underscores and hyphens.')); return;
    }
    pending.current = true; setBusy(true); onBusy(true); setError('');
    try {
      const saved = await UserApi.create({ username: username.trim(), display_name: name.trim(), password });
      bumpGuideRevision(); onSaved(saved);
    } catch (cause) { setError(message(cause)); }
    finally { pending.current = false; setBusy(false); onBusy(false); }
  };
  return <form className="wizard-identity__editor" onSubmit={event => void save(event)}>
    <EditorHeading title={tr('新建用户', 'Create user')} description={tr('用户属于全局身份目录。保存资料不会立即授予当前集群准入，也不会授予管理员角色。', 'Users belong to the global identity directory. Saving details grants neither cluster admission nor an administrator role.')} />
    <fieldset disabled={busy} className="wizard-identity__fieldset">
      <div className="wizard-identity__form-grid">
        <label className="label">{tr('登录名', 'Username')} *<input className="input mono" value={username} required maxLength={64} autoComplete="off" onChange={event => setUsername(sanitizeIdentityIdInput(event.target.value))} /><small className="wizard-field-help">{tr('仅支持英文、数字、下划线和连字符', 'Letters, digits, underscores and hyphens only')}</small></label>
        <label className="label">{tr('显示名称', 'Display name')} *<input className="input" required maxLength={128} value={name} onChange={event => setName(event.target.value)} /></label>
        <label className="label">{tr('初始密码', 'Initial password')} *<input className="input" type="password" required maxLength={256} value={password} autoComplete="new-password" onChange={event => setPassword(event.target.value)} /></label>
      </div>
    </fieldset>
    {error && <div className="wizard-identity__error" role="alert">{error}</div>}
    <CancelChanges dirty={dirty} busy={busy} tr={tr} onCancel={onCancel}><button type="submit" className="btn primary" disabled={busy}>{busy ? tr('正在保存…', 'Saving…') : tr('保存用户并返回', 'Save user and return')}</button></CancelChanges>
  </form>;
}

function InlineOrgEditor({ org, users, tr, onSaved, onCancel, onBusy, onUserSaved, onOrgCreated }: {
  org?: Org; users: IamUser[]; tr: Translate; onSaved: (org: Org) => void; onCancel: () => void; onBusy: (busy: boolean) => void;
  onUserSaved: (user: IamUser) => void; onOrgCreated: (org: Org) => void;
}) {
  const [savedOrg, setSavedOrg] = useState<Org | undefined>(org);
  const [groupId, setGroupId] = useState(org?.group_id || '');
  const [name, setName] = useState(org?.display_name || '');
  const [members, setMembers] = useState(new Set<string>());
  const [savedMembers, setSavedMembers] = useState(new Set<string>());
  const [section, setSection] = useState<'basic' | 'members'>('basic');
  const [adding, setAdding] = useState(false);
  const [newUser, setNewUser] = useState(false);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(!!org);
  const [loadError, setLoadError] = useState('');
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);
  useEffect(() => {
    if (!org) return;
    let cancelled = false;
    setLoading(true); setLoadError('');
    void OrgApi.listMembers(org.group_id).then(result => {
      if (!cancelled) { const ids = new Set(result.users.map(user => user.user_id)); setMembers(ids); setSavedMembers(ids); }
    }).catch(cause => { if (!cancelled) setLoadError(message(cause)); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [org, revision]);
  const dirty = name !== (savedOrg?.display_name || '') || (!savedOrg && !!groupId) || members.size !== savedMembers.size || [...members].some(id => !savedMembers.has(id));
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending.current || loading || loadError) return;
    if (!name.trim() || (!savedOrg && groupId.trim() && !isValidIdentityId(groupId))) { setError(tr('请填写组织名称；组织标识仅支持英文、数字、下划线和连字符。', 'Enter an organization name. Organization IDs accept letters, digits, underscores and hyphens.')); setSection('basic'); return; }
    pending.current = true; setBusy(true); onBusy(true); setError('');
    try {
      const result = await writeOrganizationChanges(savedOrg, { display_name: name.trim(), ...(groupId.trim() ? { group_id: groupId.trim() } : {}) }, [...savedMembers], [...members], {
        create: OrgApi.create,
        update: OrgApi.update,
        addMember: (id, userId) => OrgApi.addMembers(id, [userId]),
        removeMember: OrgApi.removeMember,
      });
      if (result.organization) { setSavedOrg(result.organization); setGroupId(result.organization.group_id); onOrgCreated(result.organization); }
      setSavedMembers(new Set(result.memberIds));
      if (result.error) throw result.error;
      if (!result.organization) throw new Error(tr('未能保存组织。', 'The organization could not be saved.'));
      bumpGuideRevision(); onSaved(result.organization);
    } catch (cause) {
      setError(tr('组织或部分成员变更可能已保存。草稿已保留，可重试剩余操作：', 'Organization or some membership changes may already be saved. Your draft is retained; retry the remaining operations: ') + message(cause));
    } finally { pending.current = false; setBusy(false); onBusy(false); }
  };
  if (newUser) return <InlineUserEditor tr={tr} onBusy={onBusy} onCancel={() => setNewUser(false)} onSaved={user => { onUserSaved(user); setMembers(values => new Set(values).add(user.user_id)); setNewUser(false); }} />;
  const candidates = users.filter(user => `${user.display_name} ${user.username || ''} ${user.user_id}`.toLowerCase().includes(search.trim().toLowerCase()));
  return <form className="wizard-identity__editor" onSubmit={event => void save(event)}>
    <EditorHeading title={org ? tr('组织成员与资料', 'Organization members and details') : tr('新建组织', 'Create organization')} description={tr('组织与成员关系属于全局身份配置；保存组织后返回准入列表，再保存本步才会提交集群准入。', 'Organizations and memberships are global identities. Save the organization, return to admission, then save this step to apply cluster access.')} />
    <div className="wizard-identity__notice">{tr('成员调整会影响该组织在其他集群的权限；已有会话可能需要重新登录。', 'Membership changes affect the organization in other clusters. Existing sessions may need to sign in again.')}</div>
    <div className="tabs-bar" role="tablist" aria-label={tr('组织编辑分区', 'Organization editor sections')}>
      <button type="button" className={`tab ${section === 'basic' ? 'active' : ''}`} role="tab" aria-selected={section === 'basic'} onClick={() => setSection('basic')}>{tr('基本资料', 'Basic details')}</button>
      <button type="button" className={`tab ${section === 'members' ? 'active' : ''}`} role="tab" aria-selected={section === 'members'} onClick={() => setSection('members')}>{tr('组织成员', 'Members')} · {members.size}</button>
    </div>
    {loadError && <div role="alert" className="wizard-identity__error">{loadError}<button type="button" className="btn sm" onClick={() => setRevision(value => value + 1)}>{tr('重试读取成员', 'Retry loading members')}</button></div>}
    <fieldset className="wizard-identity__fieldset" disabled={busy || loading || !!loadError}>
      {section === 'basic' ? <div className="wizard-identity__form-grid">
        <label className="label">{tr('组织标识', 'Organization ID')}<input className="input mono" maxLength={64} disabled={!!savedOrg} value={groupId} onChange={event => setGroupId(sanitizeIdentityIdInput(event.target.value))} placeholder={tr('可留空，自动生成', 'Leave blank to generate automatically')} /><small className="wizard-field-help">{savedOrg ? tr('已有组织的标识不可修改', 'An existing organization ID cannot be changed') : tr('仅支持英文、数字、下划线和连字符', 'Letters, digits, underscores and hyphens only')}</small></label>
        <label className="label">{tr('组织名称', 'Organization name')} *<input className="input" maxLength={128} required value={name} onChange={event => setName(event.target.value)} /></label>
      </div> : <>
        <div className="wizard-identity__toolbar"><span className="text-muted text-sm">{tr('组织成员可继承组织的集群准入规则', 'Members inherit organization admission rules')}</span><div className="wizard-identity__row-actions"><button type="button" className="btn sm" onClick={() => setAdding(value => !value)}>{adding ? tr('收起用户选择', 'Close user selector') : '+ ' + tr('添加已有用户', 'Add existing users')}</button><div className="wizard-identity__catalog-actions">{adding && <input className="input" aria-label={tr('搜索成员', 'Search members')} placeholder={tr('搜索用户名称或登录名', 'Search display name or username')} value={search} onChange={event => setSearch(event.target.value)} />}<button type="button" className="btn sm" onClick={() => setNewUser(true)}>+ {tr('新建用户', 'New user')}</button></div></div></div>
        {loading ? <p role="status">{tr('正在读取成员…', 'Loading members…')}</p> : <>
          {adding && <><div className="wizard-identity__catalog">{candidates.map(user => <label className="wizard-identity__choice" key={user.user_id}><input type="checkbox" disabled={user.status !== 'active' && !members.has(user.user_id)} checked={members.has(user.user_id)} onChange={() => setMembers(values => toggleSet(values, user.user_id))} /><span><strong>{user.display_name || user.user_id}</strong><small className="mono">{user.username || user.user_id}</small></span>{user.status !== 'active' && <small>{tr('已停用', 'Disabled')}</small>}</label>)}{!candidates.length && <p className="text-muted">{tr('暂无匹配用户', 'No matching users')}</p>}</div></>}
          {!members.size && <div className="wizard-identity__empty">{tr('暂无成员。添加已有用户，或在当前流程中新建用户。', 'No members yet. Add existing users or create a user here.')}</div>}
          {[...members].map(id => { const user = users.find(item => item.user_id === id); return <div className="wizard-identity__member" key={id}><span>{user?.display_name || id}<small className="mono text-muted">{user?.username || id}</small></span><button type="button" className="btn ghost sm text-danger" onClick={() => setMembers(values => { const next = new Set(values); next.delete(id); return next; })}>{tr('移除成员', 'Remove member')}</button></div>; })}
        </>}
      </>}
    </fieldset>
    {error && <div className="wizard-identity__error" role="alert">{error}</div>}
    <CancelChanges tr={tr} dirty={dirty} busy={busy} onCancel={onCancel}><button type="submit" className="btn primary" disabled={busy || loading || !!loadError}>{busy ? tr('正在保存…', 'Saving…') : tr('保存组织并返回', 'Save organization and return')}</button></CancelChanges>
  </form>;
}
