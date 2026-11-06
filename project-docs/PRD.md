# 配方笺 · 产品需求文档

文档版本：1.0.0
内容快照：2026-10-12
状态：依据当前实现整理，待产品确认

## 产品定位与目标

产品名称为配方笺，工程目录为 COM agent。面向已有专业健康分析的产品搭配人员，辅助将分析转换为有依据的营养补充剂 / 保健食品组合草案。

核心目标：减少重复整理与核算；让健康需求、商品关联、标签日量、费用和复核判断能够逐项追溯；在资料变化时阻止旧结果被误用。

当前成功口径：示例流程可运行，资料缺失可定位，确认商品集合不被模型改写，日量与报价可复算，报告和操作历史可恢复。没有已采集的商业增长指标或临床有效性指标。

**需求状态说明**下文“已实现”表示当前源码和测试中存在对应行为，不表示本次已重新运行所有业务测试。后续建议单独标注，尚未视为已获批准的开发需求。

 

## 用户角色与权限

| 角色 | 主要任务 | 权限边界 |
| --- | --- | --- |
| 搭配人员 / 工作台使用者 | 用户管理、分析输入、需求确认、选品、报告、产品核对 | 浏览器工作空间内资料；没有逐用户账号认证 |
| 管理员 | 首次设置 / 登录、跨工作空间用户与历史查看、提示词和规则管理 | 独立管理员会话，管理 API 服务端校验 |
| 专业复核人员 | 对个体方案、剂量、适用与合用依据进行实际专业审核 | 当前没有独立角色账号、签名或审批发布流 |
| 报告接收者 | 阅读人工导出的 Markdown / JSON / PDF 草案 | 当前没有客户端门户或在线分享系统 |

浏览器更换或 Cookie 清除会进入新工作空间，旧资料仍由管理员可见。姓名 / 编号用于关联个体资料，不能视为身份验证。

 

## 范围与交付物

### 当前范围

- 本机工作台与独立管理员后台；本地 SQLite 持久化。
- 用户资料、专业分析、产品库与示例数据。
- 需求提取 → 确认需求 → 产品关联 → 确认商品 → 配比 → 本地计算 → 独立 eval。
- 报告预览、受限文字编辑 / 复核、Markdown / JSON 导出、浏览器打印 PDF。
- 人工案例录入、单次规则提炼、人工审核启停、条件匹配和版本追溯。

### 当前范围外

- 从原始检验指标自动诊断、自动判定补充剂适应证。
- 实时价格、库存 / 订单 / 支付、全局最优组合搜索。
- 完整药物相互作用知识库、临床安全承诺、电子签名与正式方案发布。
- 多人云端协作、逐用户登录、模型微调、自动学习未经审核案例。

交付物为待专业复核的草案，内容含用户识别、目标原文依据、商品与日量、成分和报价、缺项、风险与复核结果。

 

## 信息架构与输入约束

工作台主流程四步：分析资料、需求确认与选品、选品结果、组合报告。侧栏提供用户选择 / 编辑、产品资料库、匹配规则；管理员后台提供用户、使用历史、功能提示词、人工案例学习。

| 输入组 | 字段与约束 | 缺失处理 |
| --- | --- | --- |
| 用户标识 | 姓名 / 称呼必填，编号必填；姓名最长 60，编号最长 80 字符 | 前端阻止空值；编号在工作空间内唯一 |
| 专业分析 | 自由格式非空文本，支持文字文件导入；保留分析审核信息 | 不替用户编造结论；提取无目标则提示核对 |
| 健康背景 | 年龄 0～120、孕哺、过敏、疾病、药物、补充剂、禁用商品 | “未知”和明确“无”分开；未知进入缺项 / 提醒 |
| 周期与费用 | 周期为 1～365 整数天；预算选填；运费 / 优惠为空按 0；金额非负 | 非法数值拒绝；预算空值不限制 |
| 产品核对 | ID / SKU、日量、单位、标签上限、包装、标签来源、成分、过敏原、禁忌、依据 | 逐商品列缺项，不能把未核验商品当成可执行候选 |
| 价格 | CNY、与 SKU 一致，来源、价格日期与有效期 | 缺失 / 过期 / 非 CNY / SKU 不符显示待报价 |
| 导入 | .xlsx 或本应用 .json 产品数组；请求体不超过 15 MB | .xls 提示另存为 .xlsx；JSON 格式或 ID 重复拒绝 |

