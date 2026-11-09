import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ApiError,
  ApprovalApi,
  type ApprovalSkillPreview,
  type ApprovalSkillPreviewFile,
} from '../../services/api';
import { useRouter } from '../../router';
import { formatTime } from '../../utils/format';

interface PreviewError {
  status: number;
  detail: string;
}

interface PreviewFileState {
  status: 'idle' | 'loading' | 'success' | 'error';
  path?: string;
  text?: string;
  imageUrl?: string;
  truncated?: boolean;
  error?: string;
}

type TreeNode =
  | { type: 'dir'; name: string; path: string; children: TreeNode[] }
  | { type: 'file'; name: string; path: string; file: ApprovalSkillPreviewFile };

function buildTree(files: ApprovalSkillPreviewFile[]): TreeNode[] {
  const root: TreeNode = { type: 'dir', name: '', path: '', children: [] };
  for (const file of files) {
    const parts = file.path.split('/');
    let dir = root;
    parts.forEach((part, index) => {
      const path = parts.slice(0, index + 1).join('/');
      if (index === parts.length - 1) {
        dir.children.push({ type: 'file', name: part, path, file });
        return;
      }
      let next = dir.children.find(
        (node): node is Extract<TreeNode, { type: 'dir' }> =>
          node.type === 'dir' && node.name === part,
      );
      if (!next) {
        next = { type: 'dir', name: part, path, children: [] };
        dir.children.push(next);
      }
      dir = next;
    });
  }

  const sortNodes = (nodes: TreeNode[]) => {
    nodes.sort((left, right) => {
      if (left.type !== right.type) return left.type === 'dir' ? -1 : 1;
      return left.name.localeCompare(right.name);
    });
    for (const node of nodes) {
      if (node.type === 'dir') sortNodes(node.children);
    }
  };
  sortNodes(root.children);
  return root.children;
}

function collectDirectories(nodes: TreeNode[], result = new Set<string>()): Set<string> {
  for (const node of nodes) {
    if (node.type !== 'dir') continue;
    result.add(node.path);
    collectDirectories(node.children, result);
  }
  return result;
}

function fileLabel(path: string): string {
  return path.split('/').filter(Boolean).pop() || path;
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value)) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB'];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 || unit === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`;
}

function splitFrontMatter(content: string): { frontMatter: string | null; body: string } {
  const match = /^(?:\uFEFF)?---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(content);
  if (!match) return { frontMatter: null, body: content };
  return { frontMatter: match[1], body: content.slice(match[0].length) };
}

function isMarkdownPath(path: string): boolean {
  return /\.mdx?$/i.test(path);
}

function isJsonPath(path: string): boolean {
  return /\.json$/i.test(path);
}

function prettyJson(content: string): string {
  try {
    return JSON.stringify(JSON.parse(content), null, 2);
  } catch {
    return content;
  }
}

