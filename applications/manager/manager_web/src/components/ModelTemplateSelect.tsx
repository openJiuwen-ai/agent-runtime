import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ModelTemplateApi } from '../services/api';

interface ModelOption {
  template_id: string;
  template_name: string;
}

interface Props {
  label: string;
  /** 当前选中的 model_template.template_id；空串 = 继承主 Agent 模型 */
  value: string;
  onChange: (id: string) => void;
}

/** 模型模板下拉：从 model-templates 拉启用的模型模板，供 react 压缩器选择模型。 */
export function ModelTemplateSelect({ label, value, onChange }: Props) {
  const { t } = useTranslation();
  const [options, setOptions] = useState<ModelOption[]>([]);

  useEffect(() => {
    let cancelled = false;
    ModelTemplateApi.list({ page: 1, page_size: 200, enabled: true })
      .then((res) => {
        if (!cancelled) setOptions(res.items ?? []);
      })
      .catch(() => {
        if (!cancelled) setOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const hasCurrent = !value || options.some((m) => m.template_id === value);

  return (
    <div>
      <label className="label">{label}</label>
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{t('reactTemplates.compressorModelNone')}</option>
        {options.map((m) => (
          <option key={m.template_id} value={m.template_id}>
            {m.template_name ? `${m.template_name} (${m.template_id})` : m.template_id}
          </option>
        ))}
        {!hasCurrent && <option value={value}>{value}</option>}
      </select>
    </div>
  );
}