网页已移除其他成分摄入及个体剂量调整输入；后端仍保留相关数据校验能力。文档不能把后端能力当作当前前端已开放的功能。

 

## 功能需求

下面的 FR 编号由本次文档定义，用于今后的开发讨论与验收定位，不是历史发布版本号。

 

### FR-01 用户资料与恢复 已实现

支持新建、编辑、选择用户；按用户分别保存专业分析、健康背景、审核信息、周期、预算、提取、商品勾选与报告。切换 / 刷新恢复对应用户状态；异步结果不可写到切换后的其他用户。示例载入进入独立示例用户。

出处：web/app.js · store.py · test_users.cjs

 

### FR-02 产品资料导入与核对 已实现

支持三种库视图、搜索商品、查看原始来源 / 规格、核对字段、保存本机修改与 JSON 备份。保持原始 Excel 不变，同名异规格不合并。归档 40 条使用 Demo 补齐时显式展示假设和示例属性，保留 label_reviewed / ingredients_complete 真实状态。

出处：engine.py · archive_demo.py · web/app.js · test_archive_demo.py

 

### FR-03 提取需求 已实现

模型仅整理专业分析明确的方向或补充需求，返回摘要、目标、逐字依据、风险、缺项及状态。本地校验输出结构、目标不重复、引用为原文连续子串；否定 / 未确认需求不进入目标。不在此步骤推荐产品或用量。

出处：needs.py · needs_schema.json · test_needs.py

 

### FR-04 人工确认与沿用 已实现

至少勾选一个提取目标后进入选品。提取记录必须属于当前工作空间 / 用户，且版本与资料一致；如明确沿用旧需求，保留提取原分析并按最新背景重新判断。沿用不等于适用证明，不能复用旧选品直接生成。

出处：server.py · store.py:extraction_context · test_needs.py / test_selection.py

 

### FR-05 关联选品 已实现

模型调用前排除禁用、停用、规则排除商品。一次批量比较允许商品，最多召回 10 款有关联 / 待核验候选；关联明确商品必须附目标索引和逐字商品证据。本地商品身份与资料引用核验不可省略。资料缺项商品可以展示理由，但不能勾选为合格商品。

出处：matching.py · generator.py:select_products · test_matching.py / test_selection.py

 

### FR-06 确认商品后生成 已实现

至少确认一款 eligible 商品；确认集合无重复、无库外 ID。生成时验证记录归属、用户版本、分析、商品库、模型和适用规则仍一致；复用确认关联记录，不重复全库关联。模型不能替换或增减商品；组合无法计算或明确冲突则解释并拦截。

出处：generator.py:generate_output · store.py:selection_context · server.py · test_selection.py

 

### FR-07 配比与计算 已实现

模型根据确认目标与商品提供用量和搭配依据；本地程序重新核对原标签日量、上限和商品身份，按周期计算成分与报价。不得使用模型生成的价格。预算超出、禁用商品、标签日量不一致等明确问题不得生成报告。

出处：generator.py · engine.py · compose_schema.json · test_generator.py / test_engine.py

 

### FR-08 五项独立复核 已实现

配比与复核使用同一请求的模型配置快照、不同提示词。eval 必须完整返回 selection / dosage / forbidden / duplication / contraindication 五项。按 fail / unknown / pass 聚合状态；提醒保留到草案，明确冲突或调用失败不交付。复核阶段保持独立调用。

出处：evaluator.py · eval_schema.json · model_client.py · test_evaluator.py

 

### FR-09 报告预览与导出 已实现

