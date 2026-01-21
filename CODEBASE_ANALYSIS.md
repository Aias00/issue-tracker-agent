# Issue Tracker Agent - 代码库分析报告

## 📋 项目概述

这是一个基于 FastAPI 的 GitHub Issue 跟踪和智能分析系统。系统能够：
- 从 GitHub 仓库获取 Issue
- 使用 LLM 对 Issue 进行智能分析
- 将分析结果通过飞书（Feishu）发送通知
- 使用 SQLite 存储 Issue 和分析数据

## 🏗️ 项目架构

### 目录结构
```
issue-tracker-agent/
├── app/
│   ├── config.py              # 配置管理
│   ├── jobs/
│   │   └── sync.py            # 核心业务逻辑：处理仓库 Issue
│   ├── storage/
│   │   └── sqlite_store.py    # SQLite 数据存储层
│   └── web/
│       └── server.py          # FastAPI Web 服务器
├── pyproject.toml             # 项目配置（uv 使用）
├── requirements.txt           # Python 依赖
└── start.sh                   # 启动脚本
```

## 📦 核心模块分析

### 1. `app/config.py` - 配置管理模块

**功能**：从环境变量加载配置，使用 dataclass 进行类型安全的配置管理。

**配置结构**：
- `GitHubConfig`: GitHub API 配置（token, repos, fetch_limit）
- `AgentConfig`: Agent 行为配置（限制、文本处理）
- `LLMConfig`: LLM 服务配置（base_url, api_key, model）
- `NotificationsConfig`: 通知配置（飞书 webhook）
- `AppConfig`: 应用配置（SQLite 路径）

**环境变量要求**：
- `GITHUB_TOKEN`: GitHub API token
- `REPOS`: 要监控的仓库列表
- `SQLITE_PATH`: SQLite 数据库路径
- `LLM_BASE_URL`: LLM API 基础 URL
- `LLM_API_KEY`: LLM API 密钥
- `LLM_MODEL`: LLM 模型名称
- `FEISHU_WEBHOOK_URL`: 飞书 webhook URL

**可选环境变量**（带默认值）：
- `PER_REPO_FETCH_LIMIT` (默认: 100)
- `MAX_NEW_ISSUES_PER_REPO` (默认: 5)
- `MAX_NEW_ISSUES_TOTAL` (默认: 20)
- `MAX_BODY_CHARS` (默认: 2000)
- `MAX_TITLE_CHARS` (默认: 100)
- `MAX_MISSING_ITEMS` (默认: 10)

### 2. `app/storage/sqlite_store.py` - 数据存储层

**功能**：SQLite 数据库操作封装，提供完整的 CRUD 功能。

**数据库表结构**：

1. **issues** - Issue 基本信息
   - 主键：`id`
   - 唯一约束：`(repo, issue_number)`
   - 字段：repo, issue_number, issue_id, issue_url, title, author_login, state, created_at, first_seen_at, last_seen_at

2. **issue_analysis** - Issue 分析结果快照
   - 主键：`id`
   - 外键：`issue_row_id` → issues(id)
   - 字段：issue_row_id, created_at, analysis_json, model_info_json
   - 索引：`(issue_row_id, created_at)`

3. **notifications** - 通知记录
   - 主键：`id`
   - 外键：`issue_row_id` → issues(id), `analysis_id` → issue_analysis(id)
   - 字段：issue_row_id, analysis_id, sent_at, channel, status, error, provider_response_json
   - 索引：`(issue_row_id, sent_at)`

4. **run_log** - 运行日志
   - 主键：`id`
   - 字段：run_at, repo, status, detail

**核心方法**：
- `init()`: 初始化数据库表结构
- `has_issue()`: 检查 Issue 是否已存在（去重）
- `upsert_issue()`: 插入或更新 Issue
- `insert_issue_analysis()`: 插入分析结果
- `insert_notification()`: 插入通知记录
- `log_run()`: 记录运行日志
- `list_issues()`: 查询 Issue 列表（支持分页和过滤）
- `get_issue()`: 获取单个 Issue
- `list_issue_analyses()`: 获取 Issue 的分析历史
- `get_analysis()`: 获取单个分析结果
- `list_notifications()`: 查询通知列表
- `get_notification()`: 获取单个通知
- `list_runs()`: 查询运行日志

**设计特点**：
- 使用外键约束保证数据完整性
- 支持软删除（通过 CASCADE）
- JSON 字段存储复杂数据结构
- 提供分页和过滤功能

### 3. `app/jobs/sync.py` - 核心业务逻辑

**功能**：处理单个仓库的 Issue，包括获取、分析、存储和通知。

**核心函数**：`process_repo_with_budget()`

**工作流程**：
1. 从 GitHub 获取仓库的 open issues（限制数量）
2. 遍历每个 Issue：
   - 检查预算（全局和单仓库限制）
   - 去重检查（基于 repo + issue_number）
   - 存储 Issue 基本信息
   - 截断文本（title 和 body）
   - 调用 LLM Agent 进行分析
   - 存储分析结果
   - 生成飞书卡片并发送通知
   - 记录通知状态
3. 记录运行日志

**预算控制**：
- `Budget`: 全局预算，控制总处理数量
- `max_new_issues_per_repo`: 每个仓库的最大处理数量
- 双重限制确保资源使用可控

**错误处理**：
- 捕获异常并记录到 run_log
- 通知发送失败时记录错误但不中断处理

