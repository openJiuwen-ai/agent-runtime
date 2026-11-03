import { useState } from 'react';
import { useAsync } from '../../hooks/useAsync';
import { InstanceApi } from '../../services/api';
import type { InstanceSummary } from '../../types/instance';
import { InstanceExpertPackagesTab } from '../instance/instanceResourcePanel/InstanceExpertPackagesTab';

const PAGE_SIZE = 200;

async function listClusters(): Promise<InstanceSummary[]> {
  const items: InstanceSummary[] = [];
  let page = 1;
  let total = 0;
  do {
    const result = await InstanceApi.list({
      page,
      page_size: PAGE_SIZE,
      sort_by: 'jiuwenclaw_name',
      sort_order: 'asc',
    });
    total = result.total;
    items.push(...result.items);
    if (result.items.length === 0) break;
    page += 1;
  } while (items.length < total);
  return items;
}

export function ExpertPackagesPage() {
  const { data, loading, error } = useAsync(listClusters, []);
  const clusters = data ?? [];
  const [pickedId, setPickedId] = useState('');
  const selectedId =
    clusters.length === 1
      ? clusters[0].jiuwenclaw_id
      : clusters.some((cluster) => cluster.jiuwenclaw_id === pickedId)
        ? pickedId
        : '';

  return (
    <InstanceExpertPackagesTab
      instanceId={selectedId}
      clusters={clusters.map((cluster) => ({
        id: cluster.jiuwenclaw_id,
        name: cluster.jiuwenclaw_name,
      }))}
      clustersLoading={loading}
      clustersError={error}
      onClusterChange={setPickedId}
    />
  );
}
