import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Empty } from '../../../components/Empty';
import { Modal } from '../../../components/Modal';
import { JsonField } from '../../../components/JsonField';
import { toast } from '../../../stores/uiStore';
import { truncate } from '../../../utils/format';
import type {
  PermissionAction,
  PermissionMode,
  PermissionRuleEntry,
  PermissionSeverity,
  PermissionToolEntry,
  PermissionsFormState,
} from '../../../types';
import { stripExampleLabel } from './permissionsForm';

type SectionKey = 'general' | 'tools' | 'rules' | 'fileGuard';

const PERMISSION_ACTIONS: PermissionAction[] = ['allow', 'ask', 'deny'];
const PERMISSION_MODES: PermissionMode[] = ['normal', 'strict'];
const SEVERITIES: PermissionSeverity[] = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

function permissionActionLabel(action: PermissionAction, t: (key: string) => string): string {
  return t(`instanceConfig.permissions.actions.${action}`);
}

function permissionModeLabel(mode: PermissionMode, t: (key: string) => string): string {
  return t(`instanceConfig.permissions.modes.${mode}`);
}

function severityLabel(severity: PermissionSeverity, t: (key: string) => string): string {
  return t(`instanceConfig.permissions.severities.${severity}`);
}

function emptyToolRow(): PermissionToolEntry {
  return { key: `tool-${Date.now()}-${Math.random()}`, name: '', action: 'ask' };
}

function emptyRuleRow(): PermissionRuleEntry {
  return {
    key: `rule-${Date.now()}-${Math.random()}`,
    id: '',
    tools: [],
    pattern: '',
    severity: 'LOW',
  };
}

function ruleConfigSummary(row: PermissionRuleEntry, t: (key: string) => string): string {
  const parts: string[] = [];
  if (row.pattern) parts.push(`${t('instanceConfig.permissions.rulePattern')}: ${row.pattern}`);
  if (row.tools.length > 0) {
    parts.push(`${t('instanceConfig.permissions.ruleTools')}: ${row.tools.join(', ')}`);
  }
  parts.push(`${t('instanceConfig.permissions.ruleSeverity')}: ${severityLabel(row.severity, t)}`);
  return parts.join(' · ');
}

