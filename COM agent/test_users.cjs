// Async ownership regression tests; visual layout is verified in the browser.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');

async function app() {
  const elements = {};
  const html = fs.readFileSync(`${__dirname}/web/index.html`, 'utf8');
  for (const [, tag, id] of html.matchAll(/<([^>]*?)\bid="([^"]+)"[^>]*>/g)) {
    const listeners = {};
    elements[id] = {value:'', type:tag.includes('type="checkbox"') ? 'checkbox' : '', checked:false,
      hidden:false, innerHTML:'', textContent:'', listeners, classList:{toggle(){}},
      addEventListener(name, fn) { listeners[name] = fn; }, setCustomValidity(){}, setAttribute(name,value){this[name]=value;},
      querySelectorAll:() => id==='needs-content' ? [{dataset:{index:'0'},checked:true}] : [],
      checkValidity:() => true, reportValidity:() => true};
  }
  const requests = [];
  const context = vm.createContext({document:{getElementById:id => elements[id], querySelectorAll:() => []},
    crypto, clearTimeout:() => {}, setTimeout:() => {}, fetch:async (path, options) => {
      if (path === '/api/bootstrap') return {ok:true, json:async () => ({text:'营养目标：蛋白质补充。',today:'2026-10-11',demo_catalog:[],archive_catalog:[]})};
      if (path === '/api/users') return {ok:true,json:async()=>({users:[]})};
      if (path === '/api/tasks') return {ok:true,json:async()=>({tasks:[]})};
      let payload = JSON.parse(options.body);
      const task = path === '/api/tasks/start';
      if (task) { path=payload.action; payload=payload.request; }
      const reply = result => ({ok:true,json:async()=>task ? {id:'task-'+path,status:'done',action:path,started_at:new Date().toISOString(),result} : result});
      if (path === '/api/users/save' || path === '/api/catalogs/save') return {ok:true,json:async()=>({saved:true})};
      if (path === '/api/extract') return reply({status:'ready',summary:'测试需求',history_id:'extract-test',
        goals:[{name:'蛋白质补充',catalog_goal:'蛋白质补充',evidence:'测试原文依据'}],cautions:[],missing:[]});
      if (path === '/api/select') return reply({status:'ready',summary:'测试选品',history_id:'select-test',products:[
        {id:'DEMO-A',name:'虚构产品A',goals:['蛋白质补充'],eligible:true,reason:'虚构依据',missing:[],conflicts:[],evidence:[]}]});
      return new Promise(resolve => requests.push({payload, resolve:override => resolve(reply(override || ({
        evaluation:{verdict:'pass',summary:'测试复核通过',source:'测试',model:'测试模型',evaluated_at:'',checks:[]},
        history_id:'history-'+payload.case.profile.id, markdown:payload.case.profile.id, report:{profile:payload.case.profile, status:'needs_review',demo:true,days:30,
          selected_products:[],analysis:{summary:payload.case.text, goals:[],lifestyle:[],follow_up:[]},
          pricing:{},candidates:[],excluded_products:[],blocked:[],missing:[]}
      })))}));
    }});
  const run = code => vm.runInContext(code, context);
  run(fs.readFileSync(`${__dirname}/web/app.js`, 'utf8'));
  await new Promise(resolve => setImmediate(resolve));
  return {run, requests, elements, submit:async() => {
      if (run('state.currentUser.extraction?.status')!=='ready') await elements['case-form'].onsubmit({preventDefault(){}});
      if (!run('state.currentUser.selection')) await elements['case-form'].onsubmit({preventDefault(){},submitter:{id:'compose'}});
      return elements['case-form'].onsubmit({preventDefault(){},submitter:{id:'generate-report'}});
    },
    type(id, value, form='case-form') { elements[id].value = value; elements[form].listeners.input({target:{id}}); }};
}

