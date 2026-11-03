export interface Page<T> { items: T[]; total: number; page_size: number }

/** Read every page; partial or failed results must never masquerade as a total. */
export async function collectPages<T>(fetchPage: (page: number, size: number) => Promise<Page<T>>): Promise<T[]> {
  const first = await fetchPage(1, 100);
  const items = [...first.items];
  const size = first.page_size || 100;
  for (let page = 2; page <= Math.ceil(first.total / size); page++) {
    const next = await fetchPage(page, size);
    items.push(...next.items);
  }
  return items;
}
export const isConnected = (status?: string | null) => ['online', 'ready', 'running', 'active', 'ok'].includes((status || '').toLowerCase());
export function allComponentsConnected(instance: { gateway_status?: string | null; runtime_status?: string | null; user_web_status?: string | null }) {
  return isConnected(instance.gateway_status) && isConnected(instance.runtime_status) && isConnected(instance.user_web_status);
}
