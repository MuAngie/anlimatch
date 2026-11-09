const $ = id => document.getElementById(id);
let state = { catalogs: { demo: [], archive: [], imported: [] }, users: [], currentUser: null, result: null, editing: null, today: '' };
const userFields = ['user-name','user-id','age','pregnancy','allergies','conditions','medications','supplements','forbidden-products','analysis-text','reviewer','review-date','analysis-approved','days','budget','shipping','discount','catalog-select'];
const escapeHTML = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = x => x == null ? '待确认' : Number(x).toLocaleString('zh-CN', {maximumFractionDigits: 2});
const price = x => x == null ? '待报价' : `¥${Number(x).toFixed(2)}`;
const split = value => !value.trim() ? null : value.trim() === '无' ? [] : value.split(/[；;,，]/).map(s => s.trim()).filter(Boolean);
const num = id => $(id).value === '' ? null : Number($(id).value);
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; setTimeout(() => $('toast').hidden = true, 3500); }
function error(id, message) { $(id).textContent = message; $(id).hidden = !message; }
async function api(path, body) {
  const response = await fetch(path, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {});
  const data = await response.json(); if (!response.ok) { const failure = new Error(data.error || '操作失败'); failure.conflict = response.status === 409; throw failure; } return data;
}
const taskNames = {starting:'正在检查资料',extract:'正在提取健康需求',select:'正在选品',compose:'正在生成搭配依据',review:'正在复核报告',model:'正在调用模型'};
function taskStatus() {
  const user=state.currentUser, task=user?.task;
  $('task-panel').hidden=!task || (task.status==='done' && !user.taskConnectionError);
  if (!task) return;
  $('task-stage').textContent=task.status==='running' ? taskNames[task.stage] || '正在处理' : task.status==='cancelled' ? '任务已取消' : task.status==='interrupted' ? '任务已中断' : '此步骤未完成';
  const end=task.finished_at ? Date.parse(task.finished_at) : Date.now();
  $('task-time').textContent='已用 '+Math.max(0,Math.floor((end-Date.parse(task.started_at))/1000))+' 秒';
  $('task-message').textContent=user.taskConnectionError || task.error || task.result?.evaluation?.summary || task.result?.summary || '可以切换用户；刷新后可继续查看任务。';
  $('cancel-task').hidden=task.status!=='running';
  $('retry-task').hidden=!task.can_retry;
  $('retry-task').disabled=!!user.pending || !!user.saveConflict;
  $('retry-task').textContent=task.retry_review ? '仅重试复核' : '重试当前步骤';
  $('refresh-task').hidden=!user.taskConnectionError;
}
async function watchTask(user, task) {
  user.task=task; user.taskConnectionError=''; taskStatus();
  while (task.status==='running') {
    await new Promise(resolve=>setTimeout(resolve,1000));
    try { task=await api('/api/tasks?id='+encodeURIComponent(task.id)); }
    catch(e) { user.taskConnectionError='连接中断，任务仍在后台。点击重新连接任务。'; taskStatus(); throw e; }
    user.task=task; taskStatus();
  }
  if (task.result) return {...task.result,task_id:task.id};
  throw new Error(task.error || (task.status==='cancelled' ? '任务已取消' : '任务未完成'));
}
async function runTask(user, action, request) {
  const task=await api('/api/tasks/start',{action,request});
  return watchTask(user,task);
}
async function resumeTask(user, retry=false) {
  if (user.pending || !user.task) return;
  if (state.currentUser===user) saveUser();
  const revision=user.revision, snapshot=userSnapshot(user);
  user.pending=true; user.pendingStage=user.task.action.split('/').pop(); generationState();
  try {
    await persistUser(user);
    const task=retry ? await api('/api/tasks/retry',{id:user.task.id}) : await api('/api/tasks?id='+encodeURIComponent(user.task.id));
    const result=await watchTask(user,task);
    if (task.action==='/api/extract') { user.extraction={...result,stale:user.revision!==(task.user_revision ?? revision)}; user.selection=null; }
    else if (userSnapshot(user)!==snapshot) return;
    else if (task.action==='/api/select') {
      user.selection=result;
      if (result.status==='ready' && user.fields['catalog-invalidated']==='true') { user.fields['catalog-invalidated']=''; scheduleSave(user); }
    }
    else user.result=result;
    if (state.currentUser===user) { state.result=user.result; renderNeeds(); renderSelection(); if (user.result) renderReport(); else workspaceStep(user.selection ? 'selection' : 'needs'); }
  } catch(e) { user.taskConnectionError=user.task?.status==='running' ? e.message : ''; if (state.currentUser===user) toast(e.message); }
  finally { user.pending=false; user.pendingStage=''; if (state.currentUser===user) renderSelection(); generationState(); }
}
$('cancel-task').onclick=async()=>{
  const user=state.currentUser;
  try { user.task=await api('/api/tasks/cancel',{id:user.task.id}); taskStatus(); }
  catch(e){toast(e.message);}
};
$('retry-task').onclick=()=>resumeTask(state.currentUser,true);
$('refresh-task').onclick=()=>resumeTask(state.currentUser);
async function restoreTasks() {
  const data=await api('/api/tasks');
  for (const task of data.tasks || []) {
    const user=state.users.find(u=>u.key===task.user_key); if (!user) continue;
    user.task=task;
    if (task.status==='running') resumeTask(user);
  }
  taskStatus();
}
function view(name) {
  for (const el of document.querySelectorAll('.view')) el.hidden = el.id !== name;
  for (const el of document.querySelectorAll('.nav')) el.classList.toggle('active', el.dataset.view === name);
  $('crumb').textContent = {workspace:'组合工作台',catalog:'产品资料库',method:'配比说明','user-profile':'用户信息'}[name];
  if (name === 'catalog') { $('catalog-view-select').value = $('catalog-select').value; renderCatalog(); }
}
document.querySelectorAll('[data-view]').forEach(el => el.addEventListener('click', () => view(el.dataset.view)));
function workspaceStep(step) {
  $('analysis-step').hidden = step !== 'analysis'; $('needs-step').hidden = step !== 'needs';
  $('case-form').hidden = step === 'report' || step === 'selection'; $('report-step').hidden = step !== 'report'; $('selection-step').hidden = step !== 'selection';
  for (const name of ['analysis','needs','selection','report']) $('step-' + name).setAttribute('aria-current', name === step ? 'step' : 'false');
}
$('step-analysis').onclick = $('back-analysis').onclick = () => workspaceStep('analysis');
$('step-needs').onclick = $('selection-back-needs').onclick = () => workspaceStep('needs');
$('step-selection').onclick = $('back-needs').onclick = $('report-back-needs').onclick = () => workspaceStep('selection');
$('step-report').onclick = () => workspaceStep('report');
function invalidate(quoteOnly = false) {
  const user = state.currentUser;
  if (!user) return;
  saveUser(); if (!quoteOnly) user.revision++;
  if (quoteOnly && user.result?.task_id) user.fields['reprice-task-id'] = user.result.task_id;
  if (!quoteOnly) { user.selection = null; delete user.fields['reprice-task-id']; }
  user.result = null; if (!quoteOnly && user.extraction) { user.extraction.stale = true; user.extraction.reuseApproved = false; } user.error = ''; state.result = null;
  renderNeeds(); generationState();
  scheduleSave(user); resetReport(quoteOnly ? '费用或周期已修改，请重新核算并复核' : '输入已更新');
  if (!$('report-step').hidden) workspaceStep(user.extraction ? 'needs' : 'analysis');
}
$('case-form').addEventListener('input', event => { if (!event?.target?.closest?.('#needs-panel')) invalidate(['days','budget','shipping','discount'].includes(event?.target?.id)); });
$('catalog-select').addEventListener('change', () => { invalidate(); libraryHint(); });
function saveUser() {
  if (!state.currentUser) return;
  for (const id of userFields) state.currentUser.fields[id] = $(id).type === 'checkbox' ? $(id).checked : $(id).value;
  scheduleSave(state.currentUser);
}
function userSnapshot(user) { return JSON.stringify({fields:user.fields,revision:user.revision,demo:user.demo}); }
function saveStatus() {
  const u = state.currentUser; if (!u) return;
  $('save-status').textContent = u.saveError || (u.savingCount ? '正在保存…' : u.savedSnapshot === userSnapshot(u) ? '已保存' : '有未保存修改');
  $('save-status').className = u.saveError ? 'save-error' : '';
  $('retry-save').hidden = !u.saveError || !!u.saveConflict;
  $('reload-user').hidden = !u.saveConflict;
  $('download-draft').hidden = !u.saveError;
}
function scheduleSave(user) {
  clearTimeout(user.saveTimer);
  if (!user.saveConflict && user.savedSnapshot !== userSnapshot(user))
    user.saveTimer = setTimeout(() => persistUser(user).catch(() => {}), 500);
  saveStatus();
}
function persistUser(user) {
  clearTimeout(user.saveTimer);
  const payload = {key:user.key, demo:user.demo, fields:{...user.fields}, revision:user.revision};
  const snapshot = userSnapshot(user);
  user.savingCount = (user.savingCount || 0)+1; saveStatus();
  user.saving = (user.saving || Promise.resolve()).catch(() => {}).then(async () => {
    if (user.saveConflict) throw Object.assign(new Error(user.saveError),{conflict:true});
    if (user.savedSnapshot === snapshot) return;
    const data = await api('/api/users/save', {...payload,expected_version:user.save_version || 0});
    if (data.saved !== true) throw Object.assign(new Error('资料已被其他窗口更新，请先保留草稿再载入最新资料'),{conflict:true});
    user.save_version = data.save_version ?? user.save_version ?? 0;
    user.savedSnapshot = snapshot; user.saveError = ''; user.saveConflict = false;
  }).catch(e => { user.saveError = e.message; user.saveConflict = !!e.conflict; throw e;
  }).finally(() => { user.savingCount--; saveStatus(); });
  return user.saving;
}
$('retry-save').onclick = () => persistUser(state.currentUser).catch(() => {});
$('download-draft').onclick = () => download('用户未保存草稿.json',userSnapshot(state.currentUser),'application/json');
$('reload-user').onclick = async () => {
  const user = state.currentUser;
  try {
    const saved = (await api('/api/users')).users.find(u=>u.key===user.key);
    if (!saved) throw new Error('未找到已保存用户');
    clearTimeout(user.saveTimer);
    Object.assign(user,saved,{saveError:'',saveConflict:false}); user.savedSnapshot = userSnapshot(user);
    if (state.currentUser===user) { state.currentUser=null; selectUser(user.key); }
  } catch(e) { user.saveError=e.message; saveStatus(); }
};
if (typeof window !== 'undefined') window.addEventListener('beforeunload', event => {
  if (state.users.some(u => u.savedSnapshot !== userSnapshot(u) || u.savingCount)) { event.preventDefault(); event.returnValue=''; }
});
async function persistCatalog(kind) {
  await api('/api/catalogs/save', {kind, products:state.catalogs[kind]});
}
function renderUsers() {
  $('user-list').innerHTML = ['我的用户','示例用户'].map(group => {
    const users = state.users.filter(u => (u.demo ? '示例用户' : '我的用户') === group);
    if (!users.length) return '';
    return `<div class="user-group">${group}</div>` + users.map(u => `<div class="user-row ${u === state.currentUser ? 'selected' : ''}"><button class="select-user" data-user="${u.key}" aria-pressed="${u === state.currentUser}"><strong>${escapeHTML(u.fields['user-name'] || '未命名用户')}</strong><small>${escapeHTML(u.fields['user-id'] || '编号待填写')}</small></button><button class="edit-user" data-user="${u.key}" aria-label="编辑${escapeHTML(u.fields['user-name'] || '用户')}的信息">编辑</button></div>`).join('');
  }).join('');
  document.querySelectorAll('.select-user').forEach(el => el.onclick = () => selectUser(el.dataset.user));
  document.querySelectorAll('.edit-user').forEach(el => el.onclick = () => selectUser(el.dataset.user, 'user-profile'));
}
function validateUser() {
  const id = $('user-id').value.trim();
  $('user-id').setCustomValidity(!id ? '请填写用户编号' : state.users.some(u => u !== state.currentUser && u.fields['user-id'].trim() === id) ? '用户编号已存在，请使用不同编号' : '');
  $('user-name').setCustomValidity($('user-name').value.trim() ? '' : '请填写用户姓名或称呼');
}
function resetReport(label = '等待生成') {
  closeReportEditor(); $('edit-report').hidden = true;
  $('report-actions').hidden = true; $('report-body').hidden = true; $('report-body').innerHTML = '';
  $('report-placeholder').hidden = false; $('report-state').textContent = label; $('report-state').className = 'badge neutral';
}
function needsReady(user = state.currentUser) { return user?.extraction?.status === 'ready' && (!user.extraction.stale || user.extraction.reuseApproved); }
function generationState() {
  const busy=!!state.currentUser?.pending || state.currentUser?.task?.status==='running';
  $('generate').disabled = busy;
  $('retry-needs').disabled = busy;
  $('reuse-needs').disabled = busy;
  $('compose').disabled = busy || !needsReady();
  $('generate-report').disabled = busy || state.currentUser?.selection?.status !== 'ready' || !state.currentUser.selection.checkedProducts?.length;
  $('retry-selection').disabled = busy;
  $('step-selection').disabled = !state.currentUser?.selection && state.currentUser?.pendingStage !== 'select';
  $('step-needs').disabled = !state.currentUser?.extraction;
  saveStatus(); taskStatus();
  $('step-report').disabled = !state.currentUser?.result && state.currentUser?.pendingStage !== 'compose';
  $('generate').innerHTML = state.currentUser?.pendingStage === 'extract' ? '正在提取健康需求…' : '提取健康需求 <span>→</span>';
  $('compose').innerHTML = state.currentUser?.pendingStage === 'select' ? '正在选品…' : state.currentUser?.selection?.status === 'ready' ? '查看现有选品 <span>→</span>' : '确认需求并选品 <span>→</span>';
  $('generate-report').textContent = state.currentUser?.pendingStage === 'compose' ? '正在生成报告并复核…' : '确认选品并生成完整报告 →';
  $('report-wait-title').textContent = state.currentUser?.pendingStage === 'compose' ? '正在生成组合报告' : '暂无报告';
  $('report-wait-description').textContent = state.currentUser?.pendingStage === 'compose' ? '正在根据已确认商品生成组合、计算用量和报价，并进行复核。' : '确认选品后生成完整报告。';
}
function renderSelection() {
  const selection = state.currentUser?.selection;
  $('selection-state').textContent = state.currentUser?.pendingStage === 'select' ? '正在选品' : ({ready:'请确认商品',needs_review:'资料待补充',block:'存在冲突',error:'选品调用失败'}[selection?.status] || '等待选品');
  $('selection-summary').textContent = state.currentUser?.pendingStage === 'select' ? '正在批量判断产品关联。完成后先展示结果，确认后再生成报告。' : selection?.summary || '';
  if (selection && !selection.checkedProducts) {
    const eligible = selection.products.filter(p=>p.eligible).map(p=>p.id);
    selection.checkedProducts = eligible;
    if (state.currentUser.fields['selection-choice-id'] === selection.history_id) {
      try {
        const saved = JSON.parse(state.currentUser.fields['selection-choices']);
        if (Array.isArray(saved)) selection.checkedProducts = saved.filter(id=>eligible.includes(id));
      } catch {}
    }
  }
  $('selection-products').innerHTML = (selection?.products || []).map(p=>`<article class="report-section"><label class="checkbox"><input class="product-choice" type="checkbox" data-id="${escapeHTML(p.id)}" ${p.eligible ? (selection.checkedProducts.includes(p.id) ? 'checked' : '') : 'disabled'} ${state.currentUser.pending ? 'disabled' : ''}><strong>${escapeHTML(p.name)}</strong></label><p>${escapeHTML(p.goals.join('、'))} · ${p.eligible ? '可进入配比' : '待核验'}</p><p>${escapeHTML(p.reason)}</p><p class="field-help">标签日量：${p.daily_units == null ? '待确认' : fmt(p.daily_units)+escapeHTML(p.unit || '')} · 单包装参考价：${price(p.price?.amount)}</p>${p.missing.length ? `<p class="notice">缺少：${escapeHTML(p.missing.join('、'))}</p>` : ''}${p.conflicts.map(c=>`<p class="error">${escapeHTML(c)}</p>`).join('')}<details><summary>查看选品依据</summary>${p.evidence.map(e=>`<p class="field-help">${escapeHTML(e.quote)}</p>`).join('')}</details></article>`).join('');
  generationState();
}
$('selection-products').addEventListener('change', () => {
  state.currentUser.selection.checkedProducts = [...$('selection-products').querySelectorAll('.product-choice:checked')].map(el=>el.dataset.id);
  state.currentUser.fields['selection-choice-id'] = state.currentUser.selection.history_id;
  state.currentUser.fields['selection-choices'] = JSON.stringify(state.currentUser.selection.checkedProducts);
  scheduleSave(state.currentUser);
  delete state.currentUser.fields['reprice-task-id']; state.currentUser.result = null; state.result = null; resetReport('选品已调整'); generationState();
});
$('retry-selection').onclick = () => { state.currentUser.selection=null; delete state.currentUser.fields['reprice-task-id']; workspaceStep('needs'); $('case-form').requestSubmit($('compose')); };
$('needs-content').addEventListener('change', () => {
  const user=state.currentUser;
  user.fields['needs-choice-id']=user.extraction.history_id;
  user.fields['needs-choices']=JSON.stringify([...$('needs-content').querySelectorAll('.need-choice:checked')].map(el=>Number(el.dataset.index)));
  user.selection=null; user.result=null; state.result=null; delete user.fields['reprice-task-id'];
  scheduleSave(user); generationState();
});
function checkedNeed(index) {
  const u=state.currentUser;
  if (u.fields['needs-choice-id']===u.extraction?.history_id) {
    try { return JSON.parse(u.fields['needs-choices']).includes(index); } catch {}
  }
  return !u.selection?.confirmed_goals || u.selection.confirmed_goals.includes(index);
}
function renderNeeds() {
  const needs = state.currentUser?.extraction;
  $('needs-panel').hidden = !needs;
  $('reuse-needs').hidden = !needs?.stale || needs.status !== 'ready' || !!needs.reuseApproved;
  if (!needs) { $('needs-content').innerHTML = ''; return; }
  $('needs-content').innerHTML = (needs.stale ? `<p class="notice">资料已修改，原提取内容已保留。${needs.reuseApproved ? '已选择沿用需求，将按最新用户信息重新选品、配比和复核。' : '可选择沿用已提取需求，或重新提取。'}</p>` : '') + `<p>${escapeHTML(needs.summary || needs.error || '需求提取未完成')}</p>` + (needs.goals || []).map((g,i)=>{
    return `<label class="checkbox"><input class="need-choice" type="checkbox" data-index="${i}" ${needsReady() ? (checkedNeed(i) ? 'checked' : '') : 'disabled'}><span><strong>${escapeHTML(g.name)}</strong><small class="field-help">原文依据：${escapeHTML(g.evidence)}</small><small class="field-help">后续结合产品成分、功能描述和依据判断关联，不要求目标标签同名。</small></span></label>`;
  }).join('') + [...(needs.cautions || []), ...(needs.missing || [])].map(s=>`<p class="field-help">${escapeHTML(s)}</p>`).join('');
}
$('reuse-needs').onclick = () => { state.currentUser.extraction.reuseApproved = true; renderNeeds(); generationState(); resetReport('请确认沿用的需求'); };
$('retry-needs').onclick = () => { workspaceStep('analysis'); $('case-form').requestSubmit($('generate')); };
function selectUser(key, destination = 'workspace') {
  saveUser(); state.currentUser = state.users.find(u => u.key === key);
  for (const id of userFields) {
    if ($(id).type === 'checkbox') $(id).checked = state.currentUser.fields[id];
    else $(id).value = state.currentUser.fields[id];
  }
  $('text-file').value = ''; state.result = state.currentUser.result;
  validateUser(); renderUsers(); libraryHint(); renderNeeds(); renderSelection(); generationState(); error('form-error', state.currentUser.error); error('analysis-error', state.currentUser.error);
  if (state.result) renderReport(); else { resetReport(state.currentUser.pending ? '正在生成' : '等待生成'); workspaceStep(state.currentUser.pendingStage === 'compose' ? 'report' : state.currentUser.selection || state.currentUser.pendingStage === 'select' ? 'selection' : state.currentUser.extraction ? 'needs' : 'analysis'); }
  view(destination); saveStatus(); taskStatus();
}
function addUser(isDemo = false) {
  let number = state.users.length + 1;
  while (state.users.some(u => u.fields['user-id'] === `USER-${String(number).padStart(3, '0')}`)) number++;
  const fields = Object.fromEntries(userFields.map(id => [id, '']));
  Object.assign(fields, {'user-name':isDemo ? '示例用户（虚构）' : `用户 ${number}`, 'user-id':isDemo ? 'DEMO-USER-001' : `USER-${String(number).padStart(3, '0')}`, pregnancy:'unknown',days:'30','analysis-approved':false,'catalog-select':$('catalog-select').value || 'demo'});
  const user = {key:crypto.randomUUID(), demo:isDemo, fields, result:null, revision:0, pending:false, error:''};
  state.users.push(user); selectUser(user.key, 'user-profile'); scheduleSave(user); return user;
}
$('add-user').onclick = () => addUser();
$('profile-form').addEventListener('input', () => { invalidate(); validateUser(); renderUsers(); });
$('profile-form').onsubmit = event => { event.preventDefault(); validateUser(); if ($('profile-form').reportValidity()) { saveUser(); view('workspace'); } };
function invalidateCatalog(key) {
  saveUser();
  for (const user of state.users) if (user.fields['catalog-select'] === key) { user.result = null; user.selection = null; user.fields['catalog-invalidated']='true'; delete user.fields['reprice-task-id']; scheduleSave(user); }
  renderNeeds(); generationState();
  if (state.currentUser.fields['catalog-select'] === key) { state.result = null; resetReport('产品库已更新'); }
}
function libraryHint() {
  const key = $('catalog-select').value;
  $('library-hint').textContent = state.catalogs[key].some(p=>p.demo_ready) ? `共 ${state.catalogs[key].length} 条记录，已补充Demo假设，可用于演示配比。` : key === 'demo' ? '示例数据仅用于测试，不用于实际配比。' : `共 ${state.catalogs[key].length} 条记录。待核验的产品资料不参与配比。`;
  $('nav-count').textContent = state.catalogs[key].length;
}
function demo() {
  let user = state.users.find(u => u.demo);
  if (!user) user = addUser(true); else selectUser(user.key);
  $('user-name').value = '示例用户（虚构）';
  $('analysis-text').value = state.demoText; $('analysis-approved').checked = false; $('pregnancy').value = 'unknown';
  ['reviewer','review-date','age','allergies','conditions','medications','supplements','forbidden-products','budget','shipping','discount'].forEach(id => $(id).value = '');
  $('days').value = 30; $('user-id').value = 'DEMO-USER-001';
  $('catalog-select').value = 'demo'; invalidate(); validateUser(); renderUsers(); libraryHint(); view('workspace');
}
$('load-demo').onclick = () => { demo(); toast('示例已载入'); };
$('text-file').onchange = async event => {
  const file = event.target.files[0]; if (!file) return;
  const user = state.currentUser, revision = user.revision;
  try {
    const text = await file.text();
    if (user.revision !== revision) return toast('导入期间资料已修改，请重新导入');
    user.fields['analysis-text'] = text; user.result = null; user.selection = null; user.extraction = null; user.revision++;
    if (state.currentUser === user) { $('analysis-text').value = text; state.result = null; resetReport('输入已更新'); renderNeeds(); generationState(); }
    scheduleSave(user);
    toast(`文字已导入${user.fields['user-name']}，请确认分析审核信息`);
  } catch(e) { toast('文字导入失败：' + e.message); }
  finally { event.target.value = ''; }
};
$('case-form').onsubmit = async event => {
  event.preventDefault(); validateUser();
  const invalidAnalysis = ['analysis-text','review-date'].find(id => !$(id).checkValidity());
  if (invalidAnalysis) { workspaceStep('analysis'); $(invalidAnalysis).reportValidity(); return; }
  if (!$('profile-form').checkValidity()) { view('user-profile'); $('profile-form').reportValidity(); return; }
  const user = state.currentUser; if (user.pending || user.task?.status==='running') return;
  saveUser(); const revision = user.revision, snapshot=userSnapshot(user);
  const extracting = event.submitter?.id === 'generate' || (!event.submitter && !needsReady(user));
  const composing = !extracting && event.submitter?.id === 'generate-report';
  if (!extracting && ['days','budget','shipping','discount','catalog-select'].some(id => !$(id).reportValidity())) { workspaceStep('needs'); return; }
  if (!extracting && !composing && user.selection?.status==='ready') { renderSelection(); workspaceStep('selection'); return; }
  user.error = ''; error('form-error', ''); error('analysis-error', ''); error('selection-error',''); user.result = null; state.result = null; resetReport(extracting ? '先提取并确认需求' : '正在配比并复核'); user.pending = true; user.pendingStage = extracting ? 'extract' : composing ? 'compose' : 'select'; renderSelection(); generationState();
  workspaceStep(extracting ? 'analysis' : composing ? 'report' : 'selection');
  try {
    const payload = { text: $('analysis-text').value, as_of: state.today, reviewer: $('reviewer').value, reviewed_at: $('review-date').value,
      analysis_approved: $('analysis-approved').checked, days: num('days'), budget: num('budget'), shipping: num('shipping') ?? 0, discount: num('discount') ?? 0,
      profile: {id:$('user-id').value.trim(), name:$('user-name').value.trim(), age:num('age'), pregnancy:$('pregnancy').value, allergies:split($('allergies').value), conditions:split($('conditions').value), medications:split($('medications').value), current_supplements:split($('supplements').value), forbidden_products:split($('forbidden-products').value) || []}, exposures:[], adjustments:[] };
    const catalogKind=user.fields['catalog-select'];
    const catalog = state.catalogs[catalogKind];
    const confirmed = extracting ? [] : [...$('needs-content').querySelectorAll('.need-choice:checked')].map(el=>Number(el.dataset.index));
    if (!extracting && !needsReady(user)) throw new Error('请先选择沿用需求或重新提取');
    if (!extracting && !confirmed.length) throw new Error('请勾选至少一个有原文依据的目标');
    await persistUser(user);
    if (userSnapshot(user) !== snapshot) return;
    if (extracting) {
      const result = await runTask(user,'/api/extract', {case:payload,catalog,catalog_kind:catalogKind,user_key:user.key,user_revision:revision});
      user.extraction = user.revision === revision ? result : {...result, stale:true}; user.selection = null;
      if (state.currentUser === user) { renderNeeds(); workspaceStep('needs'); resetReport(user.extraction.stale ? '请选择沿用需求或重新提取' : result.status === 'ready' ? '请确认提取的需求' : '需求待确认 / 补充'); }
      return;
    }
    const request = {case:payload, catalog, catalog_kind:catalogKind, user_key:user.key, user_revision:revision,
      extraction_id:user.extraction.history_id,confirmed_goals:confirmed,reuse_extraction:!!(user.extraction.stale && user.extraction.reuseApproved)};
    if (composing) { request.reprice_task_id=user.fields['reprice-task-id']; request.selection_id = user.selection?.history_id; request.confirmed_products = user.selection?.checkedProducts; }
    const result = await runTask(user,composing ? '/api/compose' : '/api/select', request);
    if (userSnapshot(user) !== snapshot) return;
    if (composing) { user.result = result; if (state.currentUser === user) { state.result = result; renderReport(); } }
    else { if (result.status==='ready' && user.fields['catalog-invalidated']==='true') { user.fields['catalog-invalidated']=''; scheduleSave(user); } user.selection = result; if (state.currentUser === user) { renderSelection(); workspaceStep('selection'); } }
  } catch (e) { if (user.revision === revision) { user.error = e.message; if (state.currentUser === user) { workspaceStep(extracting ? 'analysis' : composing ? 'selection' : 'needs'); error(extracting ? 'analysis-error' : composing ? 'selection-error' : 'form-error', e.message); } } }
  finally { user.pending = false; user.pendingStage = ''; if (state.currentUser === user) renderSelection(); generationState(); }
};
function evaluationHTML(ev) {
  const status = {pass:'通过',block:'不通过',needs_review:'有提醒，待确认',error:'调用失败'};
  return `<details class="report-section"><summary>${escapeHTML(ev.stage || '大模型复核')} · ${escapeHTML(status[ev.verdict] || '未完成')}</summary><p>${escapeHTML(ev.summary)}</p><p class="field-help">${escapeHTML(ev.source)} · ${escapeHTML(ev.model)} · ${escapeHTML(ev.evaluated_at)}</p>${ev.checks.map(item => `<p><strong>${escapeHTML(item.name)} · ${escapeHTML({pass:'通过',fail:'不通过',unknown:'待确认'}[item.status])}</strong><br>${escapeHTML(item.reason)}</p>`).join('')}<p class="field-help">复核依据所提供资料进行，不替代专业人员审核。</p></details>`;
}
function reportAvailable(result) {
  return !!result?.report && ['pass','needs_review'].includes(result.evaluation?.verdict);
}
function matchingHTML(matching) {
  if (!matching) return '';
  const related = matching.products.filter(p=>p.status==='related');
  const uncertain = matching.products.filter(p=>p.status==='uncertain');
  const candidates = [...related,...uncertain];
  return `<details class="report-section"><summary>产品关联判断 · ${related.length}款有关联 · ${uncertain.length}款待核验</summary><p>${escapeHTML(matching.summary)}</p><p class="field-help">${escapeHTML(matching.source)} · ${escapeHTML(matching.model)} · ${matching.confirmed ? '使用已确认的选品结果' : matching.rule_based ? '复用人工审核规则，未调用选品模型' : matching.cached ? '复用相同资料的关联结果' : '一次批量关联调用'} · 最多召回10款候选</p>${candidates.map(p=>`<details><summary>${escapeHTML(p.name)} · ${p.status==='uncertain' ? '关联待确认' : p.conflicts.length ? '存在冲突' : p.missing.length ? '资料待核验' : '有关联'}</summary><p>${escapeHTML(p.reason)}</p>${p.evidence.map(e=>`<p class="field-help">依据：${escapeHTML(e.quote)}</p>`).join('')}${p.missing.length ? `<p class="field-help">缺少：${escapeHTML(p.missing.join('、'))}</p>` : ''}${p.conflicts.map(c=>`<p class="field-help">${escapeHTML(c)}</p>`).join('')}</details>`).join('')}<p class="field-help">${matching.products.length-candidates.length}款未召回，未召回不等于不适用；${matching.excluded_products.length}款禁用、停用或按审核规则排除。有关联不等于已通过个体复核。</p></details>`;
}
function cleanReportText(value) {
  return String(value || '').replace(/\r\n?/g,'\n').replace(/\n[ \t]*\n(?:[ \t]*\n)+/g,'\n\n').replace(/^\n+|\n+$/g,'');
}
function productUsage(product) {
  for (const fields of [product, product.raw || {}]) {
    for (const key of ['usage_method','使用方式','使用方法','食用方法','建议食用方法']) {
      if (typeof fields[key] === 'string' && fields[key].trim()) return fields[key].trim();
    }
  }
  return '未提供，请核对现售标签后确认';
}
function reportMainProblems(r) {
  if (r.analysis.original_summary || ('input_text' in r && r.analysis.summary !== r.input_text)) return r.analysis.summary;
  return r.analysis.goal_context?.summary || (r.analysis.goals.length ? '本次已确认的需求：'+r.analysis.goals.join('、') : '未识别明确需求，请核对原专业分析。');
}
function renderReport() {
  workspaceStep('report');
  closeReportEditor(); $('edit-report').hidden = true;
  const ev = state.result.evaluation || {verdict:'error',summary:'未收到有效复核结果，未输出报告。',source:'复核服务',model:'未确认',evaluated_at:'',checks:[]};
  if (!reportAvailable(state.result)) {
    resetReport(ev.verdict === 'needs_review' ? '资料待补充' : ev.verdict === 'error' ? '调用失败' : '复核未通过');
    $('report-placeholder').hidden = true; $('report-body').hidden = false;
    $('report-state').className = 'badge blocked';
    $('report-body').innerHTML = matchingHTML(state.result.matching) + evaluationHTML(ev) + `<p class="field-help">${ev.verdict==='error' ? '模型调用未完成，可在页面上方重试未完成步骤。' : '未输出组合、用量和报价。补充资料或调整要求后重新生成。'}</p>`;
    return;
  }
  const r = state.result.report, p = r.pricing;
  $('report-placeholder').hidden = true; $('report-body').hidden = false; $('report-actions').hidden = false; $('edit-report').hidden = false;
  $('report-state').textContent = ev.verdict === 'needs_review' || r.warnings?.length ? '草案 · 有提醒' : '待专业复核'; $('report-state').className = 'badge';
  const section = (title,html) => `<section class="report-section"><h3>${title}</h3>${html}</section>`;
  const list = items => `<ul>${items.map(x=>`<li>${escapeHTML(x)}</li>`).join('')}</ul>`;
  const original = r.input_text ?? r.analysis.original_summary ?? r.analysis.summary;
  let html = `<div class="report-user"><strong>${escapeHTML(r.profile.name || r.profile.id)}</strong><span>用户编号：${escapeHTML(r.profile.id)}</span></div><div class="notice"><strong>${r.demo ? '示例报告，不用于实际配比' : '报告待专业复核'}</strong>报告日期 ${escapeHTML(r.as_of || '')} · ${r.days}天；需核对禁忌、药物相互作用及实际摄入。</div>`;
  if (r.edited_at) html += `<p class="field-help">报告文字已人工修改并重新复核 · ${escapeHTML(r.edited_at)}</p>`;
  let issues = `<div class="analysis-excerpt">${escapeHTML(cleanReportText(reportMainProblems(r)))}</div><div class="goal-chips">${r.analysis.goals.map(g=>`<span class="chip">${escapeHTML(g)}</span>`).join('')}</div>`;
  if (r.warnings?.length) issues += `<div class="notice"><strong>组合提醒（待确认）</strong>${list(r.warnings)}</div>`;
  if (r.analysis.lifestyle.length || r.analysis.follow_up.length) issues += `<h4>建议与复查</h4>${list([...r.analysis.lifestyle,...r.analysis.follow_up])}`;
  html += section('1 本次有什么主要问题',issues);
  html += section('2 选取了什么产品，为什么选取这些产品',r.selected_products.map(x=>`<article class="report-product"><h4>${escapeHTML(x.name)}</h4><p class="field-help">对应需求：${escapeHTML(x.matched_goals.join('、'))}</p><p class="analysis-excerpt">${escapeHTML(x.rationale)}</p></article>`).join('') || '<p>暂无符合条件的产品，请查看附录中的资料缺项。</p>');
  const doses = `<div class="table-scroll"><table class="report-dose-table"><thead><tr><th>产品 / SKU</th><th>每日用量</th><th>使用方式</th><th>包装数</th><th>购买金额</th><th>消耗成本</th></tr></thead><tbody>${r.selected_products.map(x=>`<tr><td>${escapeHTML(x.name)}<small>${escapeHTML(x.sku)}</small></td><td>${fmt(x.daily_units)}${escapeHTML(x.unit)}<small>${x.professional_adjustment ? '个体调整量（待核实）' : '标签默认量'}</small></td><td>${escapeHTML(productUsage(x))}</td><td>${x.quote.packs}</td><td>${price(x.quote.purchase_cost)}</td><td>${price(x.quote.consumption_cost)}</td></tr>`).join('')}</tbody></table></div>`;
  html += section('3 产品的剂量、使用方式及价格总和',doses+`<div class="quote-total"><span>${r.days}天 · 整包装购买总额</span><strong>${price(p.total)}</strong></div><div class="quote-detail"><span>已知商品小计</span><span>${price(p.known_subtotal)}</span></div><div class="quote-detail"><span>运费 / 整单优惠</span><span>${price(p.shipping)} / ${price(p.discount)}</span></div>${p.budget_remaining != null ? `<div class="quote-detail"><span>${p.budget_remaining >= 0 ? '预算余量' : '超出预算'}</span><span>${price(Math.abs(p.budget_remaining))}</span></div>` : ''}<p class="field-help">按整包装购买；未知价格不按0元计算。使用方式缺失时，需核对现售标签后确认。</p>`);
  let appendix = `<details><summary>4.1 输入信息原文</summary><p class="field-help">仅整理多余空行，保留原文内容。</p><div class="analysis-excerpt">${escapeHTML(cleanReportText(original))}</div></details>`;
  appendix += `<details><summary>4.2 推荐产品的原始信息</summary>${!('product_sources' in r) ? '<p class="field-help">历史报告未保存独立原始快照，以下为报告内保留资料。</p>' : ''}${(r.product_sources || r.selected_products).map(x=>`<details><summary>${escapeHTML(x.name)}</summary><pre class="report-source-json">${escapeHTML(JSON.stringify(x,null,2))}</pre></details>`).join('')}</details>`;
  appendix += `<details><summary>每日成分合计</summary><div class="table-scroll"><table><thead><tr><th>成分</th><th>本组合</th><th>总摄入</th><th>上限检查</th></tr></thead><tbody>${r.nutrients.map(n=>`<tr><td>${escapeHTML(n.name)}</td><td>${fmt(n.product_amount)}${escapeHTML(n.unit)}</td><td>${fmt(n.total)}${escapeHTML(n.unit)}</td><td>${escapeHTML(n.result)}</td></tr>`).join('')}</tbody></table></div><p class="field-help">饮食或其他补充剂摄入未知时，不假设为0。</p></details>`;
  if (r.analysis.goal_context) appendix += `<details><summary>需求提取与确认依据</summary>${list(r.analysis.goal_context.goals.map(g=>`${g.name}：${g.evidence}`).concat(r.analysis.goal_context.cautions))}</details>`;
  appendix += matchingHTML(state.result.matching || r.matching) + evaluationHTML(ev);
  if (r.learning?.rules.length) appendix += `<details><summary>命中的人工审核规则</summary>${r.learning.rules.map(rule=>`<p><strong>${escapeHTML(rule.title)}</strong> · 版本 ${rule.revision}<br>依据：${escapeHTML(rule.reason)}<br>局限：${escapeHTML(rule.limitations)}<br>规则 ${escapeHTML(rule.id)} · 来源案例 ${escapeHTML(rule.case_title)}（${escapeHTML(rule.case_id)}）</p>`).join('')}</details>`;
  appendix += `<details><summary>筛选记录 · ${r.candidates.length}款候选 · ${r.excluded_products.length}条排除记录</summary>${list(r.excluded_products.map(x=>`${x.name}：${x.reason}`))}</details><details><summary>待确认事项与价格来源</summary>${list([...r.blocked,...r.missing])}${r.selected_products.map(x=>`<p class="field-help">${escapeHTML(x.name)} · 价格来源 ${escapeHTML(x.quote.price_source)}；日期 ${escapeHTML(x.quote.price_date)}；有效至 ${escapeHTML(x.quote.valid_until)}。${escapeHTML(x.quote.price_reason || '')}</p>`).join('')}</details>`;
  html += section('4 附属内容',`<div class="report-appendix">${appendix}</div>`);
  $('report-body').innerHTML = html;
}