test('switching users restores drafts and assigns a late report to its original user', async () => {
  const a = await app();
  a.type('analysis-text', '用户甲的独立分析'); a.type('budget','123');
  const generating = a.submit(); await new Promise(resolve => setImmediate(resolve));
  a.run('addUser()');
  assert.equal(a.elements['analysis-text'].value, '');
  a.type('analysis-text','用户乙的独立分析'); a.type('budget','456');
  a.requests[0].resolve(); await generating;
  assert.equal(a.run('state.result'), null);
  a.run('selectUser(state.users[0].key)');
  assert.equal(a.elements['analysis-text'].value, '用户甲的独立分析');
  assert.equal(a.elements.budget.value, '123');
  assert.equal(a.run('state.result.report.profile.id'), 'DEMO-USER-001');
  a.run('selectUser(state.users[1].key)');
  assert.equal(a.elements['analysis-text'].value, '用户乙的独立分析');
  assert.equal(a.elements.budget.value, '456');
  assert.equal(a.run('state.result'), null);
});

test('quote changes retain needs and selection; a late old quote cannot replace the new input', async () => {
  const a=await app();
  let generating=a.submit(); await new Promise(resolve=>setImmediate(resolve));
  a.requests[0].resolve(); await generating;
  const revision=a.run('state.currentUser.revision');
  a.type('shipping','10');
  assert.equal(a.run('state.currentUser.revision'),revision);
  assert.equal(a.run('needsReady()'),true);
  assert.equal(a.run('state.currentUser.selection.history_id'),'select-test');
  assert.equal(a.run('state.result'),null);
  assert.equal(a.run("state.currentUser.fields['reprice-task-id']"),'task-/api/compose');
  generating=a.submit(); await new Promise(resolve=>setImmediate(resolve));
  assert.equal(a.requests[1].payload.reprice_task_id,'task-/api/compose');
  assert.equal(a.requests[1].payload.case.shipping,10);
  a.type('shipping','20'); a.requests[1].resolve(); await generating;
  assert.equal(a.run('state.result'),null);
  a.type('medications','新的用药信息','profile-form');
  assert.equal(a.run('state.currentUser.selection'),null);
  assert.equal(a.run('needsReady()'),false);
});

test('unchecked needs persist across user switching and rendering', async () => {
  const a=await app(); await a.elements['case-form'].onsubmit({preventDefault(){}});
  a.elements['needs-content'].querySelectorAll=()=>[];
  a.elements['needs-content'].listeners.change();
  a.run('addUser(); selectUser(state.users[0].key)');
  assert.equal(a.run('checkedNeed(0)'),false);
  assert.equal(a.run("state.currentUser.fields['needs-choices']"),'[]');
  assert.doesNotMatch(a.elements['needs-content'].innerHTML,/data-index="0" checked/);
});

test('save conflict preserves the local draft and blocks accidental overwrites', async () => {
  const a=await app(); a.type('analysis-text','本窗口的草稿');
  a.run("fetch=async()=>({ok:false,status:409,json:async()=>({error:'资料已被其他窗口更新'})})");
  await assert.rejects(a.run('persistUser(state.currentUser)'),/其他窗口/);
  assert.equal(a.run('state.currentUser.saveConflict'),true);
  assert.equal(a.elements['analysis-text'].value,'本窗口的草稿');
  assert.equal(a.elements['download-draft'].hidden,false);
  assert.equal(a.elements['reload-user'].hidden,false);
  a.run("fetch=async()=>{throw new Error('不应再次请求')}");
  await assert.rejects(a.run('persistUser(state.currentUser)'),/其他窗口/);
});

test('first submit only extracts needs; selection is required before composing', async () => {
  const a = await app();
  await a.elements['case-form'].onsubmit({preventDefault(){}});
  assert.equal(a.requests.length,0);
  assert.equal(a.elements['needs-panel'].hidden,false);
  assert.match(a.elements.compose.innerHTML,/确认需求并选品/);
  a.elements['case-form'].listeners.input({target:{closest:()=>a.elements['needs-panel']}});
  assert.equal(a.run('state.currentUser.extraction.status'),'ready');
  a.elements['needs-content'].querySelectorAll = () => [];
  await a.elements['case-form'].onsubmit({preventDefault(){}});
  assert.equal(a.requests.length,0);
  assert.match(a.elements['form-error'].textContent,/至少一个/);
  a.type('analysis-text','修改后的专业分析');
  assert.equal(a.run('state.currentUser.extraction.stale'),true);
  assert.equal(a.elements['needs-panel'].hidden,false);
  assert.match(a.elements.generate.innerHTML,/提取健康需求/);
});

