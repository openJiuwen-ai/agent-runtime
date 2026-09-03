import { useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, createContext } from 'react';
import { useTranslation } from 'react-i18next';
import {
  MATCH_FIELDS,
  canAddSubgroup,
  newCondNode,
  newDefaultRoot,
  newGroupNode,
  parseMatchExpr,
  removeNode,
  serializeMatchExpr,
  updateNode,
  type MatchCombineOp,
  type MatchCondNode,
  type MatchExprMode,
  type MatchExprModel,
  type MatchField,
  type MatchGroupNode,
  type MatchNode,
  type MatchOp,
} from '../utils/matchExpr';
import { AgentTemplateApi, OrgApi, UserApi } from '../services/api';
import { useAsync } from '../hooks/useAsync';

interface SelectOption { id: string; label: string }

const MatchFieldsContext = createContext<MatchField[]>(MATCH_FIELDS);

function useFieldOptions(field: MatchField): SelectOption[] {
  const allowedFields = useContext(MatchFieldsContext);
  const { data: orgs } = useAsync(() => OrgApi.list(), []);
  const { data: users } = useAsync(() => UserApi.list(), []);
  const loadAgents = allowedFields.includes('bot_id');
  const { data: agents } = useAsync(
    () => (loadAgents ? AgentTemplateApi.list() : Promise.resolve({ items: [] as { template_id: string; template_name?: string }[] })),
    [loadAgents],
  );

  return useMemo(() => {
    switch (field) {
      case 'group_id':
        return (orgs?.items ?? []).map((o) => ({ id: o.group_id, label: o.name || o.group_id }));
      case 'user_id':
        return (users?.items ?? []).map((u) => ({ id: u.user_id, label: u.display_name || u.user_id }));
      case 'bot_id':
        return (agents?.items ?? []).map((a) => ({ id: a.template_id, label: a.template_name || a.template_id }));
    }
  }, [field, orgs, users, agents]);
}

function TrashIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
      />
    </svg>
  );
}

function DeleteIconButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      className="btn sm ghost shrink-0 !border-transparent !px-2 !py-2 text-muted hover:!bg-[var(--bg-hover)] hover:!text-[var(--text)]"
      onClick={onClick}
      aria-label={label}
      title={label}
    >
      <TrashIcon />
    </button>
  );
}

function OpNode({
  value,
  onChange,
  onRemove,
  showRemove,
  anchorRef,
}: {
  value: MatchCombineOp;
  onChange: (op: MatchCombineOp) => void;
  onRemove?: () => void;
  showRemove?: boolean;
  anchorRef?: React.Ref<HTMLDivElement>;
}) {
  const { t } = useTranslation();
  return (
    <div
      ref={anchorRef}
      className="flex shrink-0 items-center gap-1 rounded-full border-2 border-[var(--primary)]/35 bg-[var(--card)] px-2.5 py-1.5 shadow-sm"
    >
      <select
        className="select !border-0 !bg-transparent !py-0 !pl-0 !pr-6 !text-xs font-semibold uppercase tracking-wide !shadow-none focus:!ring-0 min-w-[4.5rem]"
        value={value}
        onChange={(e) => onChange(e.target.value as MatchCombineOp)}
        aria-label={t('policies.matchExpr.combineOp')}
      >
        <option value="and">{t('policies.matchExpr.andLabel')}</option>
        <option value="or">{t('policies.matchExpr.orLabel')}</option>
      </select>
      {showRemove && onRemove ? (
        <DeleteIconButton label={t('policies.matchExpr.removeGroup')} onClick={onRemove} />
      ) : null}
    </div>
  );
}

