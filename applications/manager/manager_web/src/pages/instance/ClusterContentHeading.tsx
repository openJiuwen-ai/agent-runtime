import { useTranslation } from 'react-i18next';

interface Props {
  section: 'serviceResources' | 'agentResources' | 'status';
}

/** Share the compact content heading across cluster configuration pages. */
export function ClusterContentHeading({ section }: Props) {
  const { i18n } = useTranslation();
  const zh = i18n.language.startsWith('zh');
  const tr = (cn: string, en: string) => (zh ? cn : en);
  const headings = {
    serviceResources: {
      title: tr('当前集群的运行资源', 'Runtime resources for this cluster'),
      description: tr('运行时模板是可复用定义，本页决定它是否在当前集群生效。', 'The catalog stores reusable runtime templates; this page activates them for the current cluster.'),
    },
    agentResources: {
      title: tr('当前集群的 Agent 资源', 'Agent resources for this cluster'),
      description: tr('Agent 是可复用定义，本页决定它是否在当前集群生效，以及谁可以使用。', 'Agents are reusable definitions; this page decides whether they apply to this cluster and who can use them.'),
    },
    status: {
      title: tr('服务连接', 'Service connectivity'),
      description: tr('查看当前集群的组件连接状态，维护服务地址并前往用户面验证。', 'Review component connectivity, maintain service addresses and verify the cluster in the user console.'),
    },
  };
  const { title, description } = headings[section];

  return <div className="min-w-0">
    <h2 className="text-base font-semibold">{title}</h2>
    <p className="text-sm text-muted">{description}</p>
  </div>;
}