test('a late extraction stays with its original user and is marked stale after revision changes', async () => {
  for (const changed of [false,true]) {
    const a = await app();
    a.run('fetch = async () => new Promise(resolve => globalThis.finishExtract = resolve)');
    a.run('persistUser = async () => {}');
    const extracting = a.elements['case-form'].onsubmit({preventDefault(){}});
    await new Promise(resolve=>setImmediate(resolve));
    if (changed) a.type('analysis-text','修改后的分析');
    else a.run('addUser()');
    a.run("finishExtract({ok:true,json:async()=>({id:'extract-task',status:'done',result:{status:'ready',summary:'原用户需求',goals:[],cautions:[],missing:[]}})})");
    await extracting;
    if (changed) {
      assert.equal(a.run('state.currentUser.extraction.stale'),true);
      assert.equal(a.run('state.currentUser.extraction.summary'),'原用户需求');
    } else assert.equal(a.run('state.currentUser.extraction ?? null'),null);
    if (!changed) {
      a.run('selectUser(state.users[0].key)');
      assert.equal(a.run('state.currentUser.extraction.summary'),'原用户需求');
    }
  }
});

test('an outdated response cannot restore a report after profile changes', async () => {
  const a = await app();
  const generating = a.submit(); await new Promise(resolve => setImmediate(resolve));
  a.type('age','46','profile-form');
  a.requests[0].resolve(); await generating;
  assert.equal(a.run('state.currentUser.result'), null);
  assert.equal(a.elements['report-body'].hidden, true);
  assert.equal(a.elements.generate.disabled, false);
});

test('loading the demo keeps another user draft intact; catalog updates invalidate all affected reports', async () => {
  const a = await app();
  let generating = a.submit(); await new Promise(resolve => setImmediate(resolve)); a.requests[0].resolve(); await generating;
  a.run('addUser()'); a.type('analysis-text','用户乙的分析');
  generating = a.submit(); await new Promise(resolve => setImmediate(resolve)); a.requests[1].resolve(); await generating;
  const secondId = a.requests[1].payload.case.profile.id;
  assert.notEqual(secondId, 'DEMO-USER-001');
  a.elements['load-demo'].onclick();
  a.run('selectUser(state.users[1].key)');
  assert.equal(a.elements['analysis-text'].value, '用户乙的分析');
  assert.equal(a.run('state.result.report.profile.id'), secondId);
  a.run("invalidateCatalog('demo')");
  assert.equal(a.run('state.users.every(u => u.result === null)'), true);
});

test('a late text import updates its original user without replacing the selected user input', async () => {
  const a = await app(); let finish;
  const importing = a.elements['text-file'].onchange({target:{files:[{text:() => new Promise(resolve => finish=resolve)}]}});
  a.run('addUser()'); a.type('analysis-text','用户乙的分析');
  finish('用户甲导入的分析'); await importing;
  assert.equal(a.elements['analysis-text'].value, '用户乙的分析');
  a.run('selectUser(state.users[0].key)');
  assert.equal(a.elements['analysis-text'].value, '用户甲导入的分析');
});

test('failed evaluation hides the report and remains assigned to its original user', async () => {
  const a = await app();
  a.type('forbidden-products','DEMO-A','profile-form');
  const generating = a.submit(); await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(a.requests[0].payload.case.profile.forbidden_products, ['DEMO-A']);
  a.run('addUser()');
  a.requests[0].resolve({report:null,markdown:null,evaluation:{verdict:'block',summary:'禁用商品测试',source:'测试',model:'测试',evaluated_at:'',checks:[]}});
  await generating;
  assert.equal(a.run('state.result'), null);
  a.run('selectUser(state.users[0].key)');
  assert.equal(a.elements['report-actions'].hidden, true);
  assert.match(a.elements['report-body'].innerHTML, /禁用商品测试/);
  assert.equal(a.elements['forbidden-products'].value, 'DEMO-A');
});

