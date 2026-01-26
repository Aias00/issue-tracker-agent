# Issue Tracker Agent with Vector Memory

一个基于 AI 的 GitHub Issue & PR 智能分析系统，支持向量记忆和本地代码库检索。

## ✨ 核心特性

### 🧠 智能分析
- **AI 驱动**：使用 LLM 自动分析 Issue 的优先级、分类和关键点
- **Bug 根因分析**：针对 Bug 类 Issue 提供深度根因分析
- **架构设计建议**：为 Feature 类 Issue 生成实现方案
- **PR Code Review**：🆕 支持输入 PR 链接进行智能代码审查

### 🔀 PR Review 功能 🆕
- **一键审查**：粘贴 PR URL 即可启动 AI 审查
- **全面分析**：代码质量、潜在问题、改进建议
- **风险评估**：自动评估变更风险等级
- **历史追溯**：所有 Review 记录持久化保存

### 📦 仓库管理 🆕
- **集中管理**：通过 Web UI 管理所有监控的仓库
- **灵活配置**：支持配置本地路径、自动同步开关
- **独立存储**：仓库配置持久化到数据库

### 🔍 向量记忆系统
- **语义代码搜索**：基于 pgvector 的向量相似度搜索
- **历史案例检索**：自动查找类似问题的解决方案
- **情景记忆**：每次分析后自动保存到知识库

### 💾 本地代码集成
- **本地代码库映射**：支持配置 GitHub 仓库到本地路径的映射
- **智能上下文检索**：分析时自动检索相关代码片段
- **多策略检索**：向量搜索 + 文本搜索 + 历史案例

### 📊 数据持久化
- **PostgreSQL + pgvector**：业务数据和向量数据统一存储
- **高性能索引**：HNSW 向量索引，毫秒级相似度搜索
- **事务一致性**：业务逻辑和向量更新在同一事务中

---

## 🚀 快速开始

### 方式 1：一键安装（推荐）

```bash
./setup.sh
```

脚本会自动完成：
1. 启动 PostgreSQL + pgvector
2. 安装 Python 依赖
3. 迁移旧数据（如果有）
4. 初始化数据库
5. 可选：建立代码索引
6. 启动 Web 服务

### 方式 2：手动安装

#### 1. 启动数据库
```bash
docker-compose up -d
```

#### 2. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env 文件，配置：
# - DATABASE_URL
# - GITHUB_TOKEN
# - LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
# - REPO_PATHS
```

#### 3. 安装依赖
```bash
pip install -r requirements.txt
```

#### 4. 迁移数据（可选）
```bash
python migrate_to_postgres.py \
  data/issue_tracker.db \
  "postgresql://issue_tracker:password@localhost:5432/issue_tracker"
```

#### 5. 建立代码索引
```bash
python tools/index_code.py \
  /path/to/local/repo \
  --repo-name owner/repo
```

#### 6. 启动应用
```bash
uvicorn app.web.server:app --reload --host 0.0.0.0 --port 8000
```

访问：http://localhost:8000

---

## 📖 使用指南

### 1. 配置 GitHub 仓库

在 Configuration 页面或 `.env` 文件中配置：
```bash
REPOS=apache/hertzbeat,owner/repo2
REPO_PATHS={"apache/hertzbeat": "/Users/you/workspace/hertzbeat"}
```

### 2. 采集 Issue

点击 "Run & Monitor" → "Run Now"，系统会：
- 从 GitHub 拉取最新的 Issue
- 存储到数据库（不自动分析）

### 3. 手动分析 Issue

1. 进入 "Issues" 页面
2. 点击某个 Issue 的 "View Details"
3. 点击 "🔄 Re-analyze with AI"

系统会：
- 检索本地代码库（如果配置了路径）
- 使用向量搜索找到相关代码
- 搜索历史相似案例
- 生成详细的分析报告

### 4. 查看分析结果

分析结果包含：
- **Summary**：Issue 摘要
- **Priority**：优先级（High/Medium/Low）
- **Category**：分类（Bug/Feature/Question/Documentation）
- **Key Points**：关键要点列表
- **Bug Root Cause**（Bug 类）：根因分析
- **Architecture Plan**（Feature 类）：实现方案

### 5. PR 代码审查 🆕

#### 通过 Web UI
1. 进入 Web 页面
2. 在 PR Review 区域粘贴 PR URL
3. 点击 "Review" 按钮
4. 等待 AI 分析完成

#### 通过 API
```bash
curl -X POST http://localhost:8000/prs/review \
  -H "Content-Type: application/json" \
  -d '{"pr_url": "https://github.com/owner/repo/pull/123"}'
```

支持的 PR URL 格式：
- `https://github.com/owner/repo/pull/123`
- `owner/repo#123`
- `owner/repo/pull/123`