function MultiSelectDropdown({
  options,
  selected,
  onChange,
  placeholder,
}: {
  options: SelectOption[];
  selected: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase();
    return kw ? options.filter((o) => o.label.toLowerCase().includes(kw) || o.id.toLowerCase().includes(kw)) : options;
  }, [options, q]);

  const toggle = (id: string) => {
    onChange(selected.includes(id) ? selected.filter((v) => v !== id) : [...selected, id]);
  };

  const selectedLabels = selected
    .map((id) => options.find((o) => o.id === id)?.label ?? id)
    .join(', ');

  return (
    <div ref={ref} className="relative flex-1 min-w-[8rem]">
      <button
        type="button"
        className="input w-full !py-1 text-xs text-left truncate"
        onClick={() => setOpen(!open)}
      >
        {selectedLabels || <span className="text-muted">{placeholder}</span>}
      </button>
      {open && (
        <div className="absolute left-0 top-full mt-1 z-[9999] w-full min-w-[12rem] max-h-[200px] overflow-auto rounded border border-[var(--border)] bg-[var(--card)] shadow-lg">
          <input
            className="input w-full !border-0 !border-b !rounded-none !text-xs"
            placeholder="搜索..."
            value={q}
            onChange={(e) => setQ(e.target.value)}
            autoFocus
          />
          {filtered.map((o) => (
            <label key={o.id} className="flex items-center gap-2 px-2 py-1 hover:bg-[var(--bg-hover)] cursor-pointer text-xs">
              <input type="checkbox" checked={selected.includes(o.id)} onChange={() => toggle(o.id)} />
              <span className="truncate">{o.label}</span>
              <span className="text-muted mono text-[10px] ml-auto shrink-0">{o.id.length > 12 ? o.id.slice(0, 12) + '…' : o.id}</span>
            </label>
          ))}
          {filtered.length === 0 && <div className="px-2 py-2 text-xs text-muted">无匹配项</div>}
        </div>
      )}
    </div>
  );
}

function ConditionLeaf({
  value,
  onChange,
  onRemove,
  canRemove,
  onAddCondition,
}: {
  value: MatchCondNode;
  onChange: (next: MatchCondNode) => void;
  onRemove: () => void;
  canRemove: boolean;
  onAddCondition: () => void;
}) {
  const { t } = useTranslation();
  const allowedFields = useContext(MatchFieldsContext);
  const fieldOptions = useMemo(() => {
    // 已有非法字段时仍展示当前值，便于用户改成合法字段
    if (allowedFields.includes(value.field)) return allowedFields;
    return [...allowedFields, value.field];
  }, [allowedFields, value.field]);
  const options = useFieldOptions(value.field);
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--card)] px-2.5 py-2 shadow-[inset_0_1px_0_var(--card-highlight)]">
      <select
        className="select w-[5.5rem] shrink-0 !py-1 !text-xs"
        value={value.field}
        onChange={(e) => onChange({ ...value, field: e.target.value as MatchField, values: [] })}
      >
        {fieldOptions.map((f) => (
          <option key={f} value={f}>
            {t(`policies.matchExpr.fields.${f}`)}
          </option>
        ))}
      </select>
      <select
        className="select w-[5rem] shrink-0 !py-1 !text-xs"
        value={value.op}
        onChange={(e) => onChange({ ...value, op: e.target.value as MatchOp })}
      >
        <option value="in">in</option>
        <option value="not in">not in</option>
      </select>
      <MultiSelectDropdown
        options={options}
        selected={value.values}
        onChange={(values) => onChange({ ...value, values })}
        placeholder={t('policies.matchExpr.valuePlaceholder')}
      />
      <button type="button" className="btn sm ghost shrink-0 !px-2" onClick={onAddCondition}>
        + {t('policies.matchExpr.addCondition')}
      </button>
      {canRemove ? (
        <DeleteIconButton label={t('policies.matchExpr.removeCondition')} onClick={onRemove} />
      ) : null}
    </div>
  );
}

/** 最外层专用：文案为「添加条件」，实际添加括号子组 */
function RootAddSubgroupButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      className="btn sm ghost mt-2"
      onClick={onClick}
      title={t('policies.matchExpr.addSubgroupHint')}
    >
      + {t('policies.matchExpr.addCondition')}
    </button>
  );
}