test('a saved narrative edit remains with its original user after switching users', async () => {
  const a = await app();
  const generated = a.submit(); await new Promise(resolve=>setImmediate(resolve)); a.requests[0].resolve(); await generated;
  const original = structuredClone(a.run('state.result'));
  a.elements['edit-report'].onclick(); a.elements['report-summary'].value = '人工编辑的摘要';
  const saving = a.elements['report-editor'].onsubmit({preventDefault(){}}); await new Promise(resolve=>setImmediate(resolve));
  assert.equal(a.requests[1].payload.changes.summary,'人工编辑的摘要');
  assert.equal(a.requests[1].payload.history_id,original.history_id);
  assert.deepEqual(Object.keys(a.requests[1].payload.changes).sort(),['follow_up','lifestyle','rationales','summary']);
  a.run('addUser()');
  const edited = {...original,history_id:'edited-history',report:{...original.report,analysis:{...original.report.analysis,summary:'人工编辑的摘要'},edited_at:'测试日期'}};
  a.requests[1].resolve(edited); await saving;
  assert.equal(a.run('state.result'),null);
  a.run('selectUser(state.users[0].key)');
  assert.equal(a.run('state.result.report.analysis.summary'),'人工编辑的摘要');
  assert.match(a.elements['report-body'].innerHTML,/人工编辑的摘要/);
});

test('warning drafts show reminders and support editing, downloads and printing', async () => {
  const a = await app();
  const generating = a.submit(); await new Promise(resolve=>setImmediate(resolve)); a.requests[0].resolve(); await generating;
  a.run(`state.result.evaluation.verdict='needs_review'; state.result.report.warnings=['缺少膳食评估，合用依据待确认'];
    state.result.markdown='组合草案：缺少膳食评估，合用依据待确认'; renderReport();
    globalThis.downloads=[]; globalThis.prints=0;
    globalThis.Blob=class {constructor(data){downloads.push(data)}};
    globalThis.URL={createObjectURL:()=>'',revokeObjectURL:()=>{}};
    document.createElement=()=>({click(){}}); globalThis.window={print(){prints++}};`);
  assert.equal(a.elements['report-state'].textContent,'草案 · 有提醒');
  assert.equal(a.elements['report-actions'].hidden,false);
  assert.equal(a.elements['edit-report'].hidden,false);
  assert.match(a.elements['report-body'].innerHTML,/组合提醒（待确认）/);
  assert.match(a.elements['report-body'].innerHTML,/缺少膳食评估/);
  assert.doesNotMatch(a.elements['report-body'].innerHTML,/未输出组合/);
  a.elements['download-md'].onclick(); a.elements['download-json'].onclick(); a.elements['print'].onclick();
  assert.equal(a.run('downloads.length'),2); assert.equal(a.run('prints'),1);
  assert.match(a.run('downloads[0][0]'),/合用依据待确认/);
  assert.match(a.run('downloads[1][0]'),/缺少膳食评估/);
  const edited=structuredClone(a.run('state.result')); edited.history_id='warning-edit';
  a.elements['edit-report'].onclick();
  const saving=a.elements['report-editor'].onsubmit({preventDefault(){}}); await new Promise(resolve=>setImmediate(resolve));
  a.requests[1].resolve(edited); await saving;
  assert.equal(a.run('state.result.history_id'),'warning-edit');
  assert.equal(a.elements['report-editor'].hidden,true);
});

test('cancel and failed edit review both preserve the original report', async () => {
  const a = await app();
  const generated=a.submit(); await new Promise(resolve=>setImmediate(resolve)); a.requests[0].resolve(); await generated;
  const original=a.run('state.result');
  a.elements['edit-report'].onclick(); a.elements['report-summary'].value='未保存内容';
  a.elements['cancel-report-edit'].onclick();
  assert.equal(a.run('state.result'),original); assert.equal(a.elements['report-editor'].hidden,true);
  a.elements['edit-report'].onclick();
  const saving=a.elements['report-editor'].onsubmit({preventDefault(){}}); await new Promise(resolve=>setImmediate(resolve));
  a.requests[1].resolve({report:null,evaluation:{verdict:'block',summary:'摘要与原专业分析冲突'}}); await saving;
  assert.equal(a.run('state.result'),original);
  assert.equal(a.elements['report-editor'].hidden,false);
  assert.match(a.elements['report-edit-error'].textContent,/原报告已保留/);
  assert.equal(a.elements['save-report'].disabled,false);
});


