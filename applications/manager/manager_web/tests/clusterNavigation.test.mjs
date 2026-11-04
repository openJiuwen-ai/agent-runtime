import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';
const server = await createServer({configFile:false, server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
try {
  const {clusterPagePath,clusterNavigationGroup,canonicalClusterSection,canonicalClusterPath,clusterSetupSections}=await server.ssrLoadModule('/src/utils/clusterNavigation.ts');
  test('six primary tabs remain independent and related pages select their owner',()=>{
    for (const tab of ['cluster-config',...clusterSetupSections,'other-settings']) assert.equal(clusterNavigationGroup(tab),tab);
    for (const tab of ['config','workspace-quota','team-quota','token-quota','cost','audit']) assert.equal(clusterNavigationGroup(tab),'other-settings');
  });
  test('old URLs resolve without changing the intended module or cluster',()=>{
    assert.equal(canonicalClusterSection(undefined,false),'cluster-config');
    assert.equal(canonicalClusterSection('resources',false),'agent-resources');
    assert.equal(canonicalClusterSection('quota',false),'other-settings');
    assert.equal(canonicalClusterSection('quota',true),'other-settings');
    assert.equal(canonicalClusterSection('setup',false),'setup');
    assert.equal(canonicalClusterSection('unknown',false),null);
  });
  test('navigation retains template handoff but drops page-specific modal and filter parameters',()=>{
    const context=new URLSearchParams({template:'agent/a',returnTo:'/agent-templates?search=hello',setup:'1',create:'1',focusTemplate:'old'});
    for(const section of [...clusterSetupSections,'cluster-config','other-settings','setup']){
      const url=new URL(clusterPagePath('cluster/a',section,context),'http://localhost');
      assert.equal(url.pathname,'/instances/cluster%2Fa/'+section);
      assert.equal(url.searchParams.get('template'),'agent/a');
      assert.equal(url.searchParams.get('returnTo'),'/agent-templates?search=hello');
      assert.equal(url.searchParams.get('setup'),'1');
      assert.equal(url.searchParams.has('create'),false);
      assert.equal(url.searchParams.has('focusTemplate'),false);
    }
  });
  test('legacy policy bookmarks locate the matching section without losing cluster handoff',()=>{
    const mappings = [
      ['config', 'logMasking', 'log-masking'], ['config', 'logging', 'logging'],
      ['config', 'auditLog', 'audit-log'], ['config', 'cronPolicy', 'cron-quota'],
      ['workspace-quota', '', 'workspace-quota'], ['team-quota', '', 'team-quota'],
      ['token-quota', '', 'token-quota'], ['cost', '', 'cost'], ['audit', '', 'business-audit'],
    ];
    for (const [section, configTab, setting] of mappings) {
      const context = new URLSearchParams({ configTab, templateId: 'agent/a', returnTo: '/agent-templates?search=a', setup: '1', create: '1' });
      const url = new URL(canonicalClusterPath('cluster/a', section, context), 'http://localhost');
      assert.equal(url.pathname, '/instances/cluster%2Fa/other-settings');
      assert.equal(url.searchParams.get('setting'), setting);
      assert.equal(url.searchParams.get('template'), 'agent/a');
      assert.equal(url.searchParams.get('returnTo'), '/agent-templates?search=a');
      assert.equal(url.searchParams.get('setup'), '1');
      assert.equal(url.searchParams.has('configTab'), false);
      assert.equal(url.searchParams.has('create'), false);
    }
    assert.equal(new URL(canonicalClusterPath('id', 'quota', new URLSearchParams(), false), 'http://localhost').searchParams.get('setting'), 'team-quota');
    assert.equal(new URL(canonicalClusterPath('id', 'quota', new URLSearchParams(), true), 'http://localhost').searchParams.get('setting'), 'workspace-quota');
  });
  test('settings refresh retains valid anchors and unrelated pages drop them',()=>{
    const context = new URLSearchParams({ setting: 'logging' });
    assert.equal(canonicalClusterPath('id', 'other-settings', context), '/instances/id/other-settings?setting=logging');
    assert.equal(canonicalClusterPath('id', 'status', context), '/instances/id/status');
    assert.equal(canonicalClusterPath('id', 'other-settings', new URLSearchParams({ setting: 'unknown' })), '/instances/id/other-settings');
    assert.equal(canonicalClusterPath('id', 'unknown', context), '/instances/id/cluster-config');
  });
  test('legacy expert bookmarks open the resource center with cluster and safe return context',()=>{
    const context = new URLSearchParams({template:'agent/a',returnTo:'/agent-templates?search=one',setup:'1',create:'1'});
    const url = new URL(canonicalClusterPath('cluster/a', 'expert-packages', context), 'http://localhost');
    assert.equal(url.pathname, '/expert-package-templates');
    assert.equal(url.searchParams.get('cluster'), 'cluster/a');
    assert.equal(url.searchParams.get('returnTo'), clusterPagePath('cluster/a', 'agent-resources', context));
    assert.equal(url.searchParams.has('create'), false);
    assert.equal(canonicalClusterSection('expert-packages'), null);
  });
  test('unsafe return URLs remain inside the Agent catalog',()=>{
    const url=new URL(clusterPagePath('id','access',new URLSearchParams({returnTo:'https://evil.invalid/'})),'http://localhost');
    assert.equal(url.searchParams.get('returnTo'),'/agent-templates');
  });
} finally { await server.close(); }