function parseToolsInput(text: string): string[] {
  return text
    .split(/[,，\n]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function validatePermissionsFormJson(
  form: PermissionsFormState,
  checkJson: (text: string) => string | null,
  t: (key: string) => string,
): string | null {
  const err = checkJson(stripExampleLabel(form.fileGuardJson));
  if (err) return `${t('instanceConfig.permissions.sections.fileGuard')}: ${err}`;
  return null;
}

interface Props {
  form: PermissionsFormState;
  onChange: (next: PermissionsFormState) => void;
}

export function PermissionsBodyEditor({ form, onChange }: Props) {
  const { t } = useTranslation();
  const [section, setSection] = useState<SectionKey>('general');

  const [toolModalOpen, setToolModalOpen] = useState(false);
  const [editingTool, setEditingTool] = useState<PermissionToolEntry | null>(null);
  const [toolForm, setToolForm] = useState<PermissionToolEntry>(emptyToolRow());

  const [ruleModalOpen, setRuleModalOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<PermissionRuleEntry | null>(null);
  const [ruleForm, setRuleForm] = useState<PermissionRuleEntry>(emptyRuleRow());
  const [ruleToolsText, setRuleToolsText] = useState('');

  const updateForm = <K extends keyof PermissionsFormState>(key: K, value: PermissionsFormState[K]) => {
    onChange({ ...form, [key]: value });
  };

  const openToolModal = (row?: PermissionToolEntry) => {
    if (row) {
      setEditingTool(row);
      setToolForm({ ...row });
    } else {
      setEditingTool(null);
      setToolForm(emptyToolRow());
    }
    setToolModalOpen(true);
  };

  const submitTool = () => {
    if (!toolForm.name.trim()) {
      toast('warn', t('instanceConfig.permissions.toolNameRequired'));
      return;
    }
    if (editingTool) {
      updateForm(
        'tools',
        form.tools.map((row) => (row.key === editingTool.key ? { ...toolForm, name: toolForm.name.trim() } : row))
      );
    } else {
      updateForm('tools', [...form.tools, { ...toolForm, name: toolForm.name.trim() }]);
    }
    setToolModalOpen(false);
  };

  const openRuleModal = (row?: PermissionRuleEntry) => {
    if (row) {
      setEditingRule(row);
      setRuleForm({ ...row, tools: [...row.tools] });
      setRuleToolsText(row.tools.join(', '));
    } else {
      setEditingRule(null);
      setRuleForm(emptyRuleRow());
      setRuleToolsText('');
    }
    setRuleModalOpen(true);
  };

  const submitRule = () => {
    if (!ruleForm.id.trim()) {
      toast('warn', t('instanceConfig.permissions.ruleIdRequired'));
      return;
    }
    if (!ruleForm.pattern.trim()) {
      toast('warn', t('instanceConfig.permissions.rulePatternRequired'));
      return;
    }
    const next: PermissionRuleEntry = {
      ...ruleForm,
      id: ruleForm.id.trim(),
      pattern: ruleForm.pattern.trim(),
      tools: parseToolsInput(ruleToolsText),
      severity: ruleForm.severity,
    };
    if (editingRule) {
      updateForm(
        'rules',
        form.rules.map((row) => (row.key === editingRule.key ? next : row))
      );
    } else {
      updateForm('rules', [...form.rules, next]);
    }
    setRuleModalOpen(false);
  };

  const sections: { key: SectionKey; label: string }[] = [
    { key: 'general', label: t('instanceConfig.permissions.sections.general') },
    { key: 'tools', label: t('instanceConfig.permissions.sections.tools') },
    { key: 'rules', label: t('instanceConfig.permissions.sections.rules') },
    { key: 'fileGuard', label: t('instanceConfig.permissions.sections.fileGuard') },
  ];

  return (
    <div className="flex flex-col gap-3">
      <div className="tabs-bar overflow-x-auto">
        {sections.map((it) => (
          <button
            key={it.key}
            type="button"
            onClick={() => setSection(it.key)}
            className={`tab ${section === it.key ? 'active' : ''}`}
          >
            {it.label}
          </button>
        ))}
      </div>

      {section === 'general' && (
        <div className="card grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="md:col-span-2">
            <label className="flex items-center gap-2 cursor-pointer border border-border rounded-md px-3 py-2 w-fit hover:bg-bg-hover">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => updateForm('enabled', e.target.checked)}
              />
              <span>{t('instanceConfig.permissions.enabled')}</span>
            </label>
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('instanceConfig.permissions.permissionMode')}</label>
            <div className="flex flex-wrap gap-2 mt-1">
              {PERMISSION_MODES.map((mode) => (
                <button
                  key={mode}
                  type="button"
                  className={`btn sm ${form.permissionMode === mode ? 'primary' : 'ghost'}`}
                  onClick={() => updateForm('permissionMode', mode)}
                >
                  {permissionModeLabel(mode, t)}
                </button>
              ))}
            </div>
            <p className="text-[11px] text-muted mt-1">{t('instanceConfig.permissions.permissionModeHint')}</p>
          </div>
          <div>
            <label className="label">{t('instanceConfig.permissions.defaults')}</label>
            <select
              className="select"
              value={form.defaults['*'] ?? 'allow'}
              onChange={(e) =>
                updateForm('defaults', {
                  ...form.defaults,
                  '*': e.target.value as PermissionAction,
                })
              }
            >
              {PERMISSION_ACTIONS.map((action) => (
                <option key={action} value={action}>
                  {permissionActionLabel(action, t)}
                </option>
              ))}
            </select>
            <p className="text-[11px] text-muted mt-1">{t('instanceConfig.permissions.defaultsHint')}</p>
          </div>
        </div>
      )}

      {section === 'tools' && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <button className="btn primary sm" type="button" onClick={() => openToolModal()}>
              + {t('instanceConfig.permissions.newTool')}
            </button>
            <span className="text-[11px] text-muted">{t('instanceConfig.permissions.toolsHint')}</span>
          </div>
          <div className="card !p-0">
            {form.tools.length === 0 ? (
              <Empty text={t('common.empty')} />
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('instanceConfig.permissions.toolName')}</th>
                    <th>{t('instanceConfig.permissions.action')}</th>
                    <th>{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {form.tools.map((row) => (
                    <tr key={row.key}>
                      <td className="mono text-sm">{row.name}</td>
                      <td>
                        <span className="tag">{permissionActionLabel(row.action, t)}</span>
                      </td>
                      <td>
                        <div className="flex items-center gap-1">
                          <button className="btn sm ghost" type="button" onClick={() => openToolModal(row)}>
                            {t('common.edit')}
                          </button>
                          <button
                            className="btn sm danger"
                            type="button"
                            onClick={() => updateForm('tools', form.tools.filter((it) => it.key !== row.key))}
                          >
                            {t('common.delete')}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {section === 'rules' && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <button className="btn primary sm" type="button" onClick={() => openRuleModal()}>
              + {t('instanceConfig.permissions.newRule')}
            </button>
            <span className="text-[11px] text-muted">{t('instanceConfig.permissions.rulesHint')}</span>
          </div>
          <div className="card !p-0">
            {form.rules.length === 0 ? (
              <Empty text={t('common.empty')} />
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('instanceConfig.permissions.ruleId')}</th>
                    <th>{t('instanceConfig.permissions.ruleConfig')}</th>
                    <th>{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {form.rules.map((row) => (
                    <tr key={row.key}>
                      <td className="mono text-xs whitespace-nowrap">{row.id}</td>
                      <td className="text-xs text-muted max-w-[28rem]" title={ruleConfigSummary(row, t)}>
                        {truncate(ruleConfigSummary(row, t), 96)}
                      </td>
                      <td>
                        <div className="flex items-center gap-1">
                          <button className="btn sm ghost" type="button" onClick={() => openRuleModal(row)}>
                            {t('common.edit')}
                          </button>
                          <button
                            className="btn sm danger"
                            type="button"
                            onClick={() => updateForm('rules', form.rules.filter((it) => it.key !== row.key))}
                          >
                            {t('common.delete')}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {section === 'fileGuard' && (
        <div className="card">
          <JsonField
            label={t('instanceConfig.permissions.fileGuardJson')}
            value={form.fileGuardJson}
            onChange={(v) => updateForm('fileGuardJson', v)}
            rows={16}
          />
          <p className="text-[11px] text-muted mt-2">{t('instanceConfig.permissions.fileGuardJsonHint')}</p>
        </div>
      )}

      <Modal
        open={toolModalOpen}
        title={editingTool ? t('instanceConfig.permissions.editTool') : t('instanceConfig.permissions.newTool')}
        onClose={() => setToolModalOpen(false)}
        footer={
          <>
            <button className="btn ghost" type="button" onClick={() => setToolModalOpen(false)}>
              {t('common.cancel')}
            </button>
            <button className="btn primary" type="button" onClick={submitTool}>
              {t('common.ok')}
            </button>
          </>
        }
      >
        <div className="grid grid-cols-1 gap-3">
          <div>
            <label className="label">{t('instanceConfig.permissions.toolName')}</label>
            <input
              className="input mono"
              value={toolForm.name}
              onChange={(e) => setToolForm((s) => ({ ...s, name: e.target.value }))}
              disabled={!!editingTool}
            />
          </div>
          <div>
            <label className="label">{t('instanceConfig.permissions.action')}</label>
            <select
              className="select"
              value={toolForm.action}
              onChange={(e) => setToolForm((s) => ({ ...s, action: e.target.value as PermissionAction }))}
            >
              {PERMISSION_ACTIONS.map((action) => (
                <option key={action} value={action}>
                  {permissionActionLabel(action, t)}
                </option>
              ))}
            </select>
          </div>
        </div>
      </Modal>

      <Modal
        open={ruleModalOpen}
        title={editingRule ? t('instanceConfig.permissions.editRule') : t('instanceConfig.permissions.newRule')}
        onClose={() => setRuleModalOpen(false)}
        size="lg"
        footer={
          <>
            <button className="btn ghost" type="button" onClick={() => setRuleModalOpen(false)}>
              {t('common.cancel')}
            </button>
            <button className="btn primary" type="button" onClick={submitRule}>
              {t('common.ok')}
            </button>
          </>
        }
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="label">{t('instanceConfig.permissions.ruleId')}</label>
            <input
              className="input mono"
              value={ruleForm.id}
              onChange={(e) => setRuleForm((s) => ({ ...s, id: e.target.value }))}
            />
          </div>
          <div>
            <label className="label">{t('instanceConfig.permissions.ruleSeverity')}</label>
            <select
              className="select"
              value={ruleForm.severity}
              onChange={(e) => setRuleForm((s) => ({ ...s, severity: e.target.value as PermissionSeverity }))}
            >
              {SEVERITIES.map((severity) => (
                <option key={severity} value={severity}>
                  {severityLabel(severity, t)}
                </option>
              ))}
            </select>
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('instanceConfig.permissions.rulePattern')}</label>
            <input
              className="input mono text-xs"
              value={ruleForm.pattern}
              onChange={(e) => setRuleForm((s) => ({ ...s, pattern: e.target.value }))}
              placeholder="ls *"
            />
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('instanceConfig.permissions.ruleTools')}</label>
            <input
              className="input mono text-xs"
              value={ruleToolsText}
              onChange={(e) => setRuleToolsText(e.target.value)}
              placeholder="bash, mcp_exec_command, create_terminal"
            />
            <p className="text-[11px] text-muted mt-1">{t('instanceConfig.permissions.ruleToolsHint')}</p>
          </div>
        </div>
      </Modal>
    </div>
  );
}
