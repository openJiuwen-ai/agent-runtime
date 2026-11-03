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
      description: tr('运行规格目录是可复用定义，本页决定它是否在当前集群生效。', 'The catalog stores reusable profiles; this page activates them for the current cluster.'),
    },
    agentResources: {
      title: tr('当前集群已绑定 Agent', 'Agents bound to this cluster'),
      description: tr('授权条件为空时，表示当前集群内所有已准入身份。', 'An empty condition means all admitted identities in this cluster.'),
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
