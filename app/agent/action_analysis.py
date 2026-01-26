"""
Action Analysis Agent - Analyzes GitHub Actions Logs using AI

This module provides functionality to analyze workflow run failures by:
1. Parsing job logs
2. Retrieving relevant local code context (RAG) based on log errors
3. Providing root cause analysis and fix suggestions
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TypedDict, Annotated
import json
import logging
import operator
import os
import re

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from langgraph.graph import StateGraph, END

from app.config import Config

logger = logging.getLogger(__name__)


@dataclass
class ActionAnalysisResult:
    """Result of an Action analysis"""
    analysis: Dict[str, Any]
    model_info: Dict[str, Any]


class ActionAnalysisState(TypedDict):
    """State for Action analysis workflow"""
    repo: str
    run_id: int
    job_id: int
    job_name: str
    logs: str
    local_repo_path: Optional[str]
    messages: Annotated[List[BaseMessage], operator.add]
    analysis: Optional[Dict[str, Any]]
    error: Optional[str]
    code_context: Optional[str]


def extract_keywords_from_logs(logs: str) -> List[str]:
    """Helper to extract likely error keywords/filenames from logs"""
    keywords = set()
    
    # Simple heuristics
    # 1. Look for lines with "Error:" or "Exception"
    error_lines = []
    for line in logs.splitlines():
        if "Error" in line or "Exception" in line or "Failed" in line:
            error_lines.append(line)
            
    # From error lines, look for file extensions
    for line in error_lines[-20:]: # Check last 20 errors
        # Look for .go, .py, .java, .js, .ts files
        matches = re.findall(r'[\w\-/]+\.(?:go|py|java|js|ts|cpp|c|h|rs)', line)
        for m in matches:
            # Clean up path
            filename = m.split('/')[-1]
            if len(filename) > 3:
                keywords.add(filename)
                
    return list(keywords)[:5]


def retrieve_action_context_node(state: ActionAnalysisState) -> Dict:
    """
    Retrieve additional context by grepping local repo for error keywords found in logs.
    """
    logger.info("🔍 Retrieving context for Action analysis...")
    
    local_repo_path = state.get("local_repo_path")
    logs = state.get("logs", "")
    
    code_context = ""
    
    if local_repo_path and os.path.exists(local_repo_path):
        try:
            keywords = extract_keywords_from_logs(logs)
            if not keywords:
                # Fallback: try job name parts
                job_name = state.get("job_name", "")
                keywords = [w for w in job_name.split() if len(w) > 4][:2]
                
            if keywords:
                logger.info(f"🔎 Searching code for keywords: {keywords}")
                import subprocess
                hits = []
                for kw in keywords:
                    try:
                        # grep recursively, line number, binary files ignored
                        cmd = ["grep", "-r", "-n", "-I", "--exclude-dir=.git", kw, local_repo_path]
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                        if result.stdout:
                            lines = result.stdout.splitlines()[:5] # Take top 5 hits per kw
                            for line in lines:
                                parts = line.split(":", 2)
                                if len(parts) >= 3:
                                    rel_path = os.path.relpath(parts[0], local_repo_path)
                                    hits.append(f"{rel_path}:{parts[1]}: {parts[2].strip()}")
                    except Exception:
                        continue
                
                if hits:
                    code_context = "### Related Code (Found via error log keywords):\n" + "\n".join(hits[:15])
                    logger.info(f"✅ Found {len(hits)} code references")
        except Exception as e:
            logger.warning(f"Failed to search local repo: {e}")
    
    return {"code_context": code_context}


def action_analysis_node(state: ActionAnalysisState, llm: ChatOpenAI) -> Dict:
    """
    Main Action analysis node.
    """
    logger.info("🧠 Analyzing Log...")
    
    repo = state.get("repo")
    job_name = state.get("job_name")
    logs = state.get("logs", "")
    code_context = state.get("code_context", "")
    
    system_prompt = """You are an expert DevOps engineer and software developer. Analyze the given GitHub Action Job log to determine the cause of failure.