test('matching evidence is collapsed, escaped and retained when no report can be issued', async () => {
  const a = await app();
  const matching = {summary:'需核对产品资料',source:'测试模型',model:'test-model',cached:true,excluded_products:[{id:'BANNED'}],
    products:[{name:'产品<script>',status:'related',reason:'有依据',missing:['现售标签'],conflicts:[],
      evidence:[{field:'raw.product_description',quote:'原文<b>依据'}]},
      {name:'未召回商品',status:'not_retrieved',reason:'未召回',missing:[],conflicts:[],evidence:[]}]};
  const rendered = a.run(`matchingHTML(${JSON.stringify(matching)})`);
  assert.match(rendered,/产品关联判断 · 1款有关联 · 0款待核验/);
  assert.match(rendered,/复用相同资料的关联结果/);
  assert.match(rendered,/缺少：现售标签/);
  assert.match(rendered,/产品&lt;script&gt;/);
  assert.match(rendered,/原文&lt;b&gt;依据/);
  assert.match(rendered,/1款未召回，未召回不等于不适用；1款禁用/);
  assert.doesNotMatch(rendered,/<details[^>]*\bopen\b/);
  a.run(`state.result = {matching:${JSON.stringify(matching)},report:null,evaluation:{verdict:'needs_review',summary:'缺少资料',source:'规则',model:'test',evaluated_at:'',checks:[]}}; renderReport()`);
  assert.match(a.elements['report-body'].innerHTML,/产品关联判断/);
  assert.equal(a.elements['report-actions'].hidden,true);
});


