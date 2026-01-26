"""
Issue Analysis Agent - Analyzes GitHub Issues using AI

This module provides functionality to analyze issues by:
1. Fetching issue details and comments
2. Retrieving relevant local code context (RAG)
3. Providing analysis, reproduction steps, and implementation plans
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TypedDict, Annotated
import json
import logging
import operator
import os

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from langgraph.graph import StateGraph, END

from app.config import Config

logger = logging.getLogger(__name__)


@dataclass
class IssueAnalysisResult:
    """Result of an Issue analysis"""
    analysis: Dict[str, Any]
    model_info: Dict[str, Any]


class IssueAnalysisState(TypedDict):
    """State for Issue analysis workflow"""
    repo: str
    issue_number: int
    issue_url: str
    title: str
    body: str
    comments: List[Dict[str, Any]]
    local_repo_path: Optional[str]
    messages: Annotated[List[BaseMessage], operator.add]
    analysis: Optional[Dict[str, Any]]
    error: Optional[str]
    code_context: Optional[str]  # Related code from local repo based on keywords


def retrieve_issue_context_node(state: IssueAnalysisState) -> Dict:
    """
    Retrieve additional context for issue analysis.
    This searches the local repository for code relevant to the issue.
    """
    logger.info("🔍 Retrieving context for Issue analysis...")
    
    local_repo_path = state.get("local_repo_path")
    title = state.get("title", "")
    body = state.get("body", "")
    
    code_context = ""
    
    # Simple keyword search if we have a local repo
    # In a real system, this would use vector search / embeddings
    if local_repo_path and os.path.exists(local_repo_path):
        try:
            # Extract potential keywords from title (very naive)
            keywords = [w for w in title.split() if len(w) > 4][:3]
            
            if keywords:
                # Use grep to find mentions
                import subprocess
                hits = []
                for kw in keywords:
                    try:
                        cmd = ["grep", "-r", "-n", "-I", "--include=*.py", "--include=*.js", "--include=*.ts", "--include=*.go", kw, local_repo_path]
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                        if result.stdout:
                            lines = result.stdout.splitlines()[:5] # Take top 5 hits
                            for line in lines:
                                # Format: path/to/file:line:content
                                parts = line.split(":", 2)
                                if len(parts) >= 3:
                                    rel_path = os.path.relpath(parts[0], local_repo_path)
                                    hits.append(f"{rel_path}:{parts[1]}: {parts[2].strip()}")
                    except Exception:
                        continue
                
                if hits:
                    code_context = "### Potential Code References found via grep:\n" + "\n".join(hits[:10])
                    logger.info(f"✅ Found {len(hits)} code references via grep")
        except Exception as e:
            logger.warning(f"Failed to search local repo: {e}")
    
    return {"code_context": code_context}


def issue_analysis_node(state: IssueAnalysisState, llm: ChatOpenAI) -> Dict:
    """
    Main Issue analysis node.
    """
    logger.info("🧠 Analyzing Issue...")
    
    title = state.get("title", "")
    body = state.get("body", "") or ""
    comments = state.get("comments", [])
    code_context = state.get("code_context", "")
    
    # Format comments
    comments_text = ""
    if comments:
        comments_list = []
        for c in comments[-10:]: # Last 10 comments
            comments_list.append(f"- @{c.get('author', 'unknown')}: {c.get('body', '')[:200]}...")
        comments_text = "\n".join(comments_list)
    
    system_prompt = """You are an expert technical architect and principal engineer. Your task is to analyze a GitHub Issue and provide a concrete, actionable implementation plan.

1.  **Classify the Issue**:
    *   **BUG**: If it reports an error, crash, or unexpected behavior. Focus on Root Cause Analysis and Fix.
    *   **FEATURE/TASK**: If it asks for new functionality or refactoring. Focus on Design and Implementation Steps.
    *   **QUESTION/DOCS**: If it's a usage question or documentation need.

