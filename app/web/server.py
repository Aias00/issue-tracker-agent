from fastapi import FastAPI, HTTPException, Query, Body, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import logging
import os
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel

class AnalysisRequest(BaseModel):
    url: str
    log: Optional[str] = None

from app.config import load_config_from_env, Config
from app.storage.pg_store import PostgresStateStore
from app.storage.memory_store import MemoryStore
from app.notifiers.feishu.client import FeishuClient
from app.github.client import GitHubClient
from app.jobs.sync import process_repo_with_budget, Budget
from app.config_manager import read_env_file, write_env_file, update_env_vars

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Issue Tracker Agent")

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def read_root():
    return FileResponse(os.path.join(static_dir, "index.html"))

@app.get("/api/config")
async def get_config():
    # Return raw env vars for editing
    return read_env_file()

@app.post("/api/config")
async def update_config(config: Dict[str, str] = Body(...)):
    global CFG, STORE, FEISHU_CLIENT, GH_CLIENT
    
    # Normalize REPOS if present
    if 'REPOS' in config and config['REPOS']:
        from app.utils import normalize_repo_name
        repos_list = [normalize_repo_name(r.strip()) for r in config['REPOS'].split(',') if r.strip()]
        config['REPOS'] = ','.join(repos_list)
    
    # 1. Update .env file
    write_env_file(config)
    
    # 2. Update process env vars
    update_env_vars(config)
    
    # 3. Reload application config
    try:
        CFG = load_config_from_env()
        # Re-init clients that depend on config
        STORE = PostgresStateStore(CFG.app.database_url)
        STORE.init() # Ensure DB schema exists
        
        # Initialize memory store with embedding function
        try:
            from langchain_openai import OpenAIEmbeddings
            embeddings = OpenAIEmbeddings(
                base_url=CFG.llm.base_url,
                api_key=CFG.llm.api_key or "dummy",
                model="text-embedding-3-small"  # or your preferred embedding model
            )
            MEMORY_STORE = MemoryStore(CFG.app.database_url, embedding_function=embeddings.embed_query)
            logger.info("Memory store initialized with embedding function")
        except Exception as e:
            logger.warning(f"Failed to initialize embedding function: {e}, memory store will work without vector search")
            MEMORY_STORE = MemoryStore(CFG.app.database_url)
        
        FEISHU_CLIENT = FeishuClient(CFG.notifications.feishu.message.webhook_url)
        GH_CLIENT = GitHubClient(token=CFG.github.token)
        
        logger.info("Configuration reloaded successfully")
        return {"status": "success", "message": "Configuration updated and reloaded"}
    except Exception as e:
        logger.error(f"Failed to reload config: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Global dependencies
CFG: Config
STORE: PostgresStateStore
MEMORY_STORE: MemoryStore
FEISHU_CLIENT: FeishuClient
GH_CLIENT: GitHubClient

@app.on_event("startup")
async def startup_event():
    global CFG, STORE, MEMORY_STORE, FEISHU_CLIENT, GH_CLIENT
    try:
        logger.info("Starting up...")
        CFG = load_config_from_env()
        
        STORE = PostgresStateStore(CFG.app.database_url)
        STORE.init() # Initialize DB
        
        # Initialize memory store with embedding function
        try:
            from langchain_openai import OpenAIEmbeddings
            logger.info("🧠 Initializing embedding function...")
            embeddings = OpenAIEmbeddings(
                base_url=CFG.llm.base_url,
                api_key=CFG.llm.api_key or "dummy",
                model="text-embedding-3-small"
            )
            MEMORY_STORE = MemoryStore(CFG.app.database_url, embedding_function=embeddings.embed_query)
            logger.info("✅ Memory store initialized with embedding function")
        except Exception as e:
            logger.warning(f"⚠️  Failed to initialize embedding function: {e}, memory store will work without vector search")
            MEMORY_STORE = MemoryStore(CFG.app.database_url)
        
        FEISHU_CLIENT = FeishuClient(CFG.notifications.feishu.message.webhook_url)
        logger.info(f"📢 Feishu client initialized (webhook: {'configured' if CFG.notifications.feishu.message.webhook_url else 'not configured'})")
        
        GH_CLIENT = GitHubClient(token=CFG.github.token)
        logger.info(f"🐙 GitHub client initialized (token: {'provided' if CFG.github.token else 'not provided (anonymous mode)'})")
        logger.info(f"📦 Monitoring repos: {CFG.github.repos}")
        
        if CFG.github.repo_paths:
            logger.info(f"📂 Local repo paths configured: {list(CFG.github.repo_paths.keys())}")
        else:
            logger.info(f"📂 No local repo paths configured (vector search will use grep fallback)")
        
        logger.info("✅ Application initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        # We don't raise here to allow the server to start even if config is bad,
        # so user can fix it via UI.

def ensure_initialized():
    if not CFG or not STORE:
        raise HTTPException(status_code=500, detail="Server not initialized properly")

@app.post("/run")
async def trigger_run(background_tasks: BackgroundTasks):
    ensure_initialized()
    from app.utils import normalize_repo_name
    
    def _run_sync():
        logger.info("🎬 Starting sync run...")
        total_processed = 0
        budget = Budget(remaining=CFG.agent.limits.max_new_issues_total)
        
        repos = [normalize_repo_name(r.strip()) for r in CFG.github.repos.split(',') if r.strip()]
        logger.info(f"📋 Will process {len(repos)} repo(s): {repos}")
        
        for idx, repo in enumerate(repos, 1):
            if budget.remaining <= 0:
                logger.warning(f"⏸️  Global budget exhausted, stopping at repo {idx}/{len(repos)}")
                break
            
            logger.info(f"🔄 [{idx}/{len(repos)}] Processing repo: {repo}")
            logger.info(f"💰 Current budget: {budget.remaining} remaining")
            
            processed_count = process_repo_with_budget(
                repo=repo,
                cfg=CFG,
                store=STORE,
                gh=GH_CLIENT,
                feishu=FEISHU_CLIENT,
                budget=budget,
            )
            
            logger.info(f"✅ [{idx}/{len(repos)}] Repo {repo} completed: {processed_count} new issues")
            total_processed += processed_count
            # ❌ 不要重复扣减！budget.remaining 已经在 process_repo_with_budget 中修改了
            # budget.remaining -= processed_count  # REMOVED: This was causing double deduction
        
        logger.info(f"🎉 Sync run completed!")
        logger.info(f"   📊 Total repos processed: {len(repos)}")
        logger.info(f"   📝 Total new issues: {total_processed}")
        logger.info(f"   💰 Budget remaining: {budget.remaining}")
        
        STORE.log_run(
            repo="ALL",
            status="success",
            detail=f"Processed {total_processed} issues across {len(repos)} repos."
        )
        logger.info(f"Run completed. Total processed: {total_processed}, budget remaining: {budget.remaining}")
    
    background_tasks.add_task(_run_sync)
    return {"status": "started", "message": "Run triggered in background"}

@app.get('/runs')
async def get_runs(repo: Optional[str] = None, status: Optional[str] = None):
    ensure_initialized()
    return {"runs": STORE.list_runs(repo=repo, status=status)}

@app.get('/issues')
async def get_issues(
    repo: Optional[str] = None, 
    state: Optional[str] = None,
    limit: int = 100, 
    offset: int = 0
):
    ensure_initialized()
    return {"issues": STORE.list_issues(repo=repo, state=state, limit=limit, offset=offset)}

@app.get('/api/graph')
async def get_graph_config():
    """Get current graph configuration"""
    from app.agent.graph import get_current_graph_config
    return get_current_graph_config()

@app.post('/api/graph')
async def update_graph_config(config: Dict[str, Any] = Body(...)):
    """Update graph configuration"""
    from app.agent.graph import update_current_graph_config
    try:
        update_current_graph_config(config)
        return {"status": "success", "message": "Graph configuration updated"}
    except Exception as e:
        logger.error(f"Failed to update graph config: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get('/issues/{id}')
async def get_issue_by_id(id: int):
    ensure_initialized()
    issue = STORE.get_issue(id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return {"issue": issue}

@app.get('/issues/{id}/analyses')
async def get_issue_analyses(id: int):
    ensure_initialized()
    # verify issue exists
    if not STORE.get_issue(id):
        raise HTTPException(status_code=404, detail="Issue not found")
    
    
    return {"analyses": STORE.list_issue_analyses(issue_row_id=id)}

@app.post('/issues/{id}/reanalyze')
async def reanalyze_issue(id: int):
    """Re-run LLM analysis on an existing issue"""
    ensure_initialized()
    
    # Get the issue
    issue = STORE.get_issue(id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    
    # Import dependencies
    from app.agent.graph import run_issue_agent
    from app.agent.preprocess import clip_text
    
    try:
        # Clip text according to config
        title = clip_text(issue['title'] or '', CFG.agent.text.max_title_chars)
        body = clip_text(issue.get('body') or '', CFG.agent.text.max_body_chars)
        
        # Check for local repo path
        repo_name = issue['repo']
        local_path = None
        if CFG.github.repo_paths and repo_name in CFG.github.repo_paths:
            local_path = CFG.github.repo_paths[repo_name]
            logger.info(f"Using local repo path for {repo_name}: {local_path}")
        
        # Run LLM analysis
        result = run_issue_agent(
            cfg=CFG,
            repo=repo_name,
            title=title,
            body=body,
            issue_url=issue['issue_url'],
            local_repo_path=local_path
        )
        
        # Store new analysis
        analysis_id = STORE.insert_issue_analysis(
            issue_row_id=id,
            analysis=result.analysis,
            model_info=result.model_info
        )
        
        return {
            "status": "success",
            "message": "Re-analysis completed",
            "data": {
                "analysis_id": analysis_id,
                "analysis": result.analysis,
                "model_info": result.model_info
            }
        }
    except Exception as e:
        logger.error(f"Re-analysis failed for issue {id}: {e}")
        raise HTTPException(status_code=500, detail=f"Re-analysis failed: {str(e)}")

@app.get('/analyses/{id}')
async def get_analysis_by_id(id: int):
    ensure_initialized()
    analysis = STORE.get_analysis(id)
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return {"analysis": analysis}

@app.get('/notifications')
async def get_notifications(
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    ensure_initialized()
    return {"notifications": STORE.list_notifications(status=status, limit=limit, offset=offset)}

@app.get('/notifications/{id}')
async def get_notification_by_id(id: int):
    ensure_initialized()
    note = STORE.get_notification(id)
    if not note:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"notification": note}

# ============================================
# Repos Management API
# ============================================

@app.get('/repos')
async def list_repos(active_only: bool = True):
    """List all managed repositories"""
    ensure_initialized()
    return {"repos": STORE.list_repos(active_only=active_only)}

@app.get('/repos/{id}')
async def get_repo(id: int):
    """Get a specific repo by ID"""
    ensure_initialized()
    repo = STORE.get_repo(repo_id=id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    return {"repo": repo}

@app.post('/repos')
async def create_or_update_repo(
    full_name: str = Body(...),
    local_path: str = Body(None),
    is_active: bool = Body(True),
    auto_sync_issues: bool = Body(True),
    auto_sync_prs: bool = Body(False),
):
    """Create or update a repository"""
    ensure_initialized()
    
    # Normalize repo name
    from app.utils import normalize_repo_name
    full_name = normalize_repo_name(full_name)
    
    repo_id = STORE.upsert_repo(
        full_name=full_name,
        local_path=local_path,
        is_active=is_active,
        auto_sync_issues=auto_sync_issues,
        auto_sync_prs=auto_sync_prs,
    )
    
    logger.info(f"✅ Repo saved: {full_name} (id={repo_id})")
    return {"status": "success", "repo_id": repo_id}

@app.delete('/repos/{id}')
async def delete_repo(id: int):
    """Delete a repository"""
    ensure_initialized()
    deleted = STORE.delete_repo(id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Repo not found")
    return {"status": "deleted"}

@app.get('/repos/{full_name:path}/github-prs')
async def get_repo_github_prs(
    full_name: str,
    state: str = "open",
    limit: int = 30
):
    """
    Fetch PRs directly from GitHub for a specific repo.
    This is useful for selecting PRs to review.
    """
    ensure_initialized()
    
    try:
        prs = GH_CLIENT.list_recent_prs(full_name, limit=limit, state=state)
        return {
            "repo": full_name,
            "prs": [
                {
                    "number": pr.number,
                    "title": pr.title,
                    "author": pr.user.login,
                    "state": pr.state,
                    "url": pr.html_url,
                    "head_ref": pr.head_ref,
                    "base_ref": pr.base_ref,
                    "created_at": pr.created_at.isoformat() if pr.created_at else None,
                    "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
                    "files_changed": pr.files_changed,
                    "additions": pr.additions,
                    "deletions": pr.deletions,
                }
                for pr in prs
            ]
        }
    except Exception as e:
        logger.error(f"Failed to fetch PRs from GitHub: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch PRs: {str(e)}")

# ============================================
# Pull Requests API
# ============================================

@app.get('/prs')
async def list_prs(
    repo: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """List pull requests"""
    ensure_initialized()
    return {"prs": STORE.list_prs(repo=repo, state=state, limit=limit, offset=offset)}

@app.get('/prs/{id}')
async def get_pr(id: int):
    """Get a specific PR by row ID"""
    ensure_initialized()
    pr = STORE.get_pr(pr_row_id=id)
    if not pr:
        raise HTTPException(status_code=404, detail="PR not found")
    return {"pr": pr}

@app.get('/prs/{id}/reviews')
async def get_pr_reviews(id: int, limit: int = 10, offset: int = 0):
    """Get reviews for a specific PR"""
    ensure_initialized()
    reviews = STORE.list_pr_reviews(pr_row_id=id, limit=limit, offset=offset)
    return {"reviews": reviews}

@app.post('/analyze-item')
async def analyze_item_by_url(
    req: AnalysisRequest
):
    """
    Unified analysis for PRs (Review), Issues (Analysis), and Actions (Log Check).
    """
    ensure_initialized()
    
    from app.agent.pr_review import run_pr_review
    from app.agent.issue_analysis import run_issue_analysis
    from app.agent.action_analysis import run_action_analysis
    
    url = req.url
    log = req.log
    
    try:
        # Parse URL
        repo, number, type_hint = GH_CLIENT.parse_github_url(url)
        logger.info(f"🔍 Item Request: {repo}#{number} (Type: {type_hint})")

        # Handle Issue Analysis
        if type_hint == "issue":
             return await _handle_issue_analysis(repo, number)
        elif type_hint == "pr":
             return await _handle_pr_review(repo, number)
        elif type_hint == "action_job" or type_hint == "action_run":
             return await _handle_action_analysis(repo, number, type_hint, log)
        
        # Unknown type: try detecting
        if await _check_is_issue_safe(repo, number):
             return await _handle_issue_analysis(repo, number)
        
        return await _handle_pr_review(repo, number)
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ Review/Analysis Failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))



async def _check_is_issue_safe(repo: str, number: int) -> bool:
    try:
        # Simple heuristic: If it fails to get PR diff, it's likely an issue
        try:
             GH_CLIENT.get_pr_diff(repo, number)
             return False # It IS a PR (has diff)
        except:
             return True # Likely an issue
    except:
        return False

async def _ensure_local_repo(repo_full_name: str) -> Optional[str]:
    """
    Ensure the repository exists locally. 
    1. Check config maps.
    2. Check default repos dir.
    3. Clone if missing.
    """
    import os
    import asyncio
    
    # 1. Configured path - Check explicitly configured paths first
    if CFG.github.repo_paths and repo_full_name in CFG.github.repo_paths:
        path = CFG.github.repo_paths[repo_full_name]
        if os.path.exists(path):
            return path
        logger.warning(f"⚠️ Configured path for {repo_full_name} not found: {path}")

    # 2. Check Default Dir
    default_dir = CFG.github.default_repos_dir
    repo_name_only = repo_full_name.split('/')[-1]
    
    # Use simple repo name as folder name (e.g. /data/shenyu)
    target_path = os.path.join(default_dir, repo_name_only)
    
    # Check if it exists
    if os.path.exists(target_path):
        # Quick check if it's actually a git repo? Optional but good.
        if os.path.isdir(os.path.join(target_path, ".git")):
            logger.info(f"✅ Found local repo in default dir: {target_path}")
            return target_path
    
    # 3. Clone it
    logger.info(f"⏳ Repo {repo_full_name} not found locally. Cloning to {target_path}...")
    
    try:
        if not os.path.exists(os.path.dirname(target_path)):
             os.makedirs(os.path.dirname(target_path), exist_ok=True)
             
        # Construct Clone URL (with token if available for private repos)
        clone_url = f"https://github.com/{repo_full_name}.git"
        if CFG.github.token and CFG.github.token != "your_github_token_here":
            clone_url = f"https://oauth2:{CFG.github.token}@github.com/{repo_full_name}.git"
            
        process = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", clone_url, target_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            logger.info(f"✅ Successfully cloned {repo_full_name} to {target_path}")
            
            # Register repo in DB
            try:
                STORE.upsert_repo(
                    full_name=repo_full_name,
                    local_path=target_path,
                    is_active=True, # Activate it since we just cloned it for use
                    auto_sync_issues=True,
                    auto_sync_prs=False
                )
                logger.info(f"✅ Registered repo {repo_full_name} in database")
            except Exception as e:
                logger.warning(f"⚠️ Failed to register repo in DB: {e}")
                
            return target_path
        else:
            logger.error(f"❌ Git clone failed: {stderr.decode()}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Failed to auto-clone repo: {e}")
        return None

async def _handle_issue_analysis(repo: str, issue_number: int):
    from app.agent.issue_analysis import run_issue_analysis
    
    logger.info(f"🧠 Handling Issue Analysis for {repo}#{issue_number}")
    
    # Fetch Issue
    issue = GH_CLIENT.get_issue(repo, issue_number)
    
    # Fetch Comments
    comments = GH_CLIENT.get_pr_comments(repo, issue_number)
    
    # Save Issue to DB
    issue_row_id = STORE.upsert_issue(
        repo=repo,
        issue_number=issue_number,
        issue_id=issue.id,
        issue_url=issue.html_url,
        title=issue.title,
        author_login=issue.user.login,
        state=issue.state,
        created_at=issue.created_at.isoformat() if issue.created_at else None,
    )
    
    # Get local repo path (Clone if missing)
    local_path = await _ensure_local_repo(repo)
    
    # Run Analysis
    result = run_issue_analysis(
        cfg=CFG,
        repo=repo,
        issue_number=issue_number,
        issue_url=issue.html_url,
        title=issue.title,
        body=issue.body or "",
        comments=comments,
        local_repo_path=local_path
    )
    
    # Save Analysis to DB
    analysis_id = STORE.insert_issue_analysis(
        issue_row_id=issue_row_id,
        analysis=result.analysis,
        model_info=result.model_info
    )
    
    return {
        "status": "success",
        "message": "Issue analysis completed",
        "type": "issue_analysis", 
        "data": {
            "issue_row_id": issue_row_id,
            "analysis_id": analysis_id,
            "issue": {
                "repo": repo,
                "number": issue_number,
                "title": issue.title,
                "url": issue.html_url
            },
            "analysis": result.analysis
        }
    }

async def _handle_pr_review(repo: str, pr_number: int):
    from app.agent.pr_review import run_pr_review
    
    # Fetch PR details
    pr = GH_CLIENT.get_pr(repo, pr_number)
    
    # Fetch diff
    diff = GH_CLIENT.get_pr_diff(repo, pr_number)
    
    # Fetch files
    files = GH_CLIENT.get_pr_files(repo, pr_number)
    
    # Save PR to database
    pr_row_id = STORE.upsert_pr(
        repo=repo,
        pr_number=pr_number,
        pr_id=pr.id,
        pr_url=pr.html_url,
        title=pr.title,
        body=pr.body,
        author_login=pr.user.login,
        state="merged" if pr.merged else pr.state,
        head_ref=pr.head_ref,
        base_ref=pr.base_ref,
        head_sha=pr.head_sha,
        labels=pr.labels,
        diff_url=pr.diff_url,
        files_changed=pr.files_changed,
        additions=pr.additions,
        deletions=pr.deletions,
        created_at=pr.created_at.isoformat() if pr.created_at else None,
        updated_at=pr.updated_at.isoformat() if pr.updated_at else None,
        merged_at=pr.merged_at.isoformat() if pr.merged_at else None,
    )
    
    # Get local repo path (Clone if missing)
    local_path = await _ensure_local_repo(repo)
    
    # Fetch existing discussion context (comments, reviews)
    discussion_context = None
    try:
        discussion_context = GH_CLIENT.get_all_pr_discussion(repo, pr_number)
        total_items = discussion_context.get("total_comments", 0) + discussion_context.get("total_reviews", 0)
        if total_items > 0:
            logger.info(f"📚 Found {total_items} existing discussion items for PR #{pr_number}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to fetch PR discussion: {e}")
    
    # Run PR review
    result = run_pr_review(
        cfg=CFG,
        repo=repo,
        pr_number=pr_number,
        pr_url=pr.html_url,
        title=pr.title,
        body=pr.body or "",
        diff=diff,
        files=files,
        local_repo_path=local_path,
        discussion_context=discussion_context,
    )
    
    # Save review to database
    review_id = STORE.insert_pr_review(
        pr_row_id=pr_row_id,
        review=result.review,
        model_info=result.model_info,
        code_context=diff[:5000] if diff else None,  # Store truncated diff
        files_reviewed=result.files_reviewed,
        review_type="full",
    )
    
    logger.info(f"✅ PR review saved (review_id={review_id})")
    
    return {
        "status": "success",
        "message": "PR review completed",
        "type": "pr_review",
        "data": {
            "pr_row_id": pr_row_id,
            "review_id": review_id,
            "pr": {
                "repo": repo,
                "number": pr_number,
                "title": pr.title,
                "url": pr.html_url
            },
            "review": result.review
        }
    }

@app.get('/reviews/{id}')
async def get_review_by_id(id: int):
    """Get a specific PR review by ID"""
    ensure_initialized()
    review = STORE.get_pr_review(id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    return {"review": review}

# ============================================
# Combined Items API (Issues + PRs)
# ============================================

@app.get('/items')
async def list_items(
    type: Optional[str] = None,  # 'issue', 'pr', or None for all
    repo: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """
    List combined issues and PRs.
    Useful for unified display in UI.
    """
    ensure_initialized()
    
    items = []
    
    if type != 'pr':
        # Get issues
        issues = STORE.list_issues(repo=repo, state=state, limit=limit, offset=offset)
        for issue in issues:
            items.append({
                "type": "issue",
                "id": issue["id"],
                "repo": issue["repo"],
                "number": issue["issue_number"],
                "title": issue["title"],
                "author": issue["author_login"],
                "state": issue["state"],
                "url": issue["issue_url"],
                "created_at": issue.get("created_at"),
                "first_seen_at": issue.get("first_seen_at"),
            })
    
    if type != 'issue':
        # Get PRs
        prs = STORE.list_prs(repo=repo, state=state, limit=limit, offset=offset)
        for pr in prs:
            items.append({
                "type": "pr",
                "id": pr["id"],
                "repo": pr["repo"],
                "number": pr["pr_number"],
                "title": pr["title"],
                "author": pr["author_login"],
                "state": pr["state"],
                "url": pr["pr_url"],
                "created_at": pr.get("created_at"),
                "first_seen_at": pr.get("first_seen_at"),
                "files_changed": pr.get("files_changed"),
                "additions": pr.get("additions"),
                "deletions": pr.get("deletions"),
            })
    
    # Sort by created_at descending
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    
    return {"items": items[:limit]}
async def _handle_action_analysis(repo: str, number: int, type_hint: str, log_content: Optional[str] = None):
    from app.agent.action_analysis import run_action_analysis
    
    job_id = None
    run_id = None # Optional for fetching logs if we have job_id
    
    if type_hint == "action_job":
        job_id = number
    elif type_hint == "action_run":
        # For now, require job link
        raise HTTPException(status_code=400, detail="Please provide a specific Job URL (ending in /job/...) for Log Analysis")

    logger.info(f"🧠 Handling Action Analysis for {repo} Job #{job_id}")

    # Fetch logs or use provided content
    logs = ""
    if log_content and len(log_content.strip()) > 0:
        logger.info("📄 Using user-provided log content")
        logs = log_content
    else:
        try:
            logs = GH_CLIENT.download_job_logs(repo, job_id)
            if not logs:
                 raise ValueError("Logs are empty or expired.")
        except Exception as e:
            logger.error(f"❌ Failed to fetch logs: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to fetch logs: {e}. Please try pasting the log content manually.")

    # Ensure repo (Clone if missing)
    local_path = await _ensure_local_repo(repo)
    
    # Run analysis
    result = run_action_analysis(
        cfg=CFG,
        repo=repo,
        run_id=0,
        job_id=job_id,
        job_name=f"Job #{job_id}",
        logs=logs,
        local_repo_path=local_path
    )
    
    # Save "Action Job" as an Issue so we can store analysis
    # We use job_id as issue_number (ensure DB schema supports BIGINT)
    try:
        issue_row_id = STORE.upsert_issue(
            repo=repo,
            issue_number=job_id,
            issue_id=job_id, 
            issue_url=f"https://github.com/{repo}/actions/runs/0/job/{job_id}",
            title=f"Action Failure: Job #{job_id}",
            author_login="github-actions[bot]",
            state="failure",
            created_at=datetime.utcnow().isoformat()
        )
        
        STORE.insert_issue_analysis(
            issue_row_id=issue_row_id,
            analysis=result.analysis,
            model_info=result.model_info
        )
        logger.info(f"✅ Saved Action Analysis for Job #{job_id}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to save Action Analysis to DB: {e}")

    # Return formatted result compatible with frontend Issue Analysis view
    return {
        "status": "success",
        "type": "issue_analysis",  # Re-use frontend component
        "data": {
            "issue": { # Fake issue structure for frontend
                "repo": repo,
                "number": job_id,
                "title": f"Action Failure: Job #{job_id}",
                "url": f"https://github.com/{repo}/actions/jobs/{job_id}",
                "state": "failure"
            },
            "analysis": result.analysis
        }
    }
