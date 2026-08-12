import { useTranslation } from 'react-i18next';
import { Empty } from '../../components/Empty';

/** 安全护栏配置页（占位，功能待实现）。 */
export function SafetyGuardrailsPage() {
  const { t } = useTranslation();

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header">
        <div>
          <div className="page-title">{t('safetyGuardrails.title')}</div>
          <div className="page-subtitle">{t('safetyGuardrails.subtitle')}</div>
        </div>
      </div>
      <div className="card">
        <Empty text={t('safetyGuardrails.empty')} />
      </div>
    </div>
  );
}
