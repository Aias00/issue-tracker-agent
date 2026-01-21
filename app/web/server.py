from fastapi import FastAPI, HTTPException, Query, Body, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import logging
import os
from typing import Optional, Dict, Any

from app.config import load_config_from_env, Config
from app.storage.sqlite_store import SQLiteStateStore
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
        STORE = SQLiteStateStore(CFG.app.sqlite_path)
        STORE.init() # Ensure DB schema exists
        FEISHU_CLIENT = FeishuClient(CFG.notifications.feishu.message.webhook_url)
        GH_CLIENT = GitHubClient(token=CFG.github.token)
        # For now assume path doesn't change often or restart required for deep changes.
        
        logger.info("Configuration reloaded successfully")
        return {"status": "success", "message": "Configuration updated and reloaded"}
    except Exception as e:
        logger.error(f"Failed to reload config: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Global dependencies
CFG: Config
STORE: SQLiteStateStore
FEISHU_CLIENT: FeishuClient
GH_CLIENT: GitHubClient

@app.on_event("startup")
async def startup_event():
    global CFG, STORE, FEISHU_CLIENT, GH_CLIENT
    try:
        logger.info("Starting up...")
        CFG = load_config_from_env()
        
        STORE = SQLiteStateStore(CFG.app.sqlite_path)
        STORE.init() # Initialize DB
        
        FEISHU_CLIENT = FeishuClient(CFG.notifications.feishu.message.webhook_url)
        GH_CLIENT = GitHubClient(token=CFG.github.token)
        logger.info("Application initialized successfully.")
    except Exception as e:
        logger.error(f"Startup failed: {e}")
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
        total_processed = 0
        budget = Budget(remaining=CFG.agent.limits.max_new_issues_total)
        
        repos = [normalize_repo_name(r.strip()) for r in CFG.github.repos.split(',') if r.strip()]
        
        for repo in repos:
            if budget.remaining <= 0:
                logger.info("Global budget exhausted")
                break
                
            logger.info(f"Processing repo: {repo}")
            processed_count = process_repo_with_budget(
                repo=repo,
                cfg=CFG,
                store=STORE,
                gh=GH_CLIENT,
                feishu=FEISHU_CLIENT,
                budget=budget,
            )
            total_processed += processed_count
            budget.remaining -= processed_count
            
        STORE.log_run(
            repo="ALL",
            status="success",
            detail=f"Processed {total_processed} issues."
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
    
    # Run analysis in thread pool to avoid blocking
    from fastapi.concurrency import run_in_threadpool
    from app.agent.graph import run_issue_agent
    from app.agent.preprocess import clip_text
    
    def _run_analysis():
        # Clip text according to config
        title = clip_text(issue['title'] or '', CFG.agent.text.max_title_chars)
        body = clip_text(issue.get('body') or '', CFG.agent.text.max_body_chars)
        
        # Run LLM analysis
        result = run_issue_agent(
            cfg=CFG,
            repo=issue['repo'],
            title=title,
            body=body,
            issue_url=issue['issue_url']
        )
        
        # Store new analysis
        analysis_id = STORE.insert_issue_analysis(
            issue_row_id=id,
            analysis=result.analysis,
            model_info=result.model_info
        )
        
        return {
            "analysis_id": analysis_id,
            "analysis": result.analysis,
            "model_info": result.model_info
        }
    
    try:
        result = await run_in_threadpool(_run_analysis)
        return {"status": "success", "message": "Re-analysis completed", "data": result}
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