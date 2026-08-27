import './i18n';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './index.css';
import { getProductName } from './utils/env';


/**
 * 浏览器不可解析 Kubernetes Service DNS。安装最外层安全网，确保任何模块、旧配置或
 * 第三方封装产生的内部 Manager/Identity URL 都在发出前改写为当前 Manager Web 同源反代。
 */
function installSameOriginApiGuard(): void {
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw = input instanceof Request ? input.url : String(input);
    let rewritten = raw;
    try {
      const url = new URL(raw, window.location.origin);
      const internalManager = url.hostname === 'jiuwenclaw-manager-server';
      const internalIdentity = url.hostname === 'jiuwenclaw-identity';
      if (internalManager || internalIdentity) {
        const prefix = internalIdentity ? '/idp' : '/api';
        const upstreamPrefix = internalManager && url.pathname.startsWith('/api/') ? '/api' : '';
        rewritten = `${window.location.origin}${prefix}${url.pathname.slice(upstreamPrefix.length)}${url.search}${url.hash}`;
      }
    } catch { /* fetch 自己处理非法 URL */ }

    if (input instanceof Request && rewritten !== raw) {
      return nativeFetch(new Request(rewritten, input), init);
    }
    return nativeFetch(rewritten === raw ? input : rewritten, init);
  };
}

installSameOriginApiGuard();

document.title = `${getProductName()} Manager`;

ReactDOM.createRoot(document.getElementById('root')!).render(
  <BrowserRouter>
    <App />
  </BrowserRouter>,
);