报告展示用户姓名 / 编号、目标原文、商品依据 / 日量、成分、购买费用 / 消耗成本、提醒与复核记录。下载 Markdown 与 JSON；PDF 由浏览器打印产生。文件名含用户编号，Demo 报告显式标记。返回上一步保留已有输入与结果。

出处：engine.py:markdown · web/app.js · test_users.cjs

 

### FR-10 编辑报告文字 已实现

只开放摘要、产品依据、生活方式、复查建议。编辑不影响已计算产品、日量、成分与报价。保存前重新 eval，通过或仅提醒才更新。失败 / 拦截 / 取消保持原报告；记录源报告、修改字段、输入与实际提示词快照。

出处：report_editor.py · store.py:finish_edit · test_report_editor.py

 

### FR-11 管理员与历史 已实现

首次本人设置独立账号密码；登录后查看所有本机工作空间用户、按姓名 / 编号搜索、查看分页历史及详情、按用户筛选。提取、选品、配比 / eval、文字修改、导入和规则提炼记录成功 / 拦截 / 失败状态；五套实际提示词及其哈希版本只读。

出处：server.py · store.py · web/admin.js · test_admin.py

 

### FR-12 人工案例与规则审核 已实现

管理员录入匿名案例与商品快照，保存本身不调用模型。提炼单独触发，最多返回 8 条草案；允许人工新增。来源案例不可变，更正用新记录。规则保存变为 draft，人工确认后 active，停用为 inactive；修改与状态变更增加版本并保留操作者。

出处：learning.py · store.py · web/learning.js · test_learning.py

 

### FR-13 规则应用与失效 已实现

仅使用当前启用规则，按目标和健康背景精确匹配，未知条件不命中；标签 / 成分 / 适用资料 / 禁忌变化导致指纹失效。全部命中排除先本地执行；规则摘要最多 8 条关联 + 8 条排除。全部目标由合格规则商品覆盖时可免关联模型调用；配比 / eval 仍执行。选品后规则变化要求重新选品。

出处：learning.py:rule_context / learned_matching · matching.py · server.py · test_learning.py

  

## 业务规则与计算口径

1. **目标以原文为准。**不为产品销售改变专业分析；每个模型目标与关联证据可逐字核验。
2. **确认不覆盖校验。**人工确认商品后仍检查禁用、资料、日量、禁忌和累计摄入；Demo 仍保留安全检查。
3. **禁用匹配。**ID、SKU、完整名称精确匹配；模糊名称无法保证识别。已停用和命中排除规则商品在模型前排除。
4. **缺项与冲突分开。**不完整产品资料阻止成为 eligible；适用 / 合用 / 膳食等未知可成为草案提醒。明确冲突拦截。
5. **数量与费用确定计算。**包装必须与日量同单位，价格必须有效且与 SKU 一致。
6. **成分质量换算。**g、mg、µg 按质量统一；IU 不自动换算。其他摄入未知不能记为 0；仅对提供的上限作比较。
7. **规则不能改变标签剂量。**案例实际用量仅作为来源，不作为自动剂量调整记录。

```text
周期需要量 = 标签每日量 × 周期天数
购买包装数 = ceil(周期需要量 ÷ 每包装含量)
购买费用 = 购买包装数 × 单包装有效价
消耗成本 = 周期需要量 ÷ 每包装含量 × 单包装有效价
剩余量 = 购买包装数 × 每包装含量 − 周期需要量
组合购买总价 = Σ购买费用 + 运费 − 优惠
```

优惠不能超过已知商品购买金额与运费之和，超过即拒绝。有任何待报价商品时不伪造完整总价。已知总价超过填写预算时阻止报告输出。费用使用 Decimal 计算并按金额取两位小数。

就医 / 复查优先、明确要求暂停搭配、儿童及已提供累计摄入超过提供上限时暂停搭配。疾病 / 用药 / 禁忌的本地检查使用已有文本与模型判断，没有完整专业相互作用库。

出处：engine.py:product_checks / quote / compose · generator.py · evaluator.py

 

