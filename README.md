# 配方笺 · AnliMatch

本地运行的健康产品组合工作台。录入已由专业人员分析的健康结论，确认健康需求，从产品库选品，再生成包含搭配依据、标签日量、成分合计、组合报价与复核提醒的报告。

## 下载后能直接双击使用吗？

| 内容 | 使用方式 |
| --- | --- |
| 业务工作台 | 首次安装 Python 和依赖后，双击 `COM agent/启动应用.cmd`，启动本地服务并打开浏览器 |
| 项目说明网页 | 双击 `project-docs/index.html` 即可离线阅读；此页面提供说明，不生成报告 |

业务工作台依赖 Python 后端，直接打开 `COM agent/web/index.html` 不能完整使用。生成需求、选品及复核还需要自己的模型账号或 API 配置；仓库不提供共享密钥或账号。

## 快速开始（Windows）

### 1. 获取项目

在 GitHub 点击 **Code → Download ZIP** 并完整解压，或使用 Git：

```powershell
git clone https://github.com/MuAngie/anlimatch.git
cd anlimatch
```

请保留目录结构；应用启动时会读取同级 `archive/` 中的产品 Excel。

### 2. 安装运行环境

安装 **Python 3.11 或更新版本**，安装时启用“Add Python to PATH”。在项目根目录打开终端：

```powershell
python --version
python -m pip install -r "COM agent/requirements.txt"
```

应用依赖 `openpyxl` 读取 Excel。前端无需 npm 安装或构建。Node.js 仅用于前端测试，或启动独立的项目说明网页服务。

### 3. 配置模型（二选一）

**方式 A：使用本机 Codex CLI。** 本机需安装并登录 Codex CLI，使用自己的账号与模型配置。不配置下列 API 环境变量时，应用默认使用此方式。启动器会优先使用本机已有的 Codex Python 运行环境，否则使用 PATH 中的 `python`；所用环境均需安装项目依赖。

**方式 B：使用模型 API。** 将 `COM agent/.env.example` 复制为同目录的 `.env`，在本机填写：

```dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=<在本机填写自己的密钥>
OPENAI_MODEL=<支持结构化输出的模型名称>
```

接口需支持 `/chat/completions` 和严格的 JSON Schema 结构化输出。`OPENAI_BASE_URL` 填基础地址，不包含 `/chat/completions`；三项须同时配置。系统环境变量优先于 `.env`。

`.env.example` 仅包含空白占位。不要将真实密钥写进源码、README 或提交到 Git。健康分析及产品资料会发送给所配置的模型服务。

### 4. 启动应用

双击 `COM agent/启动应用.cmd`，或在项目根目录执行：

```powershell
cd "COM agent"
python server.py --port 8765
```

- 工作台：<http://127.0.0.1:8765/>
- 管理后台：<http://127.0.0.1:8765/admin>

服务仅监听本机。首次进入管理后台需自行创建管理员账号和密码，没有默认账号；密码长度为 8–128 个字符。关闭浏览器不会关闭启动器创建的后台服务；终端直接运行时可按 `Ctrl+C` 停止。

## 使用流程

1. 在左侧新增或选择用户，填写健康背景与禁用商品，粘贴已专业分析的文字结论。
2. 点击 **提取健康需求**，核对需求及其原文依据，确认本次目标。
3. 点击 **确认需求并选品**，查看候选商品、关联依据、标签资料和待核验原因。
4. 勾选本次使用的商品，确认后生成完整报告。程序计算标签日量、成分与整包装报价，模型说明组合关系并执行最终复核。
5. 查看报告中的提醒，按需修改分析摘要、搭配依据和建议文字；修改后重新复核。支持 Markdown、JSON 及浏览器打印 PDF。

预算可留空。必填项以星号标识；明确冲突会阻止输出，资料不足形成的提醒会保留在可输出的待复核草案中。按标签计算日量不代表已证明适用于某个真实用户。

## 主要功能

- 用户分类、按用户保存输入和报告、刷新后恢复。
- Excel 产品导入、资料核对与产品库 JSON 备份。
- 需求提取、批量选品、人工确认商品、组合说明与独立复核。
- 本地日量校验、成分合计、包装数量、报价和预算计算。
- 后台任务进度、取消、失败重试及复核阶段恢复。
- 管理员查看用户、使用历史、五套实际提示词与模型调用统计。
- 人工案例离线提炼规则，经审核后启用、修改或停用，可追溯来源；不做模型微调。

用户禁用商品、标签用量和安全校验优先于案例规则与模型结论。

## 产品库与数据保存

`archive/` 包含现有三份产品 Excel，共40条归档产品记录。原资料有历史信息及缺项，应用对部分参数使用明确标注的演示值，用于流程测试，不应视为现售标签或真实个体用量建议。另提供虚构产品和虚构用户示例。

新设备首次运行会创建独立的 `COM agent/data/health.sqlite3`，保存本机用户、产品修改、报告、历史、案例规则及管理员信息。浏览器工作空间通过 Cookie 区分。**原电脑的数据和管理员密码不会随 Git 下载。**

需要备份本地业务数据时，先停止服务，再备份上述数据库。数据库、密钥、历史备份、开发截图和运行日志已由 `.gitignore` 排除。

## 项目结构

```text
anlimatch/
├── COM agent/             # Python 后端、网页、提示词、校验器和测试
│   ├── web/               # 业务工作台与管理员界面
│   ├── examples/          # 虚构报告示例
│   ├── docs/              # 资料核对说明
│   ├── .env.example       # 空白配置模板
│   └── requirements.txt
├── archive/               # 启动所需的产品 Excel
├── project-docs/          # 可离线阅读的项目说明网页和 PRD 快照
├── PRODUCT.md             # 产品设计背景
└── DESIGN.md              # 界面设计约定
```

更详细的现有功能说明见 [应用 README](COM%20agent/README.md)。`project-docs/` 是独立的说明快照，功能细节以实际应用和源码为准。

## 测试

从 `COM agent/` 目录运行不连接真实模型的单元测试：

```powershell
python -m unittest test_engine test_needs test_matching test_generator test_selection test_evaluator test_report_editor test_learning test_model_optimization test_reliability test_archive_demo
node --test test_users.cjs
```

`test_admin` 使用隔离数据库及临时本机服务；`test_http` 会向 `HEALTH_TEST_URL` 指定的服务写入虚构测试资料，勿指向日常使用的数据库。最近一次完整回归为 105 项 Python 测试及 18 项前端测试通过；未据此声称真实模型的准确率或临床效果。

## 常见问题

- **双击启动后找不到 Python**：确认 Python 已安装且加入 PATH；重开终端后执行 `python --version`。
- **缺少 openpyxl**：在启动器实际使用的 Python 环境中安装 `requirements.txt`。
- **网页能打开但生成失败**：检查自己的 Codex 登录状态，或 API 三项配置、网络和额度；未配置可用模型时不会跳过模型复核。
- **提示词更新后旧任务无法续用**：保留输入，按当前版本重新发起相应步骤。

本项目用于搭配流程演示和专业人员辅助核对，输出包含待确认事项，真实使用仍需结合现售标签、完整个体资料及专业审核。
