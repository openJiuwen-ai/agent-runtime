import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';
const server = await createServer({ configFile:false, server:{middlewareMode:true,hmr:false,ws:false},appType:'custom' });
try {
  const {readAgentExpertDraft:read,writeAgentExpertDraft:write,normalizeAgentExpertSelections:normalize}=await server.ssrLoadModule('/src/utils/agentExpertDraft.ts');
  const {expertResourceReturnPath:returnPath}=await server.ssrLoadModule('/src/utils/expertResourceNavigation.ts');
  const memory=()=>{const data=new Map();return {data,getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value)};};
  test('expert preview survives reopening and isolates both accounts and templates',()=>{
    const storage=memory(), rows=[{kind:'agent_template',id:'expert-a',display_name:'A'}];
    assert.equal(write(storage,'admin','template-a',rows),true);
    assert.deepEqual(read(storage,'admin','template-a'),rows);
    assert.deepEqual(read(storage,'user','template-a'),[]);
    assert.deepEqual(read(storage,'admin','template-b'),[]);
    write(storage,'admin','template-b',rows);
    write(storage,'admin','template-a',[]);
    assert.deepEqual(read(storage,'admin','template-a'),[]);
    assert.deepEqual(read(storage,'admin','template-b'),rows);
  });
  test('sanitizing preserves kind and demo identity without retaining API grants or credentials',()=>{
    const rows=normalize([
      {kind:'agent_template',id:' a ',display_name:'Expert',version:'1',match_expr:'user_id in [admin]',client_secret:'secret'},
      {kind:'agent_template',id:'a'},
      {kind:'agent_group',id:'a'},
      {kind:'agent_template',id:'a',demo:true},
      {kind:'unknown',id:'bad'}, {kind:'agent_template',id:'../bad'}, null,
    ]);
    assert.deepEqual(rows,[{kind:'agent_template',id:'a',display_name:'Expert',version:'1'},{kind:'agent_group',id:'a'},{kind:'agent_template',id:'a',demo:true}]);
    assert.equal(JSON.stringify(rows).includes('secret'),false);
  });
  test('missing, invalid, future and unavailable preview storage is safe',()=>{
    const storage=memory();
    assert.deepEqual(read(storage,'admin','template-a'),[]);
    assert.equal(write(storage,'','template-a',[]),false);
    assert.equal(write({setItem(){throw new Error('quota')}},'admin','a',[]),false);
    assert.deepEqual(read({getItem(){throw new Error('blocked')}},'admin','a'),[]);
    for(const value of ['{','null','{"schema_version":2,"selections":[]}','{"schema_version":1,"selections":{}}']) {
      assert.deepEqual(read({getItem:()=>value},'admin','a'),[]);
    }
  });
  test('resource returns reject external or unrelated paths and consume automatic actions',()=>{
    assert.equal(returnPath('/agent-templates/tpl?create=1&returnTo=%2Fagent-templates'),'/agent-templates/tpl?returnTo=%2Fagent-templates');
    assert.equal(returnPath('/instances/c/agent-resources?template=abc&setup=1'),'/instances/c/agent-resources?template=abc&setup=1');
    assert.equal(returnPath('/agent-templates'),'/agent-templates');
    for (const path of ['https://evil.invalid','//evil.invalid','/\\evil.invalid','/users','/agent-templates/a/connect','/agent-templates/%5cevil','/agent-templates/a#hash']) assert.equal(returnPath(path),null);
  });
} finally { await server.close(); }