## 状态、异常与回退

| 阶段 | 状态 | 界面 / 后续行为 |
| --- | --- | --- |
| 提取 | ready / needs_review / block | 目标可确认 / 补资料 / 暂停流程 |
| 选品 | ready | 至少存在 eligible 商品，允许勾选后生成 |
| 选品 | needs_review / block / error | 展示缺项、冲突或错误；超时保留需求，可重新选品 |
| 配比 | proposed / needs_review / block | 可计算组合继续复核；提醒保留；明确冲突停止 |
| eval | 任一 fail → block | 不显示 / 导出新报告，解释冲突项 |
| eval | 无 fail，存在 unknown → needs_review | 展示草案、日量、报价和待确认提醒 |
| eval | 全部 pass → pass | 展示草案，仍需专业复核 |
| 任一模型阶段 | 超时 / 配置不完整 / 结构异常 / 忙碌 | 说明失败与可重试动作，不改用本地规则直接放行 |
| 输入 / 库 / 适用规则变化 | 记录过期 | 重新选品；明确沿用提取仅放宽提取归属版本使用 |
| 报告编辑 | fail / error / 取消 | 保留原报告 |

一个进程内共享模型并发槽位为 1，忙碌请求给出提示。CLI 关联阶段超时 300 秒、推理强度 medium，其他阶段超时 180 秒；这些是调用上限，不是响应时间承诺。

当已确认商品仍可按标签计算，但配比仅返回提醒并清空商品时，后端可生成明确标注的标签计算草案并继续独立 eval；该回退不会把 unknown 改成 pass，也不会绕过明确冲突。

 

## 数据结构与持久化

数据库为 COM agent/data/health.sqlite3；当前共 8 张表。JSON 字段保存输入 / 结果 / 快照；没有本次文档新建的业务数据库。

| 表 | 主要字段 | 用途 |
| --- | --- | --- |
| `admins` | 单一 id、username、salt、password_hash | 管理员账号与加盐密码哈希 |
| `sessions` | token_hash、expires | 独立管理员会话 |
| `users` | key、workspace、user_id、name、fields、revision | 用户资料与恢复状态，工作空间内编号唯一 |
| `history` | id、user_key / revision、action、status、request、result、prompts、时间 | 生成 / 导入 / 编辑 / 提炼历史及提示词快照 |
| `catalogs` | workspace + kind、products | 各工作空间保存的产品库 |
| `learning_cases` | id、title、data、created_by、created_at | 不可变匿名来源案例及商品快照 |
| `learned_rules` | id、case_id、status、revision、data、操作者 / 时间 | 当前规则与审核状态 |
| `rule_versions` | rule_id + revision、data、actor、at | 逐版本规则操作记录 |

### 主要对象

- 提取记录：status、summary、goals、cautions、missing、model、history_id；目标含 name / evidence / catalog_goal。
- 选品记录：products、matching、learning、confirmed_goals、history_id；候选含目标索引、资料引用、missing / conflicts / eligible。
- 组合结果：report、markdown、generation、evaluation、matching、history_id；无报告时 report / markdown 为 null。
- 规则：kind 为 select / combine / exclude；conditions、product_ids、reason、limitations、商品指纹、来源案例与 revision。

记录归属 / revision 由服务端校验。数据库不保存 API 密钥；密钥位于本机环境 / .env。停止业务应用后复制数据库可备份；当前没有内置数据库恢复 / 迁移管理界面。

 

## 接口清单

业务服务默认为 http://127.0.0.1:8765。POST 使用 JSON；以下为当前路由清单，详细结构以 server.py、Schema 和测试为准。