**依赖的缺失模块**：
- `app.agent.graph.run_issue_agent`: LLM Agent 执行
- `app.agent.preprocess.clip_text`: 文本截断
- `app.llm.factory.LLMFactory`: LLM 工厂类
- `app.notifiers.feishu.renderer.render_card_template_b`: 飞书卡片渲染

### 4. `app/web/server.py` - Web API 服务器

**功能**：提供 RESTful API 接口（目前为占位实现）。

**API 端点**：

1. `POST /run` - 触发 Issue 处理
   - 当前：返回占位消息
   - 应该：调用 sync.py 的处理逻辑

2. `GET /runs` - 获取运行历史
   - 当前：返回空数组
   - 应该：从 SQLiteStateStore 查询 run_log

3. `GET /issues` - 获取 Issue 列表
   - 当前：返回空数组
   - 应该：从 SQLiteStateStore.list_issues() 获取

4. `GET /issues/{id}` - 获取单个 Issue
   - 当前：返回占位数据
   - 应该：从 SQLiteStateStore.get_issue() 获取

5. `GET /issues/{id}/analyses` - 获取 Issue 的分析历史
   - 当前：返回空数组
   - 应该：从 SQLiteStateStore.list_issue_analyses() 获取

6. `GET /analyses/{id}` - 获取单个分析结果
   - 当前：返回占位数据
   - 应该：从 SQLiteStateStore.get_analysis() 获取

7. `GET /notifications` - 获取通知列表
   - 当前：返回空数组
   - 应该：从 SQLiteStateStore.list_notifications() 获取

8. `GET /notifications/{id}` - 获取单个通知
   - 当前：返回占位数据
   - 应该：从 SQLiteStateStore.get_notification() 获取

**问题**：
- 所有端点都是占位实现，没有实际功能
- 配置加载方式不一致（直接使用 os.getenv，而不是使用 config.py）
- 没有初始化数据库连接
- 没有集成 sync.py 的处理逻辑

## ⚠️ 缺失的模块

根据 `sync.py` 的导入，以下模块需要实现：

### 1. `app/agent/` 目录
- `graph.py`: 包含 `run_issue_agent()` 函数
- `preprocess.py`: 包含 `clip_text()` 函数

### 2. `app/llm/` 目录
- `factory.py`: 包含 `LLMFactory` 类

### 3. `app/notifiers/feishu/` 目录
- `renderer.py`: 包含 `render_card_template_b()` 函数
- 可能还需要 `client.py`: 飞书 API 客户端（`send_card()` 方法）

### 4. GitHub 客户端
- `sync.py` 中使用了 `gh.list_recent_issues()`，但未定义
- 需要实现 GitHub API 客户端

## 🔄 数据流

```
GitHub API
    ↓
[获取 Issues]
    ↓
[去重检查] → SQLite (issues 表)
    ↓
[存储 Issue] → SQLite (issues 表)
    ↓
[文本预处理] (clip_text)
    ↓
[LLM 分析] → LLM API
    ↓
[存储分析结果] → SQLite (issue_analysis 表)
    ↓
[生成飞书卡片] (render_card_template_b)
    ↓
[发送通知] → Feishu Webhook
    ↓
[记录通知] → SQLite (notifications 表)
    ↓
[记录运行日志] → SQLite (run_log 表)
```

## 🎯 设计模式

1. **配置管理**：使用 dataclass 进行类型安全的配置
2. **数据访问层**：SQLiteStateStore 封装所有数据库操作
3. **预算控制**：使用 Budget 对象控制资源使用
4. **错误处理**：异常捕获和日志记录
5. **去重机制**：基于 (repo, issue_number) 的唯一约束

## 📝 待完成的工作

### 高优先级
1. ✅ 修复 `config.py` 中的语法错误（已完成）
2. ❌ 实现 `app/agent/graph.py` - LLM Agent 执行逻辑
3. ❌ 实现 `app/agent/preprocess.py` - 文本预处理
4. ❌ 实现 `app/llm/factory.py` - LLM 工厂类
5. ❌ 实现 `app/notifiers/feishu/` - 飞书通知模块
6. ❌ 实现 GitHub API 客户端
7. ❌ 完善 `server.py` 的 API 实现

### 中优先级
8. ❌ 添加错误处理和验证
9. ❌ 添加日志记录
10. ❌ 添加单元测试
11. ❌ 添加 API 文档（OpenAPI/Swagger）

### 低优先级
12. ❌ 添加数据库迁移脚本
13. ❌ 添加监控和指标
14. ❌ 添加配置验证

## 🛠️ 技术栈

- **Web 框架**: FastAPI
- **数据库**: SQLite
- **包管理**: uv
- **Python 版本**: >= 3.8
- **依赖**:
  - fastapi
  - uvicorn[standard]
  - requests
  - python-dotenv

## 💡 建议

1. **统一配置管理**：`server.py` 应该使用 `config.py` 的 `load_config_from_env()`
2. **初始化数据库**：在应用启动时调用 `SQLiteStateStore.init()`
3. **实现缺失模块**：按照依赖关系逐步实现缺失的模块
4. **API 实现**：将 `server.py` 的占位实现替换为实际的数据查询
5. **错误处理**：添加统一的错误处理中间件
6. **日志系统**：使用 Python logging 模块替代 print
7. **类型提示**：为所有函数添加完整的类型提示
