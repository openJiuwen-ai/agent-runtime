import { Fragment, ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from '../router';
import { getProductName } from '../utils/env';

// 文档源文件与图片资源：?raw 内联 md 文本，import.meta.glob 把 docs/assets 的图片转成构建产物 URL
import gettingStartedMd from '../../docs/getting-started.md?raw';
import overviewMd from '../../docs/overview.md?raw';
import agentManagerMd from '../../docs/guides/agent_manager.md?raw';
import agentPoolManagerMd from '../../docs/guides/agent_pool_manager.md?raw';
import clusterManagerMd from '../../docs/guides/cluster_manager.md?raw';

const docImages = import.meta.glob<string>('../../docs/assets/*.png', {
  eager: true,
  query: '?url',
  import: 'default',
});

type DocKey = 'getting-started' | 'overview' | 'guide-agent' | 'guide-pool' | 'guide-cluster';

type TocEntry = {
  key: DocKey;
  labelKey: string;
  fallbackZh: string;
  /** 功能指南组：渲染为组标题下的缩进子项 */
  group?: 'guides';
};

/** 左侧目录：顶级两项 + 「功能指南」组（组标题不可点击，子项各对应 guides 下一个 md） */
const TOC: TocEntry[] = [
  { key: 'getting-started', labelKey: 'docs.toc.getting-started', fallbackZh: '快速开局' },
  { key: 'overview', labelKey: 'docs.toc.overview', fallbackZh: '产品总览' },
  { key: 'guide-agent', labelKey: 'docs.toc.guideAgent', fallbackZh: 'Agent管理', group: 'guides' },
  { key: 'guide-pool', labelKey: 'docs.toc.guidePool', fallbackZh: 'Agent实例池管理', group: 'guides' },
  { key: 'guide-cluster', labelKey: 'docs.toc.guideCluster', fallbackZh: '集群管理', group: 'guides' },
];

const DOC_SOURCES: Record<DocKey, string> = {
  'getting-started': gettingStartedMd,
  overview: overviewMd,
  'guide-agent': agentManagerMd,
  'guide-pool': agentPoolManagerMd,
  'guide-cluster': clusterManagerMd,
};

/** 图片路径映射：md 里的 `.\assets\xxx.png` → 构建产物 URL */
function resolveImage(src: string): string | null {
  const name = src.split(/[\\/]/).pop();
  if (!name) return null;
  return docImages[`../../docs/assets/${name}`] ?? null;
}

/* ---------------- 轻量 Markdown → React 渲染 ----------------
 * 零依赖，覆盖本项目文档用到的语法：标题、段落、无序列表、围栏代码块、
 * 表格、图片、行内代码/加粗/链接、<占位符>。构建成 React 节点而不是
 * innerHTML，无 XSS 面；样式统一由 index.css 的 .docs-body 提供。
 * ---------------------------------------------------------- */

/** 标题锚点 id：与 md 内部目录共用一份生成逻辑，保证跳转对得上 */
function headingAnchor(index: number): string {
  return `h-${index}`;
}

interface HeadingInfo {
  level: number;
  text: string;
  anchor: string;
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  // 行内语法依出现位置切分：`code`、**bold**、[text](url)、<placeholder>
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\)|<[^<>\s]+>)/g;
  const nodes: ReactNode[] = [];
  let last = 0;
  let i = 0;
  let m: RegExpExecArray | null;
  while ((m = pattern.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const token = m[0];
    const key = `${keyPrefix}-i${i++}`;
    if (token.startsWith('`')) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith('**')) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('[')) {
      const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (linkMatch) {
        nodes.push(
          <a key={key} href={linkMatch[2]} target="_blank" rel="noreferrer">
            {linkMatch[1]}
          </a>,
        );
      } else {
        nodes.push(token);
      }
    } else {
      // <download_url> 之类的占位符
      nodes.push(<code key={key}>{token}</code>);
    }
    last = m.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function MarkdownView({ source, onHeadings }: { source: string; onHeadings?: (headings: HeadingInfo[]) => void }) {
  const blocks = useMemo(() => {
    const lines = source.replace(/\r\n/g, '\n').split('\n');
    const out: ReactNode[] = [];
    const headings: HeadingInfo[] = [];
    let i = 0;
    let k = 0;
    let headingIndex = 0;
    const nextKey = () => `b${k++}`;

    while (i < lines.length) {
      const line = lines[i];

      // 围栏代码块
      if (line.trimStart().startsWith('```')) {
        const buf: string[] = [];
        i += 1;
        while (i < lines.length && !lines[i].trimStart().startsWith('```')) {
          buf.push(lines[i]);
          i += 1;
        }
        i += 1; // 跳过收尾 ```
        out.push(
          <pre key={nextKey()}>
            <code>{buf.join('\n')}</code>
          </pre>,
        );
        continue;
      }

      // 表格：| a | b | 表头 + |---|---| 分隔行
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        const parseRow = (row: string) =>
          row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
        const header = parseRow(line);
        i += 2;
        const rows: string[][] = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
          rows.push(parseRow(lines[i]));
          i += 1;
        }
        out.push(
          <table key={nextKey()}>
            <thead>
              <tr>
                {header.map((cell, ci) => (
                  <th key={ci}>{renderInline(cell, `th${ci}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td key={ci}>{renderInline(cell, `td${ri}-${ci}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>,
        );
        continue;
      }

      // 图片（含 Windows 风格相对路径 .\assets\xxx.png）
      const imgMatch = line.match(/^!\[([^\]]*)\]\(([^)]+)\)\s*$/);
      if (imgMatch) {
        const url = resolveImage(imgMatch[2]);
        if (url) out.push(<img key={nextKey()} src={url} alt={imgMatch[1]} />);
        i += 1;
        continue;
      }

      // 标题
      const headMatch = line.match(/^(#{1,4})\s+(.*)$/);
      if (headMatch) {
        const level = headMatch[1].length;
        const content = renderInline(headMatch[2].trim(), `hd${i}`);
        const anchor = headingAnchor(headingIndex);
        if (level >= 2) headings.push({ level, text: headMatch[2].trim(), anchor });
        headingIndex += 1;
        out.push(
          level === 1 ? (
            <h1 key={nextKey()} id={anchor}>{content}</h1>
          ) : level === 2 ? (
            <h2 key={nextKey()} id={anchor}>{content}</h2>
          ) : level === 3 ? (
            <h3 key={nextKey()} id={anchor}>{content}</h3>
          ) : (
            <h4 key={nextKey()} id={anchor}>{content}</h4>
          ),
        );
        i += 1;
        continue;
      }

      // 无序列表（含跨行连续项）
      if (/^\s*-\s+/.test(line)) {
        const items: ReactNode[] = [];
        while (i < lines.length && /^\s*-\s+/.test(lines[i])) {
          items.push(
            <li key={`li${i}`}>{renderInline(lines[i].replace(/^\s*-\s+/, ''), `li${i}`)}</li>,
          );
          i += 1;
        }
        out.push(<ul key={nextKey()}>{items}</ul>);
        continue;
      }

      // 空行
      if (!line.trim()) {
        i += 1;
        continue;
      }

      // 普通段落
      out.push(<p key={nextKey()}>{renderInline(line.trim(), `p${i}`)}</p>);
      i += 1;
    }

    onHeadings?.(headings);
    return out;
  }, [source, onHeadings]);

  return <>{blocks}</>;
}

/* ---------------- 文档页 ---------------- */

export function DocsPage() {
  const { t, i18n } = useTranslation();
  const { params, navigate } = useRouter();
  const productName = getProductName();

  const requested = params.get('doc');
  const docKey: DocKey = TOC.some((d) => d.key === requested) ? (requested as DocKey) : 'getting-started';
  const isZh = i18n.language.startsWith('zh');

  /** 右侧 md 内部目录：渲染时从正文提取，跳转用 scrollIntoView */
  const [headings, setHeadings] = useState<HeadingInfo[]>([]);
  const [activeAnchor, setActiveAnchor] = useState('');
  const articleRef = useRef<HTMLElement | null>(null);
  const headingSpyRef = useRef<IntersectionObserver | null>(null);

  const handleHeadings = (next: HeadingInfo[]) => {
    // 仅在提取结果变化时 setState，避免渲染循环
    setHeadings((prev) => (prev.length === next.length && prev.every((h, idx) => h.anchor === next[idx].anchor && h.text === next[idx].text) ? prev : next));
  };

  // 文档切换后重建滚动监听
  useEffect(() => {
    setActiveAnchor('');
    const root = articleRef.current;
    if (!root || headings.length === 0) return;
    headingSpyRef.current?.disconnect();
    const observer = new IntersectionObserver(
      (entries) => {
        // 取视口最上方可见的标题作为当前项
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (visible?.target.id) setActiveAnchor(visible.target.id);
      },
      { rootMargin: '-64px 0px -70% 0px', threshold: 0 },
    );
    for (const h of headings) {
      const el = root.querySelector(`#${CSS.escape(h.anchor)}`);
      if (el) observer.observe(el);
    }
    headingSpyRef.current = observer;
    return () => observer.disconnect();
  }, [headings, docKey]);

  const scrollToHeading = (anchor: string) => {
    articleRef.current?.querySelector(`#${CSS.escape(anchor)}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setActiveAnchor(anchor);
  };

  return (
    <div className="min-h-screen flex flex-col bg-[var(--bg)]">
      {/* 顶栏：与管理面 Shell 同款品牌区 */}
      <header className="topbar !static">
        <div className="brand">
          <img src="/logo.svg" alt={productName} className="brand-logo-img" />
          <div className="brand-text">
            <span className="brand-title">{t('brand.title')}</span>
            <span className="brand-sub">Manager</span>
          </div>
        </div>
        <button type="button" className="btn sm" onClick={() => navigate('/manager')}>
          {t('docs.backToConsole')}
        </button>
      </header>

      <main className="flex-1 w-full max-w-7xl mx-auto px-6 py-8 flex flex-col gap-6 min-h-0">
        {/* 文档标题 */}
        <div className="flex items-baseline gap-3 flex-wrap">
          <h1 className="text-xl font-bold text-[var(--text-strong)] m-0">
            {t('docs.title', { productName })}
          </h1>
          <span className="text-xs mono text-accent px-2 py-0.5 rounded-full bg-[var(--accent-subtle)] border border-[var(--border-accent)]">
            v1.0
          </span>
        </div>

        <div className="flex-1 flex gap-6 items-start min-h-0">
          {/* 左侧目录 */}
          <nav className="w-44 shrink-0 sticky top-6 flex flex-col gap-1">
            {TOC.map((d) =>
              d.group === 'guides' ? (
                <button
                  key={d.key}
                  type="button"
                  onClick={() => navigate(`/docs?doc=${d.key}`)}
                  className={`docs-toc-item docs-toc-item--nested ${docKey === d.key ? 'active' : ''}`}
                >
                  {isZh ? d.fallbackZh : t(d.labelKey)}
                </button>
              ) : (
                <Fragment key={d.key}>
                  <button
                    type="button"
                    onClick={() => navigate(`/docs?doc=${d.key}`)}
                    className={`docs-toc-item ${docKey === d.key ? 'active' : ''}`}
                  >
                    {isZh ? d.fallbackZh : t(d.labelKey)}
                  </button>
                  {/* 「功能指南」组标题插在产品总览之后 */}
                  {d.key === 'overview' && (
                    <div className="docs-toc-group">{t('docs.toc.guides')}</div>
                  )}
                </Fragment>
              ),
            )}
          </nav>

          {/* 中间正文 */}
          <article ref={articleRef} className="docs-body flex-1 min-w-0 card p-6 lg:p-8">
            <MarkdownView source={DOC_SOURCES[docKey]} onHeadings={handleHeadings} />
          </article>

          {/* 右侧 md 内部目录：按 md 标题层级缩进，点击跳到对应标题 */}
          <nav className="w-52 shrink-0 sticky top-6 hidden xl:flex flex-col">
            {headings.length > 0 && (
              <div className="docs-toc-group !border-t-0 !mt-0 !pt-0">{t('docs.onThisPage')}</div>
            )}
            <div className="docs-headings">
              {headings.map((h) => {
                // Tailwind 按字面量裁剪 @layer components 规则，层级类必须写全名，不能用模板拼接
                const levelClass =
                  h.level === 2
                    ? 'docs-heading-item--l2'
                    : h.level === 3
                      ? 'docs-heading-item--l3'
                      : 'docs-heading-item--l4';
                return (
                  <button
                    key={h.anchor}
                    type="button"
                    onClick={() => scrollToHeading(h.anchor)}
                    className={`docs-heading-item ${levelClass} ${activeAnchor === h.anchor ? 'active' : ''}`}
                    title={h.text}
                  >
                    {h.text}
                  </button>
                );
              })}
            </div>
          </nav>
        </div>
      </main>
    </div>
  );
}
