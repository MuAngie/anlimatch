'use strict';
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const pages = $$('.page');
const searchInput = $('#doc-search');
let currentPage = 'overview';
let toastTimer;
function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 2800);
}
function navigate() {
  const [pageId, anchor] = location.hash.slice(1).split(':');
  currentPage = pages.some(page => page.id === pageId) ? pageId : 'overview';
  searchInput.value = '';
  $('#search-results').hidden = true;
  pages.forEach(page => { page.hidden = page.id !== currentPage; });
  $$('[data-page]').forEach(link => {
    if (link.dataset.page === currentPage) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  const activePage = document.getElementById(currentPage);
  $('#page-label').textContent = activePage.dataset.label;
  document.title = `${activePage.dataset.label} · 配方笺项目说明书`;
  $('#page-toc').replaceChildren();
  activePage.querySelectorAll('.content-section[id], .feature[id]').forEach(section => {
    const link = document.createElement('a');
    link.href = `#${currentPage}:${section.id}`;
    link.textContent = section.querySelector('h2').childNodes[0].textContent.trim();
    $('#page-toc').append(link);
  });
  document.body.classList.remove('menu-open');
  $('#menu-toggle').setAttribute('aria-expanded', 'false');
  $('#menu-toggle').setAttribute('aria-label', '展开文档导航');
  if (currentPage === 'flows') renderFlow();
  const target = anchor && activePage.querySelector(`[id="${CSS.escape(anchor)}"]`);
  if (target) {
    if (target.tagName === 'DETAILS') target.open = true;
    requestAnimationFrame(() => {
      target.scrollIntoView({behavior: 'auto', block: 'start'});
      target.classList.remove('search-highlight');
      requestAnimationFrame(() => target.classList.add('search-highlight'));
    });
  } else window.scrollTo({top: 0, behavior: 'auto'});
}
const searchIndex = pages.flatMap(page => {
  const sections = [...page.querySelectorAll('.content-section[id], .feature[id]')];
  return (sections.length ? sections : [page]).map(section => ({
    page: page.id, label: page.dataset.label, id: section.id,
    title: (section.querySelector('h2, h1') || section).textContent.trim(),
    text: section.textContent.replace(/\s+/g, ' ').trim()
  }));
});
searchInput.addEventListener('input', () => {
  const query = searchInput.value.trim().toLocaleLowerCase();
  if (!query) { navigate(); return; }
  if ($('#search-results').hidden) window.scrollTo({top: 0, behavior: 'auto'});
  pages.forEach(page => { page.hidden = true; });
  $('#search-results').hidden = false;
  $('#page-toc').replaceChildren();
  $('#page-label').textContent = '搜索文档';
  const results = searchIndex.filter(item => item.text.toLocaleLowerCase().includes(query));
  $('#search-summary').textContent = `“${searchInput.value.trim()}” · 找到 ${results.length} 个章节`;
  $('#result-list').replaceChildren();
  results.forEach(item => {
    const link = document.createElement('a');
    link.href = `#${item.page}:${item.id}`;
    link.className = 'result-item';
    const label = document.createElement('small'); label.textContent = item.label;
    const heading = document.createElement('h2'); heading.textContent = item.title;
    const snippet = document.createElement('p');
    const start = Math.max(0, item.text.toLocaleLowerCase().indexOf(query) - 28);
    snippet.textContent = `${start ? '…' : ''}${item.text.slice(start, start + 150)}${start + 150 < item.text.length ? '…' : ''}`;
    link.append(label, heading, snippet);
    $('#result-list').append(link);
  });
  if (!results.length) {
    const empty = document.createElement('p'); empty.className = 'search-empty';
    empty.textContent = '没有匹配章节。试试“预算”“规则”“复核”或代码文件名。';
    $('#result-list').append(empty);
  }
});
$('#clear-search').addEventListener('click', navigate);
document.addEventListener('keydown', event => {
  if (event.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) {
    event.preventDefault();
    if (window.innerWidth <= 700) {
      document.body.classList.add('menu-open');
      $('#menu-toggle').setAttribute('aria-expanded', 'true');
    }
    searchInput.focus();
  }
  if (event.key === 'Escape') {
    navigate(); searchInput.blur();
  }
});
$('#menu-toggle').addEventListener('click', () => {
  const open = document.body.classList.toggle('menu-open');
  $('#menu-toggle').setAttribute('aria-expanded', String(open));
  $('#menu-toggle').setAttribute('aria-label', open ? '收起文档导航' : '展开文档导航');
});
document.addEventListener('click', event => {
  if (!event.target.closest('.sidebar, #menu-toggle')) {
    document.body.classList.remove('menu-open');
    $('#menu-toggle').setAttribute('aria-expanded', 'false');
  }
  const link = event.target.closest('a[href^="#"]');
  if (link && link.getAttribute('href') === location.hash) {
    event.preventDefault(); navigate();
  }
});
$('#expand-features').addEventListener('click', () => {
  const expand = $('#expand-features').getAttribute('aria-expanded') !== 'true';
  $$('.feature').forEach(item => { item.open = expand; });
  $('#expand-features').setAttribute('aria-expanded', String(expand));
  $('#expand-features').textContent = expand ? '收起全部' : '展开全部';
});
let printDetails;
window.addEventListener('beforeprint', () => {
  printDetails = $$('.feature').map(item => item.open);
  if (currentPage === 'features') $$('.feature').forEach(item => { item.open = true; });
});
window.addEventListener('afterprint', () => {
  if (printDetails) $$('.feature').forEach((item, i) => { item.open = printDetails[i]; });
});
$('#print-page').addEventListener('click', () => window.print());
function download(content, filename, mime) {
  const url = URL.createObjectURL(new Blob([content], {type: mime}));
  const link = document.createElement('a'); link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}
function markdown(node) {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent.replace(/\s+/g, ' ');
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const content = () => [...node.childNodes].map(markdown).join('');
  switch (node.tagName) {
    case 'H2': return `\n\n## ${node.textContent.trim()}\n\n`;
    case 'H3': return `\n\n### ${node.textContent.trim()}\n\n`;
    case 'P': if (node.classList.contains('section-kicker')) return ''; return `${content().trim()}\n\n`;
    case 'STRONG': return `**${content()}**`;
    case 'CODE': return `\`${node.textContent.trim()}\``;
    case 'BR': return ' / ';
    case 'UL': return [...node.children].map(child => `- ${markdown(child).trim()}\n`).join('') + '\n';
    case 'OL': return [...node.children].map((child, i) => `${i + 1}. ${markdown(child).trim()}\n`).join('') + '\n';
    case 'TABLE': {
      const rows = [...node.rows].map(row => [...row.cells].map(cell => markdown(cell).trim().replace(/\|/g, '\\|').replace(/\n/g, ' ')));
      return '\n' + rows.map((row, i) => `| ${row.join(' | ')} |\n${i === 0 ? '| ' + row.map(() => '---').join(' | ') + ' |\n' : ''}`).join('') + '\n';
    }
    case 'DIV': if (node.classList.contains('formula-block')) return '\n```text\n' + [...node.children].map(child => child.textContent).join('\n') + '\n```\n\n'; return content();
    default: return content();
  }
}
$('#download-prd').addEventListener('click', () => {
  const intro = '# 配方笺 · 产品需求文档\n\n文档版本：1.0.0\n内容快照：2026-10-12\n状态：依据当前实现整理，待产品确认\n\n';
  download(intro + markdown($('#prd-document')).replace(/\n{3,}/g, '\n\n').trim() + '\n', '配方笺-PRD-1.0.0.md', 'text/markdown;charset=utf-8');
  toast('已导出完整 PRD，包含全部 12 章。');
});

// Each diagram retains a matching Mermaid source for editing in other tools.
const node = (id, x, y, title, subtitle, type = 'system', width = 300) => ({id, x, y, title, subtitle, type, width, height: 76});
const edge = (from, to, label = '', side = 'bottom', path = null, labelAt = null) => ({from, to, label, side, path, labelAt});
const flows = {
  user: {
    title: '用户主流程', width: 960, height: 1120,
    description: '两次人工确认之后，才进入可复核的报告生成。',
    note: '依据 needs.py、matching.py、generator.py、server.py 与 web/app.js。提取无目标需补资料；商品资料未齐不可确认；unknown 可以输出带提醒草案，fail 拦截。',
    nodes: [node('input',70,30,'选择用户 · 输入专业分析','补充背景 / 周期 / 费用','human'),node('extract',70,145,'提取健康需求','模型整理 + 本地逐字依据校验'),node('confirmGoals',70,260,'核对并勾选本次目标','人工确认 ①','human'),node('select',70,375,'批量判断产品关联','排除禁用 / 停用；最多 10 款候选'),node('confirmProducts',70,490,'勾选合格商品并确认','人工确认 ②；锁定商品集合','human'),node('compose',70,605,'模型配比','只使用确认商品；不生成价格'),node('calculate',70,720,'本地标签与费用校验','原标签日量 / 成分 / 包装 / 预算'),node('eval',70,835,'独立模型 eval','选品 / 日量 / 禁用 / 重复 / 禁忌'),node('report',70,995,'待专业复核草案','预览 / 文字编辑复核 / 导出','human'),node('missing',560,145,'无明确目标 / 要求暂停','补充资料或暂停搭配','warning'),node('unready',560,375,'缺少商品资料 / 选品失败','补齐后重新选品；保留当前输入','warning'),node('stop',560,720,'明确冲突 / 不能计算','解释原因；不生成报告','danger'),node('warnings',560,895,'无 fail，存在 unknown','保留提醒、商品、日量与报价','warning')],
    edges: [edge('input','extract'),edge('extract','confirmGoals','ready'),edge('extract','missing','其他状态','right'),edge('confirmGoals','select'),edge('select','confirmProducts','有 eligible'),edge('select','unready','未就绪','right'),edge('confirmProducts','compose'),edge('compose','calculate'),edge('calculate','eval','校验通过'),edge('calculate','stop','冲突','right'),edge('eval','stop','fail / error','right','M 370 873 H 470 V 758 H 560',[465,835]),edge('eval','report','全部 pass','bottom',null,[290,947]),edge('eval','warnings','unknown','right','M 370 873 H 465 V 933 H 560',[465,905]),edge('warnings','report','','bottom','M 710 971 V 1033 H 370')],
    mermaid: `flowchart TD
  A[选择用户与专业分析] --> B[模型提取需求并校验逐字依据]
  B -->|ready| C[人工确认目标]
  B -->|needs_review 或 block| X[补资料或暂停搭配]
  C --> D[排除禁用后批量关联 / 审核规则复用]
  D -->|有 eligible| E[人工确认商品]
  D -->|缺项或失败| Y[保留输入，补齐后重新选品]
  E --> F[模型配比，仅用确认商品]
  F --> G[本地标签、成分、报价及预算校验]
  G -->|通过| H[独立五项 eval]
  G -->|明确冲突| Z[拦截，不生成报告]
  H -->|任一 fail 或调用失败| Z
  H -->|全部 pass| R[待专业复核草案]
  H -->|无 fail，有 unknown| W[保留提醒与计算结果]
  W --> R
  R --> O[预览 / 文字修改复核 / 导出]`
  },
  architecture: {
    title: '系统架构与职责', width: 990, height: 800,
    description: '浏览器、HTTP 编排、模型判断与本地计算各自承担不同职责。',
    note: '静态说明网页完全独立于此架构。业务前端原生 HTML / CSS / JS；后端 Python ThreadingHTTPServer + SQLite。模型通过统一 ModelClient 调用；规则和持久化不依赖模型。',
    nodes: [node('browser',45,35,'原生浏览器工作台 / 后台','web/app.js · admin.js · learning.js','human',270),node('server',360,35,'HTTP 编排与权限','server.py · 127.0.0.1:8765','system',270),node('db',675,35,'本地持久化','store.py · SQLite · 8 张表','system',270),node('needs',40,250,'需求提取','needs.py','system',205),node('matching',275,250,'产品关联','matching.py','system',205),node('generator',510,250,'配比与确认编排','generator.py','system',205),node('eval',745,250,'独立复核','evaluator.py','system',205),node('model',280,445,'统一模型客户端','model_client.py · 配置快照','system',420),node('cli',90,600,'已登录本机 Codex CLI','临时会话 / 禁工具 / 只读沙箱','system',340),node('api',550,600,'支持严格 Schema 的 API','基址 / 密钥 / 模型完整配置','system',340),node('rules',40,145,'本地确定性计算','engine.py · 日量 / 成分 / 报价','system',270),node('learning',675,145,'审核案例规则','learning.py · 条件 / 指纹 / 排除','system',270)],
    edges: [edge('browser','server','JSON','right'),edge('server','db','保存 / 读取','right'),edge('server','rules','','bottom','M 495 111 V 126 H 175 V 145'),edge('server','learning','','bottom','M 495 111 V 126 H 810 V 145'),edge('server','needs','','bottom','M 495 111 V 232 H 142 V 250'),edge('server','matching','','bottom','M 495 111 V 232 H 377 V 250'),edge('server','generator','','bottom','M 495 111 V 232 H 612 V 250'),edge('server','eval','','bottom','M 495 111 V 232 H 847 V 250'),edge('needs','model','','bottom','M 142 326 V 394 H 490 V 445'),edge('matching','model','','bottom','M 377 326 V 394 H 490 V 445'),edge('generator','model','','bottom','M 612 326 V 394 H 490 V 445'),edge('eval','model','','bottom','M 847 326 V 394 H 490 V 445'),edge('model','cli','默认','bottom','M 400 521 V 565 H 260 V 600',[335,563]),edge('model','api','配置 API 后','bottom','M 580 521 V 565 H 720 V 600',[650,563])],
    mermaid: `flowchart TD
  UI[原生 HTML / CSS / JavaScript] -->|JSON| HTTP[server.py HTTP 编排与权限]
  HTTP <--> DB[(store.py / SQLite)]
  HTTP --> N[needs.py 需求]
  HTTP --> M[matching.py 关联]
  HTTP --> G[generator.py 配比编排]
  HTTP --> E[evaluator.py 独立复核]
  HTTP --> L[learning.py 审核规则]
  G --> C[engine.py 确定性计算]
  L --> M
  N --> MC[model_client.py 配置快照]
  M --> MC
  G --> MC
  E --> MC
  L -->|管理员单独提炼| MC
  MC --> CLI[默认 Codex CLI]
  MC --> API[配置后 JSON Schema API]`
  },
  review: {
    title: '复核与报告回退', width: 970, height: 880,
    description: 'unknown 与 fail 分开处理；文字修改也要再次复核。',
    note: '五项键名为 selection / dosage / forbidden / duplication / contraindication。模型通过只证明提供资料的一致性。文字修改未通过时保留原报告；改变资料版本后必须重新生成。',
    nodes: [node('check',330,35,'本地校验完成的计算草案','商品、日量、报价均可核对'),node('eval',330,160,'执行独立五项 eval','不同提示词，同一配置快照'),node('block',20,325,'任一 fail / 调用失败','拦截新报告，展示原因','danger',280),node('unknown',345,325,'无 fail，存在 unknown','needs_review；提醒原样保留','warning',280),node('pass',670,325,'五项全部 pass','资料一致性通过，仍是草案','system',280),node('draft',490,500,'输出可预览 / 导出的草案','保留商品、标签用量与报价','human'),node('edit',490,625,'人工修改四类文字','产品 / 数值不变；重新 eval','human'),node('keep',45,740,'修改冲突 / 失败 / 取消','保留原报告','danger'),node('save',490,740,'修改通过 / 仅提醒','更新报告与导出，留存历史','human')],
    edges: [edge('check','eval'),edge('eval','block','fail / error','bottom','M 480 236 V 280 H 160 V 325',[212,278]),edge('eval','unknown','unknown'),edge('eval','pass','pass','bottom','M 480 236 V 280 H 810 V 325',[750,278]),edge('unknown','draft','','bottom','M 485 401 V 455 H 640 V 500'),edge('pass','draft','','bottom','M 810 401 V 455 H 640 V 500'),edge('draft','edit'),edge('edit','save','pass / needs_review'),edge('edit','keep','fail / error / 取消','left','M 490 663 H 400 V 778 H 345',[397,703])],
    mermaid: `flowchart TD
  A[本地校验完成的计算草案] --> B[独立五项 eval]
  B -->|任一 fail / 调用失败| X[不输出新报告，解释原因]
  B -->|无 fail，有 unknown| C[needs_review / 保留提醒]
  B -->|全部 pass| D[pass / 仍需专业复核]
  C --> E[展示并导出草案]
  D --> E
  E --> F[修改摘要、产品依据、生活方式、复查建议]
  F --> G[保持产品、日量、成分与报价 / 再次 eval]
  G -->|pass 或 needs_review| H[更新报告，保存历史]
  G -->|fail 或 error| I[保留原报告]
  F -->|取消| I`
  },
  learning: {
    title: '人工规则的审核与应用', width: 1000, height: 1090,
    description: '来源保留，规则审核后使用；商品或规则变化就失效。',
    note: '规则是选品参考，不是模型微调。未知背景不命中；不同条件组 AND，同组 OR，组合目标须全部满足。所有排除先执行；最多各 8 条摘要。全目标覆盖时可免关联调用，配比和独立 eval 始终继续。',
    nodes: [node('case',60,30,'管理员录入匿名案例','不可变来源 + 当时商品快照','human'),node('distill',60,145,'单独模型提炼 / 人工新增','模型最多提炼 8 条；保存不自动启用'),node('draft',60,260,'draft · 待审核','修改适用条件 / 商品 / 依据 / 局限','warning'),node('active',60,375,'active · 已审核启用','记录操作者、时间和规则版本','human'),node('inactive',580,375,'inactive · 停用','不进入后续规则匹配','warning'),node('match',60,490,'按当前目标和背景本地匹配','精确归一匹配；未知条件不命中'),node('invalid',580,490,'资料指纹变化 / 规则变更','不直接复用；要求重新选品','warning'),node('exclude',60,605,'优先排除命中商品','全部排除生效；模型仅接收短摘要'),node('coverage',60,720,'检查是否覆盖全部目标','规则商品也须通过标签 / 禁忌校验'),node('batch',580,720,'覆盖不足 / 无可复用规则','恢复批量模型关联'),node('confirm',60,845,'使用者确认合格商品','规则不改剂量与确认商品集合','human'),node('generate',60,970,'配比 + 本地计算 + 独立 eval','规则不能跳过任何后续校验')],
    edges: [edge('case','distill'),edge('distill','draft'),edge('draft','active','人工审核'),edge('active','inactive','停用','right'),edge('active','match'),edge('match','invalid','不一致','right'),edge('match','exclude','匹配且指纹有效'),edge('exclude','coverage'),edge('coverage','confirm','完整覆盖'),edge('coverage','batch','不足','right'),edge('batch','confirm','','bottom','M 730 796 V 883 H 360'),edge('confirm','generate')],
    mermaid: `flowchart TD
  A[管理员保存匿名来源与商品快照] --> B[单独提炼规则 / 人工新增]
  B --> C[draft 待审核]
  C -->|人工确认| D[active 启用]
  D -->|编辑| C
  D -->|停用| X[inactive]
  X -->|再次审核| D
  D --> E[按目标 / 背景精确匹配与商品指纹核验]
  E -->|未知背景 / 商品资料变化| Y[不复用规则，重新选品]
  E -->|有效| F[本地优先执行全部排除]
  F --> G{合格规则商品覆盖全部目标?}
  G -->|是| H[复用审核关联，省去关联模型调用]
  G -->|否| I[执行批量关联]
  H --> J[人工确认商品]
  I --> J
  J --> K[配比 / 本地计算 / 独立 eval 始终保留]`
  }
};
let currentFlow = 'user';
let zoom = 1;
const xml = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[char]));
function renderFlow() {
  const graph = flows[currentFlow];
  $('#flow-title').textContent = graph.title;
  $('#flow-description').textContent = graph.description;
  $('#flow-note').replaceChildren();
  const noteTitle = document.createElement('strong'); noteTitle.textContent = '读图说明与依据';
  const noteText = document.createElement('p'); noteText.textContent = graph.note;
  $('#flow-note').append(noteTitle, noteText);
  $('#mermaid-source').textContent = graph.mermaid;
  $('#flow-panel').setAttribute('aria-labelledby', `flow-tab-${currentFlow}`);
  const palette = {system:['#f8f7fb','#d4d1df','#282637'],human:['#eeebf7','#a49ac8','#514480'],warning:['#faf2e5','#d6b685','#795b2e'],danger:['#f9ebee','#d6a0ac','#a04458']};
  const edges = graph.edges.map(item => {
    const from = graph.nodes.find(n => n.id === item.from), to = graph.nodes.find(n => n.id === item.to);
    let x1 = from.x + from.width / 2, y1 = from.y + from.height, x2 = to.x + to.width / 2, y2 = to.y;
    if (item.side === 'right') {x1 = from.x + from.width; y1 = from.y + from.height / 2; x2 = to.x; y2 = to.y + to.height / 2;}
    if (item.side === 'left') {x1 = from.x; y1 = from.y + from.height / 2; x2 = to.x + to.width; y2 = to.y + to.height / 2;}
    const line = item.path || `M ${x1} ${y1} L ${x2} ${y2}`;
    const [lx, ly] = item.labelAt || [(x1+x2)/2+(item.side==='bottom'?42:0),(y1+y2)/2-(item.side==='bottom'?0:10)];
    return `<path d="${line}" fill="none" stroke="#a29cac" stroke-width="1.5" marker-end="url(#arrow)"/>${item.label ? `<text x="${lx}" y="${ly}" text-anchor="middle" font-size="11" fill="#71677e" stroke="#f8f7fb" stroke-width="5" paint-order="stroke">${xml(item.label)}</text>` : ''}`;
  }).join('');
  const nodes = graph.nodes.map(item => {
    const [fill, stroke, color] = palette[item.type];
    return `<g><rect x="${item.x}" y="${item.y}" width="${item.width}" height="${item.height}" rx="8" fill="${fill}" stroke="${stroke}"/><text x="${item.x+item.width/2}" y="${item.y+31}" text-anchor="middle" font-size="14" font-weight="600" fill="${color}">${xml(item.title)}</text><text x="${item.x+item.width/2}" y="${item.y+55}" text-anchor="middle" font-size="10.5" fill="#71677e">${xml(item.subtitle)}</text></g>`;
  }).join('');
  const auxiliary = currentFlow === 'architecture' ? '<text x="495" y="745" text-anchor="middle" font-size="11" fill="#71677e">本地计算由编排调用，审核规则参与选品；案例提炼单独调用模型。</text>' : currentFlow === 'learning' ? '<path d="M 60 413 H 25 V 298 H 60" fill="none" stroke="#a29cac" stroke-width="1.5" marker-end="url(#arrow)"/><text x="24" y="230" font-size="10" fill="#71677e">编辑转待审核</text>' : '';
  $('#diagram').innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${graph.width} ${graph.height}" role="img" aria-labelledby="diagram-title diagram-desc" style="font-family:Segoe UI,Microsoft YaHei,sans-serif"><title id="diagram-title">${xml(graph.title)}</title><desc id="diagram-desc">${xml(graph.description+' '+graph.note)}</desc><rect width="${graph.width}" height="${graph.height}" fill="#f8f7fb"/><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#a29cac"/></marker></defs>${edges}${auxiliary}${nodes}</svg>`;
  applyZoom();
}
function applyZoom() {
  const graph = flows[currentFlow], svg = $('#diagram svg');
  if (!svg) return;
  svg.style.width = `${graph.width * zoom}px`;
  svg.style.height = `${graph.height * zoom}px`;
  $('#zoom-label').textContent = `${Math.round(zoom*100)}%`;
  $('#zoom-out').disabled = zoom <= .4;
  $('#zoom-in').disabled = zoom >= 1.8;
}
function fitFlow() {
  zoom = Math.min(1, Math.max(.4, ($('#diagram-viewport').clientWidth-44)/flows[currentFlow].width));
  applyZoom();
}
$$('[data-flow]').forEach(button => button.addEventListener('click', () => {
  currentFlow = button.dataset.flow;
  $$('[data-flow]').forEach(tab => {const active = tab === button; tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;});
  renderFlow(); fitFlow();
  $('#diagram-viewport').scrollTo({top:0,left:0});
}));
$('.flow-tabs').addEventListener('keydown', event => {
  const tabs = $$('[data-flow]'), index = tabs.indexOf(document.activeElement);
  if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
  event.preventDefault();
  const next = event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
  tabs[next].click(); tabs[next].focus();
});
$('#zoom-out').addEventListener('click', () => {zoom = Math.max(.4, zoom-.1); applyZoom();});
$('#zoom-in').addEventListener('click', () => {zoom = Math.min(1.8, zoom+.1); applyZoom();});
$('#zoom-fit').addEventListener('click', fitFlow);
$('#download-svg').addEventListener('click', () => {
  const svg = $('#diagram svg').cloneNode(true);
  svg.style.removeProperty('width'); svg.style.removeProperty('height');
  svg.setAttribute('width', flows[currentFlow].width); svg.setAttribute('height', flows[currentFlow].height);
  download('<?xml version="1.0" encoding="UTF-8"?>\n'+new XMLSerializer().serializeToString(svg), `配方笺-${flows[currentFlow].title}.svg`, 'image/svg+xml;charset=utf-8');
  toast('已导出当前流程图 SVG。');
});
$('#download-mermaid').addEventListener('click', () => {download(flows[currentFlow].mermaid+'\n', `配方笺-${flows[currentFlow].title}.mmd`, 'text/plain;charset=utf-8');toast('已导出 Mermaid 源文件。');});
window.addEventListener('hashchange', navigate);
navigate();
