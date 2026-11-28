import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { createServer } from 'vite';

const globals = ['window', 'document', 'localStorage', 'fetch'];
const originals = new Map(globals.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
globalThis.window = { location: { origin: 'http://localhost', protocol: 'http:' } };
globalThis.document = {
  cookie: '', documentElement: { lang: '' },
  addEventListener() {}, removeEventListener() {},
};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
const server = await createServer({
  configFile: false,
  esbuild: { jsx: 'automatic' },
  server: { middlewareMode: true, hmr: false, ws: false },
  appType: 'custom',
});

const reply = (items, total = items.length, page_size = 100) =>
  new Response(JSON.stringify({ code: 200, data: { items, total, page_size } }), { status: 200 });
const resource = (id, name = id) => ({ resource_id: id, resource_name: name, ref_template_id: 'shared-config' });
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
const text = node => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const checkboxes = renderer => renderer.root.findAllByProps({ type: 'checkbox' });
const optionNames = renderer => checkboxes(renderer).map(node => text(node.parent));
const fieldSelect = renderer => renderer.root.findAllByType('select').find(node =>
  node.findAllByProps({ value: 'bot_id' }).some(child => child.type === 'option'));
const opSelect = renderer => renderer.root.findAllByType('select').find(node =>
  node.findAllByProps({ value: 'not in' }).some(child => child.type === 'option'));
const dropdown = renderer => renderer.root.findAllByType('button').find(node =>
  node.props.className?.includes('text-left'));

try {
  const { MatchExprEditor } = await server.ssrLoadModule('/src/components/MatchExprEditor.tsx');
  const { parseMatchExpr } = await server.ssrLoadModule('/src/utils/matchExpr.ts');

  function mockApi(loadResources) {
    const calls = [];
    globalThis.fetch = async url => {
      const parsed = new URL(url);
      calls.push(parsed.pathname);
      if (parsed.pathname.startsWith('/idp/')) return new Response(JSON.stringify({ items: [], total: 0, page_size: 100 }), { status: 200 });
      if (parsed.pathname.endsWith('/agent-resources')) return loadResources(parsed);
      // Return valid template choices so reverting the editor's source fails on its output.
      if (parsed.pathname.includes('/agent-templates')) {
        return reply([{ template_id: 'shared-config', template_name: 'Shared config' }]);
      }
      return reply([]);
    };
    return calls;
  }

  async function mount(t, cluster, value = '') {
    const changes = [];
    const props = { value, agentResourceInstanceId: cluster, onChange: next => changes.push(next) };
    let renderer;
    await act(async () => { renderer = create(React.createElement(MatchExprEditor, props)); });
    t.after(async () => { await act(async () => renderer.unmount()); });
    if (!value) {
      await act(async () => renderer.root.findAllByType('select')[0].props.onChange({ target: { value: 'custom' } }));
      await act(async () => fieldSelect(renderer).props.onChange({ target: { value: 'bot_id' } }));
    }
    await act(async () => dropdown(renderer).props.onClick());
    return { renderer, changes, props };
  }

  await test('editor emits resource IDs from actual checkbox selection for inclusion and exclusion', async t => {
    const calls = mockApi(url => reply([
      resource(url.searchParams.get('page') === '1' ? 'resource-a' : 'resource-b',
        url.searchParams.get('page') === '1' ? 'Agent A' : 'Agent B'),
    ], 2, 1));
    const { renderer, changes, props } = await mount(t, 'cluster-a');
    assert.deepEqual(optionNames(renderer), ['Agent Aresource-a', 'Agent Bresource-b']);
    await act(async () => checkboxes(renderer)[1].props.onChange());
    let condition = parseMatchExpr(changes.at(-1)).root.children[0];
    assert.deepEqual({ field: condition.field, op: condition.op, values: condition.values },
      { field: 'bot_id', op: 'in', values: ['resource-b'] });
    await act(async () => opSelect(renderer).props.onChange({ target: { value: 'not in' } }));
    condition = parseMatchExpr(changes.at(-1)).root.children[0];
    assert.equal(condition.op, 'not in');
    assert.deepEqual(condition.values, ['resource-b']);
    assert.doesNotMatch(changes.at(-1), /shared-config|resource-a/);
    assert.ok(!calls.some(path => path.includes('/agent-templates')));

    // Remount with the emitted value, as when reopening an existing rule.
    let reopened;
    await act(async () => { reopened = create(React.createElement(MatchExprEditor, { ...props, value: changes.at(-1) })); });
    t.after(async () => { await act(async () => reopened.unmount()); });
    await act(async () => dropdown(reopened).props.onClick());
    assert.deepEqual(checkboxes(reopened).map(node => node.props.checked), [false, true]);
    assert.equal(opSelect(reopened).props.value, 'not in');
  });

  await test('switching clusters hides loaded old options while the next response is pending', async t => {
    const next = deferred();
    mockApi(url => url.pathname.includes('/cluster-a/') ? reply([resource('resource-a')]) : next.promise);
    const { renderer, props } = await mount(t, 'cluster-a');
    assert.deepEqual(optionNames(renderer), ['resource-aresource-a']);
    await act(async () => renderer.update(React.createElement(MatchExprEditor, { ...props, agentResourceInstanceId: 'cluster-b' })));
    assert.deepEqual(optionNames(renderer), [], 'old cluster options must disappear before the new request completes');
    await act(async () => next.resolve(reply([resource('resource-b')])));
    assert.deepEqual(optionNames(renderer), ['resource-bresource-b']);
  });

  for (const lateResult of ['success', 'failure']) {
    await test(`late ${lateResult} from an old cluster cannot replace the current options`, async t => {
      const old = deferred();
      mockApi(url => url.pathname.includes('/cluster-a/') ? old.promise : reply([resource('resource-b')]));
      const { renderer, props } = await mount(t, 'cluster-a');
      assert.deepEqual(optionNames(renderer), []);
      await act(async () => renderer.update(React.createElement(MatchExprEditor, { ...props, agentResourceInstanceId: 'cluster-b' })));
      assert.deepEqual(optionNames(renderer), ['resource-bresource-b']);
      await act(async () => old.resolve(lateResult === 'success'
        ? reply([resource('resource-a')])
        : new Response(JSON.stringify({ detail: 'old cluster unavailable' }), { status: 503 })));
      assert.deepEqual(optionNames(renderer), ['resource-bresource-b']);
      assert.deepEqual(renderer.root.findAllByProps({ role: 'alert' }), []);
    });
  }

  await test('legacy template IDs remain removable and selecting a resource explicitly replaces them', async t => {
    mockApi(() => reply([resource('resource-a', 'Agent A')]));
    const { renderer, changes } = await mount(t, 'cluster-a', "bot_id in ('shared-config')");
    assert.equal(renderer.root.findAllByProps({ role: 'alert' }).length, 1);
    assert.equal(checkboxes(renderer).length, 2);
    await act(async () => checkboxes(renderer).find(node => node.props.checked).props.onChange());
    await act(async () => checkboxes(renderer)[0].props.onChange());
    assert.deepEqual(parseMatchExpr(changes.at(-1)).root.children[0].values, ['resource-a']);
    assert.doesNotMatch(changes.at(-1), /shared-config/);
    assert.equal(renderer.root.findAllByProps({ role: 'alert' }).length, 0);
  });
} finally {
  await server.close();
  for (const [key, descriptor] of originals) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
}