| 方法 / 路径 | 主要输入 | 用途 / 权限 |
| --- | --- | --- |
| GET /api/bootstrap | 工作空间 Cookie | 示例、归档、保存产品库、当日日期 |
| GET /api/users | 工作空间 Cookie | 当前工作空间用户 |
| POST /api/users/save | key、revision、fields、demo | 保存用户 |
| POST /api/catalogs/save | kind、products | 保存产品库 |
| POST /api/import | filename、Base64 data | 解析 Excel / JSON；不等同于自动保存库 |
| POST /api/extract | user_key / revision、case、catalog | 提取健康需求并记录历史 |
| POST /api/select | 用户、case、catalog、extraction_id、confirmed_goals、可选 reuse_extraction | 检验提取记录，应用审核规则，批量关联 |
| POST /api/compose | 上列资料、selection_id、confirmed_products | 工作台确认后配比 / 复核；后端兼容直接 case 客户端 |
| POST /api/reports/edit | user_key / revision、history_id、changes | 编辑四类文字并复核 |
| GET /api/admin/status | 管理员 Cookie（可无） | 首次设置状态与当前账号 |
| POST /api/admin/setup | username、password | 仅首次允许 |
| POST /api/admin/login | username、password | 创建会话，失败限流 |
| POST /api/admin/logout | 管理员 Cookie | 注销会话 |
| GET /api/admin/users | 管理员 Cookie | 所有本机用户，管理员 |
| GET /api/admin/user?key=… | 用户 key | 单用户详情，管理员 |
| GET /api/admin/history | offset、可选 user_key | 历史分页 / 筛选，管理员 |
| GET /api/admin/history/detail?id=… | 历史 id | 输入、结果与快照，管理员 |
| GET /api/admin/prompts | 管理员 Cookie | 五套只读提示词及 Schema |
| GET /api/admin/learning | 管理员 Cookie | 案例、规则与商品列表 |
| GET /api/admin/learning/case?id=… | 案例 id | 来源详情，管理员 |
| GET /api/admin/learning/versions?id=… | 规则 id | 规则版本，管理员 |
| POST /api/admin/learning/case | 案例资料 | 不可变来源入库，管理员 |
| POST /api/admin/learning/distill | case_id | 单独模型提炼，管理员 |
| POST /api/admin/learning/rule | case_id、rule、可选 id / revision | 新增 / 修改为待审核，管理员 |
| POST /api/admin/learning/state | id、revision、status、reviewed | 审核启用 / 停用，管理员 |

服务端拒绝跨来源提交及非本机 Host。管理读取未授权返回 401，权限失败提交返回 403；不存在路由返回 404，非法输入通常返回 400，重复首次设置 409，登录失败限流 429。模型拦截可能是正常 JSON 业务状态，不能只根据 HTTP 200 判断报告是否生成。

工作台的两次人工确认是主界面契约；兼容直接 case 的后端路径仍存在。若后续要求所有外部调用必须使用确认链路，需要单独修改并验证兼容性。

 

## 非功能要求与实现边界

- **部署：**业务仅监听 127.0.0.1；Python 3.11+，openpyxl 3.1～4 之间。本说明网页是另一独立静态服务。
- **认证：**管理员密码 PBKDF2-SHA256，600,000 次迭代；会话 token 存哈希，8 小时到期；退出立即失效。5 分钟内 5 次失败登录后限制。
- **可追溯：**输入、结果、状态、用户版本、实际提示词快照 / 哈希留存；人工规则另有逐版本记录。
- **模型配置：**默认复用已登录本机 Codex CLI；API 需基址、密钥、模型完整配置和严格 JSON Schema。环境变量优先于 .env；每请求使用配置快照。
- **模型调用：**需求、关联、配比、eval 为独立提示词；案例提炼是第五套。CLI 临时会话、禁工具、只读沙箱，不继承当前聊天历史。
- **性能：**一次批量关联、最多 10 候选、最多 32 组进程内缓存；规则覆盖可免关联调用。没有固定耗时 / Token SLA。配比与 eval 不缓存。
- **信息流：**健康原文、背景、商品标签与组合发送到所选模型服务；结构化姓名 / 编号不单独发送，原文中的身份信息可能仍发送。管理员提炼会发送来源案例。
- **易用性：**缺项指出字段、忙碌 / 超时提示重试、刷新恢复、键盘控件和文本状态。原工作台已有窄屏样式，本次不重新证明业务页的全部无障碍指标。