审查结果包含：
- **Summary**：变更摘要
- **Potential Issues**：潜在问题（按严重程度）
- **Suggestions**：改进建议
- **Code Quality Score**：代码质量评分（1-10）
- **Risk Level**：风险等级（LOW/MEDIUM/HIGH）
- **Overall Assessment**：总体评价（APPROVE/REQUEST_CHANGES/COMMENT）

### 6. 仓库管理 🆕

#### 通过 Web UI
进入 "Repos" 页面管理所有监控的仓库。

#### 通过 API
```bash
# 列出所有仓库
curl http://localhost:8000/repos

# 添加/更新仓库
curl -X POST http://localhost:8000/repos \
  -H "Content-Type: application/json" \
  -d '{
    "full_name": "owner/repo",
    "local_path": "/path/to/local/repo",
    "is_active": true,
    "auto_sync_issues": true,
    "auto_sync_prs": false
  }'

# 删除仓库
curl -X DELETE http://localhost:8000/repos/1
```

---

## 🛠️ 高级功能

### 代码索引管理

#### 建立索引
```bash
python tools/index_code.py /path/to/repo --repo-name owner/repo
```

#### 强制重建索引
```bash
python tools/index_code.py /path/to/repo --repo-name owner/repo --force
```

#### 查看索引状态
```sql
-- 连接到数据库
docker exec -it issue-tracker-postgres psql -U issue_tracker

-- 查看索引统计
SELECT 
    repo, 
    COUNT(*) as chunks,
    COUNT(DISTINCT file_path) as files
FROM code_embeddings
GROUP BY repo;
```

### 向量搜索调优

编辑 `app/agent/graph.py`：

```python
# 调整相似度阈值
if similarity > 0.5:  # 降低阈值会返回更多结果

# 调整返回数量
results = memory_store.search_code_embeddings(
    query_embedding=query_embedding,
    repo=state['repo'],
    limit=10  # 增加返回数量
)
```

### 自定义嵌入模型

编辑 `app/web/server.py`：

```python
embeddings = OpenAIEmbeddings(
    base_url=CFG.llm.base_url,
    api_key=CFG.llm.api_key,
    model="text-embedding-3-large"  # 使用更高质量的模型
)
```

---

## 📁 项目结构

```
issue-tracker-agent/
├── app/
│   ├── agent/           # AI Agent 逻辑
│   │   └── graph.py     # LangGraph 工作流
│   ├── storage/         # 数据存储层
│   │   ├── pg_store.py      # PostgreSQL 业务存储
│   │   └── memory_store.py  # 向量记忆存储
│   ├── web/             # Web 服务
│   │   ├── server.py    # FastAPI 服务器
│   │   └── static/      # 前端页面
│   ├── github/          # GitHub API 客户端
│   ├── notifiers/       # 通知服务（飞书）
│   └── config.py        # 配置管理
├── tools/
│   └── index_code.py    # 代码索引工具
├── docker-compose.yml   # PostgreSQL 容器配置
├── setup.sh             # 一键安装脚本
├── migrate_to_postgres.py  # 数据迁移脚本
├── MIGRATION_GUIDE.md   # 迁移指南
└── VECTOR_MEMORY_GUIDE.md  # 向量记忆使用指南
```

---

## 🔧 技术栈

- **后端**：Python 3.9+, FastAPI, LangChain, LangGraph
- **数据库**：PostgreSQL 16 + pgvector
- **向量搜索**：HNSW 索引
- **AI**：OpenAI API (或兼容接口)
- **前端**：原生 HTML/CSS/JavaScript
- **容器化**：Docker, Docker Compose

---

## 📚 文档

- [迁移指南](MIGRATION_GUIDE.md) - 从 SQLite 迁移到 PostgreSQL
- [向量记忆指南](VECTOR_MEMORY_GUIDE.md) - 向量检索详细说明
- [代码库分析](CODEBASE_ANALYSIS.md) - 项目架构分析

---

## 🐛 故障排查

### 问题 1：向量搜索不工作
**检查**：
```bash
# 查看日志
docker-compose logs app | grep "Memory store initialized"

# 验证索引
docker exec -it issue-tracker-postgres psql -U issue_tracker -c \
  "SELECT COUNT(*) FROM code_embeddings;"
```

### 问题 2：数据库连接失败
**检查**：
```bash
# 确认 PostgreSQL 运行中
docker-compose ps

# 测试连接
docker exec -it issue-tracker-postgres psql -U issue_tracker -c "SELECT 1;"
```

### 问题 3：嵌入生成慢
**优化**：
- 使用本地嵌入模型（sentence-transformers）
- 减少索引的文件数量
- 调整 chunk 大小

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

---

## 📄 许可证

MIT License

---

## 🙏 致谢

- [LangChain](https://github.com/langchain-ai/langchain)
- [LangGraph](https://github.com/langchain-ai/langgraph)
- [pgvector](https://github.com/pgvector/pgvector)
- [FastAPI](https://fastapi.tiangolo.com/)