test('editing during extraction keeps the draft and offers reuse or a fresh extraction', async () => {
  const a = await app();
  a.type('analysis-text','虚构专业分析：蛋白质补充'); a.type('budget','280'); a.type('reviewer','测试审核人');
  a.run("const originalFetch = fetch; fetch = (path, options) => path === '/api/tasks/start' ? new Promise(resolve => globalThis.finishExtract = resolve) : originalFetch(path,options)");
  const extracting = a.elements['case-form'].onsubmit({preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  a.run("selectUser(state.currentUser.key, 'user-profile')");
  a.type('age','46','profile-form');
  a.elements['profile-form'].onsubmit({preventDefault(){}});
  a.run("finishExtract({ok:true,json:async()=>({id:'extract-task',status:'done',result:{status:'ready',summary:'原资料提取结果',goals:[{name:'蛋白质补充',evidence:'蛋白质补充'}],cautions:[],missing:[]}})})");
  await extracting;
  a.run('selectUser(state.currentUser.key)');
  assert.equal(a.elements['analysis-text'].value,'虚构专业分析：蛋白质补充');
  assert.equal(a.elements.budget.value,'280');
  assert.equal(a.elements.reviewer.value,'测试审核人');
  assert.equal(a.elements.age.value,'46');
  assert.equal(a.run('state.currentUser.extraction.summary'),'原资料提取结果');
  assert.equal(a.run('state.currentUser.extraction.stale'),true);
  assert.match(a.elements['needs-content'].innerHTML,/资料已修改/);
  assert.match(a.elements['needs-content'].innerHTML,/disabled/);
  assert.match(a.elements.generate.innerHTML,/提取健康需求/);
  assert.equal(a.elements['reuse-needs'].hidden,false);
  a.run('fetch = originalFetch');
  await a.elements['case-form'].onsubmit({preventDefault(){}});
  assert.equal(a.requests.length,0);
  assert.equal(a.run('state.currentUser.extraction.stale ?? false'),false);
});

test('editing after extraction permits explicitly reusing needs with the updated profile', async () => {
  const a = await app();
  await a.elements['case-form'].onsubmit({preventDefault(){}});
  a.run("selectUser(state.currentUser.key, 'user-profile')");
  a.type('medications','虚构药物','profile-form');
  a.elements['profile-form'].onsubmit({preventDefault(){}});
  assert.equal(a.run('state.currentUser.extraction.summary'),'测试需求');
  assert.equal(a.run('state.currentUser.extraction.stale'),true);
  assert.equal(a.elements['needs-panel'].hidden,false);
  assert.match(a.elements.generate.innerHTML,/提取健康需求/);
});


test('explicit reuse sends the current profile and extraction id, and another edit requires a new choice', async () => {
  const a = await app();
  await a.elements['case-form'].onsubmit({preventDefault(){}});
  a.type('age','46','profile-form');
  a.type('forbidden-products','DEMO-A','profile-form');
  assert.equal(a.elements['reuse-needs'].hidden,false);
  a.elements['reuse-needs'].onclick();
  assert.match(a.elements.compose.innerHTML,/确认需求并选品/);
  const composing = a.submit();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(a.requests[0].payload.reuse_extraction,true);
  assert.equal(a.requests[0].payload.extraction_id,'extract-test');
  assert.equal(a.requests[0].payload.case.profile.age,46);
  assert.deepEqual(a.requests[0].payload.case.profile.forbidden_products,['DEMO-A']);
  a.requests[0].resolve(); await composing;
  a.type('age','47','profile-form');
  assert.equal(a.elements['reuse-needs'].hidden,false);
  assert.match(a.elements.generate.innerHTML,/提取健康需求/);
});


test('steps reveal composition only after extraction and retain input on back navigation', async () => {
  const a = await app();
  assert.equal(a.elements['analysis-step'].hidden,false);
  assert.equal(a.elements['needs-step'].hidden,true);
  assert.equal(a.elements['report-step'].hidden,true);
  assert.equal(a.elements['step-needs'].disabled,true);
  assert.equal(a.elements['step-report'].disabled,true);
  a.type('analysis-text','虚构分析原文');
  await a.elements['case-form'].onsubmit({preventDefault(){},submitter:{id:'generate'}});
  assert.equal(a.elements['analysis-step'].hidden,true);
  assert.equal(a.elements['needs-step'].hidden,false);
  assert.equal(a.elements['report-step'].hidden,true);
  assert.equal(a.elements.compose.disabled,false);
  assert.equal(a.elements['step-needs'].disabled,false);
  a.elements['back-analysis'].onclick();
  assert.equal(a.elements['analysis-step'].hidden,false);
  assert.equal(a.elements['analysis-text'].value,'虚构分析原文');
  assert.match(a.elements.generate.innerHTML,/提取健康需求/);
  await a.elements['case-form'].onsubmit({preventDefault(){},submitter:{id:'generate'}});
  assert.equal(a.requests.length,0);
  await a.elements['case-form'].onsubmit({preventDefault(){},submitter:{id:'compose'}});
  assert.equal(a.elements['selection-step'].hidden,false);
  assert.equal(a.elements['report-step'].hidden,true);
  assert.equal(a.requests.length,0);
  await a.elements['case-form'].onsubmit({preventDefault(){}});
  assert.equal(a.requests.length,0);
  a.elements['selection-products'].querySelectorAll = () => [];
  a.elements['selection-products'].listeners.change();
  assert.equal(a.elements['generate-report'].disabled,true);
  a.run('delete state.currentUser.selection.checkedProducts; renderSelection()');
  assert.equal(a.elements['generate-report'].disabled,true);
  a.elements['selection-products'].querySelectorAll = () => [{dataset:{id:'DEMO-A'}}];
  a.elements['selection-products'].listeners.change();
  assert.equal(a.elements['generate-report'].disabled,false);
  const composing = a.elements['case-form'].onsubmit({preventDefault(){},submitter:{id:'generate-report'}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(a.requests[0].payload.selection_id,'select-test');
  assert.deepEqual(a.requests[0].payload.confirmed_products,['DEMO-A']);
  assert.equal(a.elements['case-form'].hidden,true);
  assert.equal(a.elements['report-step'].hidden,false);
  a.requests[0].resolve(); await composing;
  a.elements['report-back-needs'].onclick();
  assert.equal(a.elements['selection-step'].hidden,false);
  a.elements['selection-back-needs'].onclick();
  assert.equal(a.elements['case-form'].hidden,false);
  assert.equal(a.elements['needs-step'].hidden,false);
  assert.equal(a.elements['analysis-text'].value,'虚构分析原文');
});