function closeReportEditor() {
  state.reportEdit = null; $('report-editor').hidden = true; $('report-edit-fields').disabled = false;
  $('save-report').disabled = false; $('save-report').textContent = '保存并复核'; $('cancel-report-edit').disabled = false;
}
$('edit-report').onclick = () => {
  const user = state.currentUser, result = user.result;
  if (!reportAvailable(result) || user.pending) return;
  const r = result.report;
  state.reportEdit = {user, revision:user.revision, historyId:result.history_id};
  $('report-summary').value = r.analysis.summary;
  $('report-lifestyle').value = r.analysis.lifestyle.join('\n'); $('report-follow-up').value = r.analysis.follow_up.join('\n');
  $('report-rationales').innerHTML = r.selected_products.map((p,i)=>`<label for="report-rationale-${i}">${escapeHTML(p.name)} · 搭配依据 <span class="required">*</span></label><textarea id="report-rationale-${i}" data-product="${escapeHTML(p.id)}" rows="5" required>${escapeHTML(p.rationale)}</textarea>`).join('');
  error('report-edit-error',''); $('report-editor').hidden = false; $('report-body').hidden = true; $('report-actions').hidden = true; $('edit-report').hidden = true;
};
$('cancel-report-edit').onclick = () => renderReport();
$('report-editor').onsubmit = async event => {
  event.preventDefault(); const edit = state.reportEdit; if (!edit || edit.user.pending) return;
  const user = edit.user, lines = id => $(id).value.split('\n').map(s=>s.trim()).filter(Boolean);
  const changes = {summary:$('report-summary').value, lifestyle:lines('report-lifestyle'), follow_up:lines('report-follow-up'),
    rationales:Object.fromEntries([...$('report-rationales').querySelectorAll('textarea')].map(el=>[el.dataset.product, el.value]))};
  user.pending = true; generationState(); $('report-edit-fields').disabled = true; $('save-report').disabled = true; $('cancel-report-edit').disabled = true;
  $('save-report').textContent = '正在复核修改…'; error('report-edit-error','');
  try {
    await persistUser(user);
    const result = await api('/api/reports/edit',{user_key:user.key,user_revision:edit.revision,history_id:edit.historyId,changes});
    if (user.revision !== edit.revision || user.result?.history_id !== edit.historyId) return;
    if (!reportAvailable(result)) throw new Error(result.evaluation?.summary || '修改未通过复核，原报告已保留');
    user.result = result; delete user.fields['reprice-task-id']; scheduleSave(user);
    if (state.currentUser === user) { state.result = result; renderReport(); toast(result.evaluation.verdict === 'needs_review' ? '报告修改已保存，请查看提醒' : '报告修改已保存并通过复核'); }
  } catch(e) { if (state.reportEdit === edit) error('report-edit-error',e.message + '；原报告已保留。'); else toast('报告修改未保存：' + e.message); }
  finally {
    user.pending = false; generationState();
    if (state.reportEdit === edit) { $('report-edit-fields').disabled = false; $('save-report').disabled = false; $('cancel-report-edit').disabled = false; $('save-report').textContent = '保存并复核'; }
  }
};
function download(name, data, type) { const url = URL.createObjectURL(new Blob([data], {type})); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
const reportFilename = extension => `健康产品组合报告-${state.result.report.profile.id.replace(/[\\/:*?"<>|\x00-\x1f]/g, '_')}.${extension}`;
$('download-md').onclick = () => { if (reportAvailable(state.result)) download(reportFilename('md'), state.result.markdown, 'text/markdown;charset=utf-8'); };
$('download-json').onclick = () => { if (reportAvailable(state.result)) download(reportFilename('json'), JSON.stringify(state.result.report, null, 2), 'application/json'); };
$('print').onclick = () => {
  if (!reportAvailable(state.result)) return;
  const closed = [...$('report-body').querySelectorAll('details:not([open])')];
  closed.forEach(el=>el.open=true);
  try { window.print(); } finally { closed.forEach(el=>el.open=false); }
};
function renderCatalog() {
  const key = $('catalog-view-select').value, q = $('product-search').value.toLowerCase();
  const products = state.catalogs[key].filter(p => `${p.name} ${p.id} ${p.goals.join(' ')}`.toLowerCase().includes(q));
  $('catalog-demo-note').hidden = !state.catalogs[key].some(p=>p.demo_ready);
  $('catalog-summary').textContent = `${products.length} 条记录`;
  $('product-rows').innerHTML = products.map(p => `<tr><td><strong>${escapeHTML(p.name)}</strong><small>${escapeHTML(p.sku)} · ${escapeHTML(p.source.file)} 第${p.source.row}行</small></td><td>${escapeHTML(p.goals.join('、') || '待核验')}</td><td>${p.daily_units == null ? '待核验' : fmt(p.daily_units) + escapeHTML(p.unit || '')}</td><td>${price(p.price.amount)}<small>${escapeHTML(p.price.as_of || '未提供日期')}</small></td><td><span class="badge ${p.label_reviewed && p.ingredients_complete ? 'neutral' : ''}">${p.demo_ready ? 'Demo参数齐全' : p.demo ? '虚拟示例' : p.label_reviewed && p.ingredients_complete ? '资料已核对' : '资料待核验'}</span></td><td><button class="text-button edit-product" data-id="${escapeHTML(p.id)}">查看 / 核对 →</button></td></tr>`).join('') || '<tr><td colspan="6">没有匹配记录，请导入产品库或调整搜索。</td></tr>';
  document.querySelectorAll('.edit-product').forEach(el => el.onclick = () => editProduct(key, el.dataset.id));
}
$('catalog-view-select').onchange = () => { $('product-editor').hidden = true; renderCatalog(); };
$('product-search').oninput = renderCatalog;
$('excel-file').onchange = async event => {
  const file = event.target.files[0]; if (!file) return; error('catalog-error', '');
  try {
    if (file.size > 10_000_000) throw new Error('Excel文件请小于10MB');
    const bytes = new Uint8Array(await file.arrayBuffer()); let binary = '';
    for (let i=0;i<bytes.length;i+=8192) binary += String.fromCharCode(...bytes.subarray(i,i+8192));
    const data = await api('/api/import', {filename:file.name, data:btoa(binary)});
    invalidateCatalog('imported'); state.catalogs.imported = data.products;
    for (const id of ['catalog-select','catalog-view-select']) { $(id).querySelector('[value=imported]').disabled = false; $(id).value = 'imported'; }
    invalidate(); libraryHint(); renderCatalog(); $('product-editor').hidden = true; toast(`导入 ${data.products.length} 款产品，原文件未修改`);
    await persistCatalog('imported');
  } catch(e) { error('catalog-error', e.message); }
  finally { event.target.value = ''; }
};
function editProduct(key, id) {
  const p = state.catalogs[key].find(p => p.id === id); state.editing = {key, id}; $('product-editor').hidden = false;
  $('editor-title').textContent = p.name + ' · 资料核对';
  $('demo-product-info').hidden = !p.demo_ready;
  const demoLabels = {unit:'用量单位',daily_units:'每日用量',units_per_pack:'包装含量',label_daily_max:'每日计算上限',label_source:'标签来源',ingredients:'成分计算集合',allergens:'过敏原',contraindications:'禁忌',price:'报价'};
  $('demo-product-fields').innerHTML = Object.entries(p.demo_assumptions || {}).map(([key,value])=>`<dt>${escapeHTML(demoLabels[key] || key)}</dt><dd>${escapeHTML(value)}</dd>`).join('');
  $('archive-product-info').hidden = !p.archive_info;
  $('archive-product-fields').innerHTML = Object.entries(p.archive_info || {}).map(([label,value]) => `<dt>${escapeHTML(label)}</dt><dd>${escapeHTML(value)}</dd>`).join('');
  $('archive-ingredient-table').innerHTML = p.ingredients_raw?.length ? `<table><thead><tr><th>成分</th><th>含量原文</th><th>计量基准</th><th>资料类型 / 核验状态</th></tr></thead><tbody>${p.ingredients_raw.map(row => { const v = row.values; return `<tr><td>${escapeHTML(v[2])}</td><td>${escapeHTML(v[3])}</td><td>${escapeHTML(v[4])}</td><td>${escapeHTML(v[5])}<small>${escapeHTML(v[6])}</small></td></tr>`; }).join('')}</tbody></table>` : '';
  const fields = {'p-goals':p.goals.join('；'),'p-sku':p.sku,'p-dose':p.daily_units,'p-unit':p.unit,'p-max':p.label_daily_max,'p-pack':p.units_per_pack,'p-label':p.label_source,'p-evidence':p.evidence.join('\n'),'p-allergens':p.allergens ? (p.allergens.join('；') || '无') : '', 'p-contra':p.contraindications ? (p.contraindications.join('；') || '无') : '', 'p-ingredients':p.ingredients.length ? JSON.stringify(p.ingredients,null,2) : '', 'p-price':p.price.amount,'p-price-source':p.price.source,'p-price-date':p.price.as_of,'p-valid':p.price.valid_until};
  for (const [id,value] of Object.entries(fields)) $(id).value = value ?? '';
  $('p-reviewed').checked = p.label_reviewed && p.ingredients_complete; $('raw-product').textContent = JSON.stringify({source:p.source, raw:p.raw, tags:p.tags_raw, ingredients:p.ingredients_raw},null,2);
  error('editor-error',''); $('product-editor').scrollIntoView({behavior:'smooth',block:'start'});
}
$('close-editor').onclick = () => $('product-editor').hidden = true;
$('product-form').onsubmit = async event => {
  event.preventDefault(); error('editor-error','');
  try {
    const ingredients = JSON.parse($('p-ingredients').value);
    if (!Array.isArray(ingredients) || !ingredients.length || ingredients.some(x => !x.name || !x.source || !['g','mg','µg','ug','mcg'].includes(x.unit) || typeof x.amount_per_unit !== 'number' || x.amount_per_unit < 0 || !Number.isFinite(x.amount_per_unit))) throw new Error('成分须为非空数组，每项包含name、数值amount_per_unit、unit(g/mg/µg)和source');
    const p = state.catalogs[state.editing.key].find(p => p.id === state.editing.id);
    Object.assign(p,{goals:split($('p-goals').value)||[],sku:$('p-sku').value,daily_units:num('p-dose'),unit:$('p-unit').value,label_daily_max:num('p-max'),units_per_pack:num('p-pack'),label_source:$('p-label').value,evidence:$('p-evidence').value.split('\n').filter(Boolean),allergens:split($('p-allergens').value),contraindications:split($('p-contra').value),ingredients,ingredients_complete:$('p-reviewed').checked,label_reviewed:$('p-reviewed').checked,
      price:{amount:num('p-price'),currency:'CNY',sku:$('p-sku').value,source:$('p-price-source').value,as_of:$('p-price-date').value,valid_until:$('p-valid').value}});
    invalidateCatalog(state.editing.key); renderCatalog(); libraryHint(); toast('本次产品资料已更新，请重新生成报告');
    await persistCatalog(state.editing.key);
  } catch(e) { error('editor-error',e.message); }
};
$('download-catalog').onclick = () => download('核验产品库.json', JSON.stringify(state.catalogs[$('catalog-view-select').value],null,2), 'application/json');
async function initialize() { try {
  const data = await api('/api/bootstrap'); state.demoText = data.text; state.today = data.today;
  state.catalogs.demo = data.demo_catalog; state.catalogs.archive = data.archive_catalog;
  Object.assign(state.catalogs, data.saved_catalogs || {});
  if (state.catalogs.imported.length) for (const id of ['catalog-select','catalog-view-select']) $(id).querySelector('[value=imported]').disabled = false;
  const saved = await api('/api/users');
  state.users = saved.users.map(u => ({...u, fields:{...Object.fromEntries(userFields.map(id => [id, ''])),pregnancy:'unknown',days:'30','catalog-select':'demo',...u.fields}, pending:false,error:''}));
  for (const user of state.users) user.savedSnapshot=userSnapshot(user);
  if (state.users.length) selectUser(state.users[0].key); else demo();
  renderCatalog(); $('add-user').disabled = false;
  restoreTasks().catch(e=>toast('任务状态暂未恢复：'+e.message));
} catch(e) { error('form-error','初始化失败：'+e.message); } }
initialize();

