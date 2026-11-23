import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createInstance } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { createServer } from 'vite';

const server = await createServer({ configFile: false, esbuild: { jsx: 'automatic' }, server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  const { ClusterTemplatePicker } = await server.ssrLoadModule('/src/pages/instance/instanceResourcePanel/ClusterTemplatePicker.tsx');
  const i18n = createInstance();
  await i18n.init({ lng: 'zh', resources: { zh: { translation: {} } } });
  const render = props => renderToStaticMarkup(React.createElement(I18nextProvider, { i18n }, React.createElement(ClusterTemplatePicker, {
    kind: 'runtime', candidates: [], value: '', loading: false, error: null, onChange() {}, onReload() {}, onManage() {}, ...props,
  })));

  for (const kind of ['runtime', 'agent']) {
    test(`${kind}: empty catalog offers a primary preparation action, not an unusable selector`, () => {
      const html = render({ kind });
      assert.match(html, /还没有/);
      assert.match(html, /class="btn primary"/);
      assert.match(html, /返回集群/);
      assert.doesNotMatch(html, /type="radio"/);
    });
    test(`${kind}: all disabled differs from empty, errors and loading never claim there are no templates`, () => {
      const disabled = render({ kind, candidates: [{ id: 'disabled', label: 'Disabled', enabled: false }] });
      assert.match(disabled, /暂无可用/);
      assert.match(disabled, /请先启用模板/);
      assert.doesNotMatch(disabled, /还没有|type="radio"/);
      assert.match(render({ kind, error: 'Failed to load' }), /role="alert"/);
      assert.doesNotMatch(render({ kind, error: 'Failed to load' }), /还没有|class="btn primary"/);
      assert.match(render({ kind, loading: true }), /role="status"/);
    });
    test(`${kind}: selection is single-choice, disabled templates cannot be selected, stale selections are not counted`, () => {
      const candidates = [{ id: 'available', label: 'Available', enabled: true }, { id: 'disabled', label: 'Disabled', enabled: false }];
      const html = render({ kind, candidates, value: 'available' });
      assert.equal((html.match(/type="radio"/g) || []).length, 2);
      assert.match(html, /checked="" value="available"/);
      assert.match(html, /disabled="" value="disabled"/);
      assert.match(html, /已选择 1 项/);
      assert.match(render({ kind, candidates, value: 'deleted' }), /已选择 0 项/);
    });
  }
} finally { await server.close(); }
