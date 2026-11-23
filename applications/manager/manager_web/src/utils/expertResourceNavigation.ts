/** Resource-center return links can only target the originating template or cluster binding. */
export function expertResourceReturnPath(raw: string | null): string | null {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//') || /[\\\r\n]/.test(raw)) return null;
  try {
    const url = new URL(raw, 'http://manager.local');
    if (url.origin !== 'http://manager.local' || url.hash) return null;
    const path = decodeURIComponent(url.pathname);
    if (/[\\\r\n]/.test(path)) return null;
    if (!/^\/agent-templates(?:\/[^/?#]+)?$/.test(path) && !/^\/instances\/[^/?#]+\/agent-resources$/.test(path)) return null;
    // Do not replay consumed actions when returning from a catalog.
    for (const key of ['create', 'open', 'focusTemplate', 'selectedTemplate']) url.searchParams.delete(key);
    return url.pathname + url.search;
  } catch { return null; }
}