当前没有专门的自动删除 / 保留期限管理、完整日志脱敏审计、云备份、临床发布或加密数据库功能；这些需要后续范围确认。

 

## 验收清单与已有验证

这些验收项由当前测试及 README 反推。本次新建文档不会向真实模型发送业务数据，也不运行会改写日常数据库的 HTTP 测试。

| 编号 | 验收场景与预期 | 已有测试出处 |
| --- | --- | --- |
| AC-01 | 切换 / 刷新恢复各用户；异步结果仍归发起用户 | test_users.cjs |
| AC-02 | 目标 / 产品引用不在原文，结构异常或目标重复被拒绝 | test_needs.py / test_matching.py |
| AC-03 | 提取属于他人、版本变化、选品记录过期时不能生成 | test_selection.py / test_admin.py |
| AC-04 | 确认 A / C 后模型不得增减商品，生成不重复全库关联 | test_selection.py |
| AC-05 | 禁用、停用、已知过敏、禁忌、超量被排除 / 拦截 | test_engine.py / test_generator.py |
| AC-06 | 有效价格与包装计算正确；未知 / 过期价待报价 | test_engine.py |
| AC-07 | eval 任一 fail 拦截；unknown 保留提醒与草案 | test_evaluator.py / test_generator.py |
| AC-08 | 编辑仅改文字；冲突 / 失败 / 取消保持原报告 | test_report_editor.py / test_users.cjs |
| AC-09 | 管理员首次设置、权限、注销、过期和跨来源拒绝 | test_admin.py |
| AC-10 | Demo 补齐 40 条，原 Excel 与真实审核状态保留 | test_archive_demo.py |
| AC-11 | 规则修改转 draft；审核启用、停用和版本可追溯 | test_learning.py |
| AC-12 | 未知背景 / 商品变化不复用；排除优先；完整规则覆盖省关联，配比与 eval 保留 | test_learning.py |
| AC-13 | HTTP 输入、导入、禁用导致无报告等接口行为 | test_http.py，必须指向隔离数据库服务 |

### 真实模型验证记录

README 记载 2026-10-11 对 40 条归档商品执行一次虚构蛋白质 / B6 需求的真实选品：耗时 69.5 秒，返回 4 款候选，eligible 为 0，结果 needs_review，未生成完整组合报告。详见 data/selection-live-check.json 与 selection-live-result.json。这个单次结果不代表全流程、所有人群或当前 Demo 补齐后的真实模型验收。

README 另记载虚构资料的生成 / 独立复核和拒绝案例；缺少统一版本与时间证据，未把该文字描述升级为本次实测结果。

### 建议以后采集的指标 待确认

流程完成率、资料缺项率、人工修改率、可复算费用错误率、过期记录拦截率、各阶段耗时与调用次数、规则命中率。尚无埋点口径或指标目标。

 

## 后续决策与建议顺序

| 建议优先级 | 事项 | 需要决定什么 |
| --- | --- | --- |
| P0 · 建议 | 真实产品资料补齐 | 审核责任、现售标签来源、适用市场、完整成分和价格有效期 |
| P0 · 建议 | 正式审核与发布边界 | 草案何时可转正式方案，审核角色 / 签名 / 责任如何记录 |
| P1 · 建议 | 数据保留与恢复 | 数据库备份恢复、删除 / 保留期限、个人资料治理 |
| P1 · 建议 | 版本与验证基线 | 建立 Git、记录每次变更、隔离验收环境、代表性真实模型验证 |
| P2 · 建议 | 效率与覆盖 | 更大产品库的召回 / 缓存策略，度量规则覆盖与模型成本 |

以上是文档整理发现的待决策事项，不是已承诺排期。产品负责人、目标人群、使用规模、正式审核制度及发布标准目前没有单独签署的需求文件，本 PRD 需作为开发基线逐项确认。
