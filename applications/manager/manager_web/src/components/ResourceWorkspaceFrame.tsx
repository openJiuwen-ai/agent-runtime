import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { WorkspaceNote } from './WorkspaceNote';
import { resourceCatalog } from '../config/resources';
import { useRouter } from '../router';

/**
 * Resource pages share the same product rule: the catalog prepares reusable
 * definitions, while a specific Agent or cluster decides where they take effect.
 */
export function ResourceWorkspaceFrame({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation();
  const { path } = useRouter();
  const zh = i18n.language.startsWith('zh');
  const resource = resourceCatalog.find((entry) => entry.path === path);
  const experts = path === '/expert-package-templates';
  return (
    <div className="resource-workspace">
      {children}
      <WorkspaceNote title={resource ? t(`${resource.key}.title`) : (zh ? '资源中心' : 'Resource catalog')}>
        {experts ? (zh
          ? '将专家与专家团组合为可复用模板，在 Agent 模板中引用；发布 Agent 时，所选专家能力会随模板一起下发。'
          : 'Compose experts and expert groups into reusable templates, then reference them in an Agent template. The selected capabilities are delivered when the Agent is published.') : <>
          {resource && <>{t(`${resource.key}.subtitle`)}{zh ? '。' : '. '}</>}
          {zh ? '保存后，在 Agent模板或集群配置中引用；修改已被引用的资源前，请先查看关联模板和集群。' : 'Reference saved resources from an Agent template or cluster configuration. Review linked templates and clusters before changing a referenced resource.'}
        </>}
      </WorkspaceNote>
    </div>
  );
}