function MarkdownBody({ content }: { content: string }) {
  return (
    <div className="docs-body skill-preview-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

export function ApprovalSkillPreviewPage({ orderNum }: { orderNum: string }) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [preview, setPreview] = useState<ApprovalSkillPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<PreviewError | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [tab, setTab] = useState<'content' | 'files'>('content');
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [fileState, setFileState] = useState<PreviewFileState>({ status: 'idle' });

  const goBack = useCallback(
    () => navigate(`/approvals/${encodeURIComponent(orderNum)}`),
    [navigate, orderNum],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') goBack();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [goBack]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    ApprovalApi.previewSkillPackage(orderNum)
      .then((data) => {
        if (cancelled) return;
        setPreview(data);
        setLoading(false);
      })
      .catch((loadError: unknown) => {
        if (cancelled) return;
        setPreview(null);
        setLoading(false);
        setError({
          status: loadError instanceof ApiError ? loadError.status : 0,
          detail: loadError instanceof Error ? loadError.message : String(loadError),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [orderNum, reloadToken]);

  const tree = useMemo(() => buildTree(preview?.files ?? []), [preview]);
  const directories = useMemo(
    () => Array.from(collectDirectories(tree)).sort(),
    [tree],
  );

  useEffect(() => {
    setExpanded(new Set(directories));
  }, [directories]);

  useEffect(() => {
    if (!preview || selectedPath) return;
    const preferred =
      preview.files.find((file) => file.path.toLowerCase() === 'skill.md')
      ?? preview.files.find((file) => file.kind !== 'binary')
      ?? preview.files[0];
    setSelectedPath(preferred?.path ?? null);
  }, [preview, selectedPath]);

  const selectedFile = useMemo(
    () => preview?.files.find((file) => file.path === selectedPath) ?? null,
    [preview, selectedPath],
  );

  // 个人版「内容详情」只展示 SKILL.md 正文；文件预览里仍保留 front matter。
  const skillBody = useMemo(
    () => (preview ? splitFrontMatter(preview.skill_md).body : ''),
    [preview],
  );

  useEffect(() => {
    if (!selectedFile || selectedFile.kind === 'binary') {
      setFileState({ status: 'idle' });
      return;
    }
    if (preview && selectedFile.path.toLowerCase() === 'skill.md') {
      setFileState({
        status: 'success',
        path: selectedFile.path,
        text: preview.skill_md,
        truncated: preview.skill_md_truncated,
      });
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    const path = selectedFile.path;
    setFileState({ status: 'loading', path });
    ApprovalApi.previewSkillPackageFile(orderNum, selectedFile.path)
      .then(async ({ blob, truncated }) => {
        if (cancelled) return;
        if (selectedFile.kind === 'image') {
          objectUrl = URL.createObjectURL(blob);
          setFileState({ status: 'success', path, imageUrl: objectUrl });
          return;
        }
        const text = await blob.text();
        if (!cancelled) setFileState({ status: 'success', path, text, truncated });
      })
      .catch((fileError: unknown) => {
        if (cancelled) return;
        setFileState({
          status: 'error',
          path,
          error: fileError instanceof Error ? fileError.message : String(fileError),
        });
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [orderNum, preview, selectedFile]);

  const toggleDirectory = (path: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const renderNode = (node: TreeNode, depth: number): ReactNode => {
    const indent = { paddingLeft: `${8 + depth * 14}px` };
    if (node.type === 'dir') {
      const open = expanded.has(node.path);
      return (
        <div key={node.path} role="treeitem" aria-expanded={open}>
          <button
            type="button"
            className="skill-preview-tree__row is-dir"
            style={indent}
            onClick={() => toggleDirectory(node.path)}
            title={node.path}
            data-testid="approval-skill-preview-tree-item"
            data-variant={node.path}
            data-kind="directory"
          >
            <span className="skill-preview-tree__chevron" aria-hidden="true">
              {open ? '▾' : '▸'}
            </span>
            <span className="truncate">{node.name}</span>
          </button>
          {open ? (
            <div role="group">{node.children.map((child) => renderNode(child, depth + 1))}</div>
          ) : null}
        </div>
      );
    }

    const active = node.path === selectedPath;
    return (
      <button
        key={node.path}
        type="button"
        role="treeitem"
        aria-selected={active}
        className={`skill-preview-tree__row is-file${active ? ' is-selected' : ''}`}
        style={indent}
        onClick={() => setSelectedPath(node.path)}
        title={node.path}
        data-testid="approval-skill-preview-tree-item"
        data-variant={node.path}
        data-kind="file"
      >
        <span className={`skill-preview-tree__dot is-${node.file.kind}`} aria-hidden="true" />
        <span className="truncate">{node.name}</span>
        {node.file.kind === 'binary' ? (
          <span className="skill-preview-tree__badge">
            {t('approvals.previewNotPreviewableShort')}
          </span>
        ) : null}
      </button>
    );
  };

  const renderPreviewBody = () => {
    if (!selectedFile) {
      return <div className="skill-preview-state">{t('approvals.previewSelectFile')}</div>;
    }
    if (selectedFile.kind === 'binary') {
      return <div className="skill-preview-state">{t('approvals.previewBinaryHint')}</div>;
    }
    if (fileState.path !== selectedFile.path || fileState.status === 'idle' || fileState.status === 'loading') {
      return <div className="skill-preview-state">{t('common.loading')}</div>;
    }
    if (fileState.status === 'error') {
      return (
        <div className="skill-preview-state text-danger">
          {t('approvals.previewLoadFailed', { detail: fileState.error ?? '' })}
        </div>
      );
    }
    if (selectedFile.kind === 'image' && fileState.imageUrl) {
      return (
        <div className="skill-preview-image">
          <img src={fileState.imageUrl} alt={fileLabel(selectedFile.path)} />
        </div>
      );
    }
    const text = fileState.text ?? '';
    if (isMarkdownPath(selectedFile.path)) {
      const parts = splitFrontMatter(text);
      return (
        <>
          {parts.frontMatter ? (
            <pre className="skill-preview-frontmatter">{parts.frontMatter}</pre>
          ) : null}
          <MarkdownBody content={parts.body || ' '} />
        </>
      );
    }
    return (
      <pre className="skill-preview-code">
        {isJsonPath(selectedFile.path) ? prettyJson(text) : text}
      </pre>
    );
  };

  const errorKey = (() => {
    if (!error) return 'errors.loadFailed';
    if (error.status === 410) return 'approvals.skillPackageExpired';
    if (error.status === 502) return 'approvals.skillPackageUnavailable';
    if (error.status === 400) return 'approvals.previewPackageInvalid';
    if (error.status === 404) return 'approvals.previewNotFound';
    return 'errors.loadFailed';
  })();

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="page-header flex w-full min-w-0 shrink-0 flex-wrap items-start gap-3">
        <button
          type="button"
          className="btn ghost sm shrink-0 mt-0.5"
          onClick={goBack}
          title={t('approvals.previewEscHint')}
          data-testid="approval-skill-preview-back"
        >
          ← {t('approvals.previewBack')}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <div className="page-title truncate" title={preview?.skill_name || orderNum}>
              {preview?.skill_name || t('approvals.skillPreviewTitle')}
            </div>
            <span className="badge">{t('approvals.previewReadonly')}</span>
            {preview?.skill_version ? (
              <span className="text-xs text-muted">{preview.skill_version}</span>
            ) : null}
          </div>
          {preview ? (
            <div className="mt-1 text-sm text-muted break-words">
              {preview.description || t('approvals.previewNoDescription')}
            </div>
          ) : null}
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
            <span>{t('approvals.previewSource', { orderNum })}</span>
            {preview?.expires_at ? (
              <span>
                {t('approvals.previewExpiresAt', {
                  expiresAt: formatTime(preview.expires_at),
                })}
              </span>
            ) : null}
            <span>{t('approvals.previewEscHint')}</span>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="card text-sm text-muted" data-testid="approval-skill-preview-state" data-variant="loading">
          {t('common.loading')}
        </div>
      ) : error ? (
        <div className="card text-sm text-danger" data-testid="approval-skill-preview-state" data-variant="error">
          <div>{t(errorKey, { detail: error.detail })}</div>
          <button
            type="button"
            className="btn sm mt-3"
            onClick={() => setReloadToken((value) => value + 1)}
            data-testid="approval-skill-preview-retry"
          >
            {t('common.refresh')}
          </button>
        </div>
      ) : preview ? (
        <>
          <div className="tabs-bar max-w-full shrink-0 self-start overflow-x-auto" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'content'}
              className={`tab${tab === 'content' ? ' active' : ''}`}
              onClick={() => setTab('content')}
              data-testid="approval-skill-preview-tab"
              data-variant="content"
            >
              {t('approvals.previewTabsContent')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'files'}
              className={`tab${tab === 'files' ? ' active' : ''}`}
              onClick={() => setTab('files')}
              data-testid="approval-skill-preview-tab"
              data-variant="files"
            >
              {t('approvals.previewTabsFiles')}
              <span className="tab__count">{preview.files.length}</span>
            </button>
          </div>

          {tab === 'content' ? (
            <section
              className="card skill-preview-pane"
              data-testid="approval-skill-preview-content"
            >
              {skillBody.trim() ? (
                <>
                  <MarkdownBody content={skillBody} />
                  {preview.skill_md_truncated ? (
                    <div className="skill-preview-hint">
                      {t('approvals.previewTruncated')}
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="text-sm text-muted">{t('approvals.previewNoContent')}</div>
              )}
            </section>
          ) : (
            <section className="skill-preview-files" data-testid="approval-skill-preview-files">
              <aside
                className="skill-preview-files__tree"
                aria-label={t('approvals.previewFileTree')}
                data-testid="approval-skill-preview-tree"
              >
                {tree.length === 0 ? (
                  <div className="skill-preview-state">{t('approvals.previewEmptyFiles')}</div>
                ) : (
                  <div role="tree">{tree.map((node) => renderNode(node, 0))}</div>
                )}
              </aside>
              <section
                className="skill-preview-files__content"
                aria-live="polite"
                data-testid="approval-skill-preview-file-content"
              >
                <header className="skill-preview-files__header">
                  <span className="mono break-all text-[12px]">
                    {selectedFile?.path || '—'}
                  </span>
                  {selectedFile ? (
                    <span className="shrink-0 text-[11px] text-muted">
                      {formatBytes(selectedFile.size)}
                    </span>
                  ) : null}
                </header>
                <div className="skill-preview-files__body">
                  {renderPreviewBody()}
                  {fileState.truncated && fileState.path === selectedFile?.path ? (
                    <div className="skill-preview-hint">
                      {t('approvals.previewTruncated')}
                    </div>
                  ) : null}
                </div>
              </section>
            </section>
          )}
        </>
      ) : null}
    </div>
  );
}
