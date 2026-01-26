# Vector Memory 集成完成指南

## ✅ 已完成的集成

### 1. **智能上下文检索**
Agent 现在支持三层检索策略：

#### **策略 1：向量语义搜索**（优先）
- 使用 Issue 标题+描述生成查询向量
- 在 `code_embeddings` 表中搜索最相似的代码片段
- 相似度阈值：0.5（可调整）
- 返回 Top 5 最相关的代码块

#### **策略 2：Grep 文本搜索**（降级）
- 当向量搜索不可用或结果不足时启用
- 基于关键词进行文件名和内容搜索
- 保持向后兼容性

#### **策略 3：历史案例检索**
- 搜索 `analysis_memory` 表中的相似历史分析
- 相似度阈值：0.6
- 提供"类似问题的解决方案"参考

### 2. **情景记忆（Episodic Memory）**
- 每次成功分析后，自动保存到 `analysis_memory` 表
- 包含：Issue 标题、分类、解决方案、向量嵌入
- 支持跨 Issue 的经验复用

### 3. **代码索引工具**
创建了 `tools/index_code.py`，支持：
- 自动扫描本地代码库
- 智能分块（Python 按函数/类，其他按行数）
- 批量生成嵌入并存储
- 增量更新（基于文件哈希去重）

---

## 🚀 使用指南

### 步骤 1：启动数据库和应用

```bash
# 启动 PostgreSQL
docker-compose up -d

# 启动应用
uvicorn app.web.server:app --reload
```

### 步骤 2：为代码库建立索引

```bash
# 基本用法
python tools/index_code.py \
  /path/to/local/hertzbeat \
  --repo-name apache/hertzbeat

# 强制重建索引
python tools/index_code.py \
  /path/to/local/hertzbeat \
  --repo-name apache/hertzbeat \
  --force

# 使用自定义数据库
python tools/index_code.py \
  /path/to/local/repo \
  --repo-name owner/repo \
  --database-url "postgresql://user:pass@host/db"
```

**索引过程**：
- 扫描所有支持的代码文件（.py, .java, .js, .go 等）
- 跳过 `.git`, `node_modules`, `__pycache__` 等目录
- 自动分块（Python 按函数，其他按 500 行）
- 生成向量嵌入并存储到 `code_embeddings` 表

### 步骤 3：配置本地路径映射

在 `.env` 中配置：
```bash
REPO_PATHS={"apache/hertzbeat": "/Users/yourname/workspace/hertzbeat"}
```

或在前端 Configuration 页面配置。

### 步骤 4：测试向量检索

1. 在前端点击 "Issues" 查看已采集的 Issue
2. 点击某个 Issue 的 "View Details"
3. 点击 "🔄 Re-analyze with AI"
4. 查看日志，应该看到：
   ```
   Using vector search for context retrieval
   Found 5 relevant code chunks via vector search
   Found 2 similar past analyses
   ```

---

## 📊 工作流程示意

```
Issue 分析请求
    ↓
retrieve_context_node
    ├─ 尝试向量搜索 (code_embeddings)
    │   └─ 找到相关代码片段
    ├─ 搜索历史案例 (analysis_memory)
    │   └─ 找到类似问题的解决方案
    └─ 降级到 grep（如果向量搜索失败）
    ↓
analyze_node (使用增强的上下文)
    ↓
bug_analysis_node / architect_node
    ↓
保存分析结果到 analysis_memory
```

---

## 🔧 配置选项

### 嵌入模型配置

默认使用 `text-embedding-3-small`，您可以修改：

```python
# 在 app/web/server.py 中
embeddings = OpenAIEmbeddings(
    base_url=CFG.llm.base_url,
    api_key=CFG.llm.api_key,
    model="text-embedding-3-large"  # 更高质量，但更慢
)
```

支持的模型：
- `text-embedding-3-small` (1536 维，快速)
- `text-embedding-3-large` (3072 维，高质量)
- `text-embedding-ada-002` (1536 维，旧版)

### 相似度阈值调整

在 `app/agent/graph.py` 中：

```python
# 代码搜索阈值
if similarity > 0.5:  # 调整此值 (0.0-1.0)

# 历史案例阈值
if similarity > 0.6:  # 调整此值
```

---

## 📈 性能优化建议

### 1. **批量索引**
如果代码库很大，可以分批索引：
```bash
# 只索引特定目录
python tools/index_code.py /path/to/repo/src --repo-name owner/repo
```

### 2. **定期更新索引**
设置 cron 任务定期重建索引：
```bash
# 每天凌晨 2 点更新
0 2 * * * cd /path/to/project && python tools/index_code.py /path/to/repo --repo-name owner/repo --force
```

### 3. **监控向量搜索性能**
```sql
-- 查看索引大小
SELECT 
    repo, 
    COUNT(*) as chunk_count,
    pg_size_pretty(pg_total_relation_size('code_embeddings')) as table_size
FROM code_embeddings
GROUP BY repo;

-- 测试查询速度
EXPLAIN ANALYZE
SELECT file_path, 1 - (embedding <=> '[0.1, 0.2, ...]'::vector) AS similarity
FROM code_embeddings
WHERE repo = 'apache/hertzbeat'
ORDER BY embedding <=> '[0.1, 0.2, ...]'::vector
LIMIT 5;
```

---

## 🐛 故障排查

### 问题 1：向量搜索不工作
**症状**：日志显示 "Using grep search for context retrieval"

**解决**：
1. 检查嵌入函数是否初始化成功
   ```bash
   # 查看应用日志
   grep "Memory store initialized with embedding function" logs
   ```

2. 验证 LLM_BASE_URL 和 LLM_API_KEY 配置正确

3. 确保代码库已建立索引
   ```sql
   SELECT COUNT(*) FROM code_embeddings WHERE repo = 'your/repo';
   ```

### 问题 2：索引速度慢
**原因**：嵌入生成需要调用 LLM API

**优化**：
- 使用本地嵌入模型（如 sentence-transformers）
- 减少 chunk 大小
- 并行处理（修改 index_code.py 使用多线程）

### 问题 3：内存占用高
**原因**：向量维度较高（1536 或 3072）

**优化**：
- 使用 PCA 降维
- 定期清理旧的嵌入
- 使用 IVFFlat 索引代替 HNSW（牺牲精度换速度）

---

## 🎯 下一步增强

1. **增量索引**：只索引变更的文件（基于 git diff）
2. **多模态检索**：结合代码结构、注释、文档
3. **自适应阈值**：根据检索结果质量动态调整相似度阈值
4. **可视化**：在前端展示检索到的代码片段和相似度分数

---

需要我帮您测试向量检索功能吗？或者您想先建立代码索引？