/** 从 Op 节点到各子节点的平滑贝塞尔连线 */
function MindMapConnectors({
  opEl,
  childRefs,
  layoutKey,
}: {
  opEl: HTMLDivElement | null;
  childRefs: React.MutableRefObject<(HTMLDivElement | null)[]>;
  layoutKey: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [paths, setPaths] = useState<string[]>([]);
  const [size, setSize] = useState({ w: 0, h: 0 });

  const recalc = useCallback(() => {
    const draw = () => {
      const container = containerRef.current;
      if (!container || !opEl) {
        setPaths([]);
        return;
      }
      const cr = container.getBoundingClientRect();
      setSize({ w: cr.width, h: cr.height });
      const or = opEl.getBoundingClientRect();
      const sx = or.right - cr.left;
      const sy = or.top + or.height / 2 - cr.top;

      const next: string[] = [];
      for (const el of childRefs.current) {
        if (!el) continue;
        const r = el.getBoundingClientRect();
        const ex = r.left - cr.left;
        const ey = r.top + r.height / 2 - cr.top;
        const span = Math.max(ex - sx, 40);
        const cpx1 = sx + span * 0.55;
        const cpx2 = ex - span * 0.45;
        next.push(`M ${sx} ${sy} C ${cpx1} ${sy}, ${cpx2} ${ey}, ${ex} ${ey}`);
      }
      setPaths(next);
    };
    draw();
    requestAnimationFrame(draw);
  }, [opEl, childRefs]);

  useLayoutEffect(() => {
    recalc();
    const container = containerRef.current;
    if (!container) return;
    const ro = new ResizeObserver(() => recalc());
    ro.observe(container);
    for (const el of childRefs.current) {
      if (el) ro.observe(el);
    }
    return () => ro.disconnect();
  }, [recalc, layoutKey]);

  if (paths.length === 0) {
    return <div ref={containerRef} className="pointer-events-none absolute inset-0" aria-hidden />;
  }

  return (
    <div ref={containerRef} className="pointer-events-none absolute inset-0" aria-hidden>
      <svg width={size.w} height={size.h} className="overflow-visible">
        {paths.map((d, i) => (
          <path
            key={i}
            d={d}
            fill="none"
            stroke="var(--border)"
            strokeWidth={1.75}
            strokeLinecap="round"
          />
        ))}
      </svg>
    </div>
  );
}

function MindMapGroup({
  group,
  root,
  depth,
  isRoot,
  layoutKey,
  onChangeRoot,
}: {
  group: MatchGroupNode;
  root: MatchGroupNode;
  depth: number;
  isRoot: boolean;
  layoutKey: string;
  onChangeRoot: (next: MatchGroupNode) => void;
}) {
  const [opEl, setOpEl] = useState<HTMLDivElement | null>(null);
  const childRefs = useRef<(HTMLDivElement | null)[]>([]);

  const bindOpRef = (el: HTMLDivElement | null) => {
    setOpEl(el);
  };

  const patchRoot = (updater: (r: MatchGroupNode) => MatchGroupNode) => {
    onChangeRoot(updater(root));
  };

  const updateGroup = (groupId: string, updater: (g: MatchGroupNode) => MatchGroupNode) => {
    patchRoot((r) =>
      updateNode(r, groupId, (n) => {
        if (n.kind !== 'group') return n;
        return updater(n);
      }) as MatchGroupNode,
    );
  };

  const updateCond = (condId: string, next: MatchCondNode) => {
    patchRoot((r) => updateNode(r, condId, () => next) as MatchGroupNode);
  };

  const removeItem = (nodeId: string) => {
    if (nodeId === root.id) return;
    patchRoot((r) => removeNode(r, nodeId));
  };

  const addConditionInside = (condId: string) => {
    if (canAddSubgroup(root, group.id)) {
      patchRoot(
        (r) =>
          updateNode(r, condId, (n) => {
            if (n.kind !== 'cond') return n;
            return newGroupNode('or', [n, newCondNode()]);
          }) as MatchGroupNode,
      );
      return;
    }
    updateGroup(group.id, (g) => ({
      ...g,
      children: [...g.children, newCondNode()],
    }));
  };

  const addSubgroup = () => {
    if (!canAddSubgroup(root, group.id)) return;
    updateGroup(group.id, (g) => ({
      ...g,
      children: [...g.children, newGroupNode('or', [newCondNode()])],
    }));
  };

  const multi = group.children.length > 1;
  const showRootAddSubgroup = isRoot && canAddSubgroup(root, group.id);

  childRefs.current = [];

  const shellClass = isRoot
    ? 'relative rounded-xl border border-[var(--border)] bg-[var(--bg-muted)]/80 p-4'
    : 'relative min-w-0 rounded-lg border border-[var(--border)]/70 bg-[var(--bg-muted)]/40 p-3';

  const rootFooter = showRootAddSubgroup ? <RootAddSubgroupButton onClick={addSubgroup} /> : null;

  const renderCondition = (child: MatchCondNode, index: number, canRemove: boolean, withConnector: boolean) => (
    <div
      ref={
        withConnector
          ? (el) => {
              childRefs.current[index] = el;
            }
          : undefined
      }
    >
      <ConditionLeaf
        value={child}
        canRemove={canRemove}
        onChange={(next) => updateCond(child.id, next)}
        onRemove={() => removeItem(child.id)}
        onAddCondition={() => addConditionInside(child.id)}
      />
    </div>
  );

  /** 仅一条条件：不展示 OR/AND；非根子组不再套额外外框 */
  if (!multi && group.children.length === 1 && group.children[0].kind === 'cond') {
    const child = group.children[0];
    const leaf = (
      <ConditionLeaf
        value={child}
        canRemove={!isRoot}
        onChange={(next) => updateCond(child.id, next)}
        onRemove={() => removeItem(child.id)}
        onAddCondition={() => addConditionInside(child.id)}
      />
    );
    if (isRoot) {
      return (
        <div className={shellClass}>
          {leaf}
          {rootFooter}
        </div>
      );
    }
    return leaf;
  }

  /** 仅一条子组：透传渲染，避免双层外框 */
  if (!multi && group.children.length === 1 && group.children[0].kind === 'group') {
    const child = group.children[0];
    const nested = (
      <MindMapGroup
        group={child}
        root={root}
        depth={depth + 1}
        isRoot={false}
        layoutKey={layoutKey}
        onChangeRoot={onChangeRoot}
      />
    );
    if (isRoot) {
      return (
        <div className={shellClass}>
          {nested}
          {rootFooter}
        </div>
      );
    }
    return nested;
  }

  return (
    <div className={shellClass}>
      <div className="relative flex min-w-0 items-stretch gap-4">
        <MindMapConnectors opEl={opEl} childRefs={childRefs} layoutKey={layoutKey} />

        <div className="flex shrink-0 flex-col justify-center self-center z-[1]">
          <OpNode
            anchorRef={bindOpRef}
            value={group.op}
            onChange={(op) => updateGroup(group.id, (g) => ({ ...g, op }))}
            showRemove={!isRoot}
            onRemove={() => removeItem(group.id)}
          />
        </div>

        <div className="relative z-[1] flex min-w-0 flex-1 flex-col gap-3 py-0.5">
          {group.children.map((child, index) => (
            <div key={child.id} className="min-w-0 pl-1">
              {child.kind === 'cond' ? (
                renderCondition(child, index, !isRoot || multi, true)
              ) : (
                <MindMapGroup
                  group={child}
                  root={root}
                  depth={depth + 1}
                  isRoot={false}
                  layoutKey={layoutKey}
                  onChangeRoot={onChangeRoot}
                />
              )}
            </div>
          ))}
        </div>
      </div>
      {rootFooter}
    </div>
  );
}

function layoutStructureKey(root: MatchGroupNode): string {
  const walk = (node: MatchNode): string => {
    if (node.kind === 'cond') return `c:${node.id}`;
    return `g:${node.id}:${node.children.map(walk).join('|')}`;
  };
  return walk(root);
}

interface MatchExprEditorProps {
  value: string;
  onChange: (value: string) => void;
  /** 可选字段；默认 group/user/bot。Agent 资源传 user/group 即可。 */
  allowedFields?: MatchField[];
}

export function MatchExprEditor({
  value,
  onChange,
  allowedFields = MATCH_FIELDS,
}: MatchExprEditorProps) {
  const { t } = useTranslation();
  const fields = useMemo(
    () => (allowedFields.length > 0 ? allowedFields : MATCH_FIELDS),
    [allowedFields],
  );
  const [model, setModel] = useState<MatchExprModel>(() => parseMatchExpr(value));
  const lastEmittedRef = useRef(value);

  useEffect(() => {
    if (value === lastEmittedRef.current) return;
    lastEmittedRef.current = value;
    setModel(parseMatchExpr(value));
  }, [value]);

  const preview = useMemo(() => serializeMatchExpr(model), [model]);
  const structureKey = useMemo(
    () => (model.mode === 'custom' && !model.raw ? layoutStructureKey(model.root) : ''),
    [model],
  );

  const emit = (next: MatchExprModel) => {
    setModel(next);
    const serialized = serializeMatchExpr(next);
    lastEmittedRef.current = serialized;
    onChange(serialized);
  };

  const setMode = (mode: MatchExprMode) => {
    if (mode === 'all') {
      emit({ mode: 'all', root: newDefaultRoot() });
      return;
    }
    emit({
      mode: 'custom',
      root: model.root.children.length ? model.root : newDefaultRoot(),
      raw: undefined,
    });
  };

  return (
    <MatchFieldsContext.Provider value={fields}>
      <div className="flex flex-col gap-2">
        <select
          className="select w-full max-w-xs"
          value={model.mode}
          onChange={(e) => setMode(e.target.value as MatchExprMode)}
        >
          <option value="all">{t('policies.matchExpr.modeAll')}</option>
          <option value="custom">{t('policies.matchExpr.modeCustom')}</option>
        </select>

        {model.mode === 'all' ? (
          <div className="text-[11px] text-muted">{t('policies.matchExpr.allHint')}</div>
        ) : model.raw ? (
          <div className="flex flex-col gap-2">
            <div className="text-[11px] text-muted">{t('policies.matchExpr.rawHint')}</div>
            <textarea
              className="input min-h-[72px] mono text-xs"
              value={model.raw}
              onChange={(e) => emit({ ...model, raw: e.target.value })}
            />
            <button
              type="button"
              className="btn sm ghost self-start"
              onClick={() => emit(parseMatchExpr(model.raw))}
            >
              {t('policies.matchExpr.resetToVisual')}
            </button>
          </div>
        ) : (
          <MindMapGroup
            group={model.root}
            root={model.root}
            depth={0}
            isRoot
            layoutKey={structureKey}
            onChangeRoot={(root) => emit({ ...model, mode: 'custom', root, raw: undefined })}
          />
        )}

        {model.mode === 'custom' && preview ? (
          <div className="rounded-md border border-dashed border-[var(--border)] bg-[var(--bg-muted)] px-2.5 py-2">
            <div className="text-[11px] text-muted mb-1">{t('policies.matchExpr.preview')}</div>
            <code className="block text-[11px] mono text-muted break-all">{preview}</code>
          </div>
        ) : null}
      </div>
    </MatchFieldsContext.Provider>
  );
}