1.  **Analyze the Failure**:
    *   Examine the error stack traces and logs carefully.
    *   Determine if it's a Code Error, Test Failure, Environment Issue, or Configuration Error.
    *   Identify the exact file and line number if possible.

2.  **Output Format (JSON)**:
{
    "summary": "Concise summary of why the job failed",
    "type": "BUILD_FAILURE/TEST_FAILURE/LINT_ERROR/DEPLOY_ERROR/CONFIG_ERROR",
    "severity": "HIGH/MEDIUM/LOW",
    "technical_analysis": "Detailed breakdown of the error structure using the provided logs and code context.",
    "implementation_plan": "Step-by-step fix. Include EXACT CODE CHANGES or commands to run. Example: 'In file `.github/workflows/ci.yml`, change `node-version: 14` to `node-version: 16`'.",
    "files_to_change": ["likely/file/path", ...],
    "reproduction_steps": ["command to run locally"]
}

Important:
- Use Markdown for code snippets.
- Be extremely specific. Quote the error and the fix.
"""

    user_content = f"""# Action Failure Analysis Request

## Repository
{repo}

## Job Name
{job_name}

## Log Snippet (Recent/Key parts)
{logs[-15000:] if len(logs) > 15000 else logs}
"""

    if code_context:
        user_content += f"""
## Local Code Context
{code_context}
"""

    try:
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content)
        ]
        
        response = llm.invoke(messages)
        content = response.content
        
        # Robust JSON extraction
        import re
        
        json_str = content
        if "```" in content:
            matches = re.findall(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', content)
            if matches:
                 json_str = matches[0]
            else:
                 parts = content.split("```")
                 if len(parts) >= 2:
                     json_str = parts[1]
                     
        if not json_str.strip().startswith("{"):
            match = re.search(r'(\{[\s\S]*\})', content)
            if match:
                json_str = match.group(1)
                
        json_str = json_str.strip()
        
        try:
            # strict=False allows control characters like newlines in strings
            analysis = json.loads(json_str, strict=False)
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON Parse Error: {e}")
            
            analysis = {
                "summary": "JSON Parsing Failed. Displaying raw output.",
                "type": "PARSE_ERROR",
                "severity": "HIGH",
                "technical_analysis": f"**Raw Output:**\n\n{content}",
                "implementation_plan": "N/A (See Technical Analysis)",
                "raw_response": True
            }
            
        return {"analysis": analysis}
        
    except Exception as e:
        logger.error(f"❌ Action analysis failed: {e}")
        return {
            "error": str(e),
            "analysis": {"error": str(e), "summary": "Analysis Failed"}
        }


def run_action_analysis(
    cfg: Config,
    repo: str,
    run_id: int,
    job_id: int,
    job_name: str,
    logs: str,
    local_repo_path: Optional[str] = None
) -> ActionAnalysisResult:
    """Run the action analysis workflow"""
    
    logger.info(f"🚀 Starting Action Analysis for {repo} Job {job_id}")
    
    llm = ChatOpenAI(
        base_url=cfg.llm.base_url,
        api_key=cfg.llm.api_key or "dummy",
        model=cfg.llm.model,
        temperature=0.2, # Lower temp for log analysis
    )
    
    workflow = StateGraph(ActionAnalysisState)
    workflow.add_node("retrieve_context", retrieve_action_context_node)
    workflow.add_node("analyze", lambda s: action_analysis_node(s, llm))
    
    workflow.add_edge("retrieve_context", "analyze")
    workflow.add_edge("analyze", END)
    
    workflow.set_entry_point("retrieve_context")
    app = workflow.compile()
    
    initial_state = {
        "repo": repo,
        "run_id": run_id,
        "job_id": job_id,
        "job_name": job_name,
        "logs": logs,
        "local_repo_path": local_repo_path,
        "messages": [],
        "analysis": None,
        "error": None,
        "code_context": None
    }
    
    result = app.invoke(initial_state)
    
    return ActionAnalysisResult(
        analysis=result.get("analysis", {}),
        model_info={"model": cfg.llm.model}
    )
