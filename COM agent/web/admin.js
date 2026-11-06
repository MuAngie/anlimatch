const $ = id => document.getElementById(id);
const escapeHTML = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time = x => x ? new Date(x).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '暂无';
const statuses = {running:'处理中',ready:'待确认需求',pass:'复核通过',block:'已拦截',needs_review:'待确认',error:'失败',completed:'完成'};
const actions = {extract:'需求提取',select:'选品',compose:'配比与复核',import:'产品库导入',report_edit:'报告文字修改与复核',learning:'人工案例规则提炼'};
let setup = false, users = [], offset = 0, epoch = 0;
function error(id, message) { $(id).textContent = message; $(id).hidden = !message; }
function closeConsole() { if(typeof resetLearning==='function') resetLearning(); epoch++; users=[]; $('admin-console').hidden=true; $('auth-panel').hidden=false; $('logout').hidden=true; $('account-name').textContent=''; ['admin-users','admin-history','admin-prompts','user-detail','history-detail','learning-cases','learning-rules','learning-case-detail'].forEach(id=>$(id).innerHTML=''); }
async function api(path, body) {
  const response = await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
  const data = await response.json();
  if (!response.ok) { if (response.status===401 && path!=='/api/admin/login') { closeConsole(); error('auth-error','管理员会话已失效，请重新登录。'); } throw new Error(data.error || '操作失败'); }
  return data;
}
async function status() {
  const data=await api('/api/admin/status'); setup=!data.initialized;
  $('auth-title').textContent=setup?'设置管理员账号':'管理员登录';
  $('auth-description').textContent=setup?'首次使用，需要先设置独立管理员账号和密码。':'使用独立管理员账号登录。';
  $('confirmation').hidden=!setup; $('setup-help').hidden=!setup;
  $('admin-confirm').required=setup; $('admin-password').minLength=setup?8:1;
  $('admin-password').autocomplete=setup?'new-password':'current-password';
  $('auth-submit').disabled=false; $('auth-submit').textContent=setup?'创建管理员账号':'登录';
  if(data.account) await openConsole(data.account);
}
async function openConsole(account) { epoch++; $('auth-panel').hidden=true; $('admin-console').hidden=false; $('logout').hidden=false; $('account-name').textContent=account.username+' · 管理员'; $('admin-password').value=''; $('admin-confirm').value=''; panel('users-panel'); await loadUsers(); }
$('auth-form').onsubmit=async event=>{
  event.preventDefault(); error('auth-error','');
  if(setup && $('admin-password').value!==$('admin-confirm').value) return error('auth-error','两次输入的密码不一致');
  $('auth-submit').disabled=true;
  try { const payload={username:$('admin-username').value.trim(),password:$('admin-password').value};
    if(setup) { await api('/api/admin/setup',payload); $('admin-password').value=''; $('admin-confirm').value=''; await status(); error('auth-error','管理员已创建，请输入密码登录。'); }
    else await openConsole((await api('/api/admin/login',payload)).account);
  } catch(e) { error('auth-error',e.message); }
  finally { $('auth-submit').disabled=false; }
};
$('logout').onclick=async()=>{ try { await api('/api/admin/logout',{}); closeConsole(); await status(); } catch(e) { error('admin-error',e.message); } };
function renderUsers(){const q=$('user-search').value.toLowerCase(); const rows=users.filter(u=>(u.name+' '+u.user_id).toLowerCase().includes(q)); $('users-count').textContent=`共 ${rows.length} 位用户`;
  $('admin-users').innerHTML=rows.map(u=>`<tr><td><strong>${escapeHTML(u.name||'未命名用户')}</strong><small>${escapeHTML(u.user_id||'编号未填写')}</small></td><td>${u.demo?'示例':'实际用户'}</td><td>${u.usage_count}</td><td>${time(u.last_used)}</td><td>${time(u.updated_at)}</td><td><button class="text-button user-detail-button" data-key="${escapeHTML(u.key)}">查看资料</button><button class="text-button user-history-button" data-key="${escapeHTML(u.key)}">使用历史</button></td></tr>`).join('') || '<tr><td colspan="6">暂无用户。工作台新增或编辑的用户会保存到这里。</td></tr>';
  document.querySelectorAll('.user-detail-button').forEach(el=>el.onclick=()=>showUser(el.dataset.key)); document.querySelectorAll('.user-history-button').forEach(el=>el.onclick=()=>{ $('history-user').value=el.dataset.key; offset=0; panel('history-panel'); });
}
async function loadUsers(){const turn=epoch; try{ const data=await api('/api/admin/users'); if(turn!==epoch)return; users=data.users; renderUsers(); const selected=$('history-user').value; $('history-user').innerHTML='<option value="">全部用户</option>'+users.map(u=>`<option value="${escapeHTML(u.key)}">${escapeHTML(u.name+' / '+u.user_id)}</option>`).join(''); $('history-user').value=selected; }catch(e){error('admin-error',e.message);} }
const fieldNames={'user-name':'用户姓名 / 称呼','user-id':'用户编号',age:'年龄',pregnancy:'孕哺状态',allergies:'过敏史',conditions:'疾病史',medications:'正在使用的药物',supplements:'在用补充剂','forbidden-products':'禁用商品','analysis-text':'专业体检分析',reviewer:'分析审核人','review-date':'审核日期','analysis-approved':'分析已由专业人员审核',days:'计划周期（天）',budget:'预算',shipping:'运费',discount:'优惠',exposures:'成分摄入信息',adjustments:'个体剂量调整','catalog-select':'产品库'};
async function showUser(key){const turn=epoch;try{const data=await api('/api/admin/user?key='+encodeURIComponent(key));if(turn!==epoch)return; $('user-detail').hidden=false; $('user-detail').innerHTML='<h2>用户资料</h2><dl class="user-fields">'+Object.entries(data.fields).map(([k,v])=>`<div class="${['analysis-text','exposures','adjustments'].includes(k)?'wide':''}"><dt>${escapeHTML(fieldNames[k]||k)}</dt><dd>${escapeHTML(typeof v==='boolean'?(v?'是':'否'):v||'未填写')}</dd></div>`).join('')+'</dl>'; $('user-detail').scrollIntoView({behavior:'smooth',block:'start'});}catch(e){error('admin-error',e.message);} }
async function loadHistory(){const turn=epoch;try{const data=await api(`/api/admin/history?offset=${offset}&user_key=${encodeURIComponent($('history-user').value)}`);if(turn!==epoch)return; $('history-count').textContent=`共 ${data.total} 条 · 第 ${Math.floor(offset/30)+1} 页`; $('history-prev').disabled=offset===0; $('history-next').disabled=offset+data.limit>=data.total;
  $('admin-history').innerHTML=data.items.map(h=>`<tr><td>${time(h.started_at)}</td><td>${escapeHTML(h.user_name||'未关联用户')}<small>${escapeHTML(h.user_id)}</small></td><td>${escapeHTML(actions[h.action]||h.action)}</td><td><span class="badge ${['block','error'].includes(h.status)?'blocked':''}">${escapeHTML(statuses[h.status]||h.status)}</span></td><td><button class="text-button history-detail-button" data-id="${escapeHTML(h.id)}">查看详情</button></td></tr>`).join('')||'<tr><td colspan="5">暂无使用记录。开始配比或导入产品库后会自动记录。</td></tr>';
  document.querySelectorAll('.history-detail-button').forEach(el=>el.onclick=()=>showHistory(el.dataset.id));
}catch(e){error('admin-error',e.message);} }
async function showHistory(id){const turn=epoch;try{const data=await api('/api/admin/history/detail?id='+encodeURIComponent(id));if(turn!==epoch)return; const result=data.result;
  $('history-detail').hidden=false; $('history-detail').innerHTML=`<h2>使用记录详情</h2><p>${time(data.started_at)} · ${escapeHTML(data.user_name||'未关联用户')} · ${escapeHTML(statuses[data.status]||data.status)}</p><h3>结果</h3><pre>${escapeHTML(JSON.stringify(result,null,2)||'处理中，尚未产生结果')}</pre><details><summary>查看当次输入</summary><pre>${escapeHTML(JSON.stringify(data.request,null,2))}</pre></details><details><summary>查看当次提示词快照</summary>${(data.prompts||[]).map(p=>`<h3>${escapeHTML(p.name)} · ${escapeHTML(p.version)}</h3><pre>${escapeHTML(p.text)}</pre>`).join('')||'<p>本功能不使用大模型提示词。</p>'}</details>`; $('history-detail').scrollIntoView({behavior:'smooth',block:'start'});
}catch(e){error('admin-error',e.message);} }
async function loadPrompts(){const turn=epoch;try{const data=await api('/api/admin/prompts');if(turn!==epoch)return; $('admin-prompts').innerHTML=data.prompts.map(p=>`<section class="prompt-item"><h2>${escapeHTML(p.name)}</h2><p class="field-help">${escapeHTML(p.description)} · 版本 ${escapeHTML(p.version)}</p><label for="prompt-${p.key}">提示词</label><textarea id="prompt-${p.key}" readonly rows="17">${escapeHTML(p.text)}</textarea><details><summary>输出结构</summary><pre>${escapeHTML(JSON.stringify(p.schema,null,2))}</pre></details></section>`).join(''); $('rules-list').textContent=data.rules.join('、');}catch(e){error('admin-error',e.message);} }
function panel(id){error('admin-error',''); document.querySelectorAll('.admin-panel').forEach(el=>el.hidden=el.id!==id); document.querySelectorAll('[data-panel]').forEach(el=>el.classList.toggle('active',el.dataset.panel===id)); if(id==='history-panel')loadHistory(); if(id==='prompts-panel')loadPrompts(); if(id==='learning-panel')loadLearning(); }
document.querySelectorAll('[data-panel]').forEach(el=>el.onclick=()=>panel(el.dataset.panel));
$('user-search').oninput=renderUsers; $('refresh-users').onclick=loadUsers; $('refresh-history').onclick=loadHistory; $('refresh-prompts').onclick=loadPrompts;
$('history-user').onchange=()=>{offset=0;$('history-detail').hidden=true;loadHistory();}; $('history-prev').onclick=()=>{offset=Math.max(0,offset-30);loadHistory();}; $('history-next').onclick=()=>{offset+=30;loadHistory();};
status().catch(e=>error('auth-error',e.message));