2.  **Analysis Requirements**:
    *   **For BUGS**:
        *   Analyze the provided code context and logs (if any).
        *   Hypothesize the root cause (specific logic errors, missing handling, etc.).
        *   Explain *why* it is failing.
    *   **For FEATURES/TASKS**:
        *   Outline the design choices (e.g., "Create class X", "Modify function Y").
        *   Assess impact on existing modules.

3.  **Output Format (JSON)**:
{
    "summary": "High-level summary of the requirement or problem",
    "type": "BUG/FEATURE/QUESTION/OTHER",
    "severity": "HIGH/MEDIUM/LOW",
    "technical_analysis": "Deep dive into the problem. For Bugs, explain the root cause. For Features, explain the design rationale.",
    "implementation_plan": "Detailed, step-by-step instructions. **Crucial:** Include CODE SNIPPETS or specific logic changes. Avoid generic statements.",
    "files_to_change": ["path/to/file.java", ...],
    "check_list": ["Verify X case", "Add unit test for Y"]
}

Important:
- Use Markdown in string fields (e.g., use code blocks for snippets).
- Be specific. Avoid phrases like "Update the code". Instead say "In class `HttpClient.java`, replace `CloseableHttpClient` with `OkHttpClient`."
"""

    user_content = f"""# Issue Analysis Request

## Issue Title
{title}

## Issue Description
{body if body else "(No description provided)"}

## Recent Discussion
{comments_text if comments_text else "(No discussion)"}
"""

    if code_context:
        user_content += f"""
## Context Information
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
        
        # 1. Try regex for markdown code blocks first
        json_str = content
        if "```" in content:
            matches = re.findall(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', content)
            if matches:
                json_str = matches[0]
            else:
                # Maybe just backticks without json tag or mismatched
                parts = content.split("```")
                if len(parts) >= 2:
                    json_str = parts[1]
        
        # 2. If cleanup didn't produce perfect JSON, try finding outermost {}
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
            logger.error(f"❌ Raw Content: {json_str[:500]}...")
            
            # Fallback: Create a structured object with raw content so nothing is swallowed
            analysis = {
                "summary": "JSON Parsing Failed. Displaying raw output below.",
                "type": "PARSE_ERROR",
                "severity": "HIGH",
                "technical_analysis": f"**Raw Output (Analysis):**\n\n{content}",
                "implementation_plan": "**Raw Output (Plan):**\n\n(See Technical Analysis above)",
                "raw_response": True,
                "error": str(e)
            }
            
        return {"analysis": analysis}
        
    except Exception as e:
        logger.error(f"❌ Issue analysis failed: {e}")
        return {
            "error": str(e),
            "analysis": {"error": str(e), "summary": "Analysis Failed"}
        }


def run_issue_analysis(
    cfg: Config,
    repo: str,
    issue_number: int,
    issue_url: str,
    title: str,
    body: str,
    comments: List[Dict[str, Any]],
    local_repo_path: Optional[str] = None
) -> IssueAnalysisResult:
    """Run the issue analysis workflow"""
    
    logger.info(f"🚀 Starting Issue Analysis for {repo}#{issue_number}")
    
    llm = ChatOpenAI(
        base_url=cfg.llm.base_url,
        api_key=cfg.llm.api_key or "dummy",
        model=cfg.llm.model,
        temperature=0.3,
    )
    
    workflow = StateGraph(IssueAnalysisState)
    workflow.add_node("retrieve_context", retrieve_issue_context_node)
    workflow.add_node("analyze", lambda s: issue_analysis_node(s, llm))
    
    workflow.add_edge("retrieve_context", "analyze")
    workflow.add_edge("analyze", END)
    
    workflow.set_entry_point("retrieve_context")
    app = workflow.compile()
    
    initial_state = {
        "repo": repo,
        "issue_number": issue_number,
        "issue_url": issue_url,
        "title": title,
        "body": body,
        "comments": comments,
        "local_repo_path": local_repo_path,
        "messages": [],
        "analysis": None,
        "error": None,
        "code_context": None
    }
    
    result = app.invoke(initial_state)
    
    return IssueAnalysisResult(
        analysis=result.get("analysis", {}),
        model_info={"model": cfg.llm.model}
    )
