import { useTranslation } from 'react-i18next';

interface Props {
  section: 'serviceResources' | 'agentResources' | 'status';
}

/** Share the compact content heading across cluster configuration pages. */
export function ClusterContentHeading({ section }: Props) {
  const { t } = useTranslation();
  const headings = {
    serviceResources: {
      title: t('redesign.heading.runtimeTitle'),
      description: t('redesign.heading.runtimeDesc'),
    },
    agentResources: {
      title: t('redesign.heading.agentTitle'),
      description: t('redesign.heading.agentDesc'),
    },
    status: {
      title: t('redesign.heading.statusTitle'),
      description: t('redesign.heading.statusDesc'),
    },
  };
  const { title, description } = headings[section];

  return (
    <div className="min-w-0">
      <h2 className="text-base font-semibold">{title}</h2>
      <p className="text-sm text-muted">{description}</p>
    </div>
  );
}
