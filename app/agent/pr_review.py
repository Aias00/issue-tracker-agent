"""
PR Review Agent - Analyzes Pull Requests using AI

This module provides functionality to review PRs by:
1. Fetching PR details and diff from GitHub
2. Analyzing code changes using LLM
3. Providing review summary, issues, and suggestions
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TypedDict, Annotated
import json
import logging
import operator

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from langgraph.graph import StateGraph, END

from app.config import Config
from app.github.client import GitHubClient, GitHubPR

logger = logging.getLogger(__name__)


@dataclass
class PRReviewResult:
    """Result of a PR review"""
    review: Dict[str, Any]
    model_info: Dict[str, Any]
    files_reviewed: List[str]


class PRReviewState(TypedDict):
    """State for PR review workflow"""
    repo: str
    pr_number: int
    pr_url: str
    title: str
    body: str
    diff: str
    files: List[Dict[str, Any]]
    local_repo_path: Optional[str]
    messages: Annotated[List[BaseMessage], operator.add]
    review: Optional[Dict[str, Any]]
    error: Optional[str]
    code_context: Optional[str]  # Related code from local repo
    discussion_context: Optional[Dict[str, Any]]  # Existing PR comments and reviews


def retrieve_pr_context_node(state: PRReviewState) -> Dict:
    """
    Retrieve additional context for PR review.
    This could include:
    - Related code from local repository
    - Similar past reviews
    - Related issues
    """
    logger.info("🔍 Retrieving context for PR review...")
    
    local_repo_path = state.get("local_repo_path")
    diff = state.get("diff", "")
    files = state.get("files", [])
    
    code_context = ""
    
    # If we have local repo, we can look at the full files
    if local_repo_path and files:
        import os
        context_parts = []
        
        for file_info in files[:5]:  # Limit to 5 files
            filename = file_info.get("filename", "")
            file_path = os.path.join(local_repo_path, filename)
            
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        if len(content) > 5000:
                            content = content[:5000] + "\n... (truncated)"
                        context_parts.append(f"### File: {filename}\n```\n{content}\n```")
                except Exception as e:
                    logger.warning(f"Failed to read {file_path}: {e}")
        
        if context_parts:
            code_context = "\n\n".join(context_parts)
            logger.info(f"✅ Retrieved {len(context_parts)} source files for context")
    
    return {"code_context": code_context}


def pr_review_node(state: PRReviewState, llm: ChatOpenAI) -> Dict:
    """
    Main PR review node - analyzes the PR and provides feedback.
    """
    logger.info("🔍 Analyzing PR...")
    
    title = state.get("title", "")
    body = state.get("body", "") or ""
    diff = state.get("diff", "")
    files = state.get("files", [])
    code_context = state.get("code_context", "")
    
    # Build file summary
    file_summary = "\n".join([
        f"- {f['filename']}: +{f.get('additions', 0)}/-{f.get('deletions', 0)} ({f.get('status', 'modified')})"
        for f in files[:20]
    ])
    
    # Truncate diff if too large
    max_diff_length = 15000
    if len(diff) > max_diff_length:
        diff = diff[:max_diff_length] + "\n\n... (diff truncated due to size)"
        logger.warning(f"⚠️  Diff truncated from {len(state.get('diff', ''))} to {max_diff_length} chars")
    
    # Build review prompt
    system_prompt = """You are an expert code reviewer. Analyze the given Pull Request and provide a comprehensive review.

Your review should be in TWO parts:

## Part 1: Overall Review
Provide a high-level assessment of the entire PR:
- Summary of what the PR does
- Overall code quality assessment
- Risk level and approval recommendation

## Part 2: Line-Level Comments
For key changes in the diff, provide SPECIFIC line-level feedback:
- Reference the exact file and line numbers from the diff
- Quote the specific code being discussed
- Explain what's good or what needs improvement

Be specific! Reference file names, line numbers, and quote actual code from the diff.

Respond in JSON format with the following structure:
{
    "summary": "Brief overview of what this PR accomplishes",
    "change_analysis": "Detailed analysis of the main changes and their purpose",
    "potential_issues": [
        {"severity": "high/medium/low", "file": "path/to/file.ts", "line": "123", "description": "..."}
    ],
    "suggestions": [
        {"file": "path/to/file.ts", "line": "45-50", "suggestion": "...", "code_suggestion": "optional improved code"}
    ],
    "line_comments": [
        {
            "file": "path/to/file.ts",
            "line_start": 205,
            "line_end": 208,
            "type": "comment/suggestion/issue",
            "code_snippet": "// the actual code from diff",
            "comment": "Detailed explanation of this specific change"
        }
    ],
    "code_quality": {
        "score": 1-10,
        "comments": "..."
    },
    "risk_level": "LOW/MEDIUM/HIGH",
    "risk_factors": ["list of specific risks"],
    "overall_assessment": "APPROVE/REQUEST_CHANGES/COMMENT",
    "review_notes": "Additional context or notes for the PR author"
}

IMPORTANT: 
- In `line_comments`, include at least 2-3 specific comments about actual code changes from the diff
- Always quote the exact code being discussed in `code_snippet`
- Line numbers should match those in the diff (prefixed with + or -)"""

    # Build discussion context if available
    discussion_context = state.get("discussion_context", {})
    discussion_section = ""
    
    if discussion_context:
        # Format existing reviews
        reviews = discussion_context.get("reviews", [])
        if reviews:
            review_lines = []
            for r in reviews:
                state_emoji = {
                    "APPROVED": "✅", 
                    "CHANGES_REQUESTED": "❌", 
                    "COMMENTED": "💬"
                }.get(r.get("state", ""), "📝")
                body_preview = (r.get("body", "") or "")[:200]
                if len(r.get("body", "") or "") > 200:
                    body_preview += "..."
                review_lines.append(
                    f"  - {state_emoji} @{r.get('author', 'unknown')} ({r.get('state', 'COMMENTED')}): {body_preview}"
                )
            if review_lines:
                discussion_section += "### Previous Reviews\n" + "\n".join(review_lines) + "\n\n"
        
        # Format issue comments (general discussion)
        issue_comments = discussion_context.get("issue_comments", [])
        if issue_comments:
            comment_lines = []
            for c in issue_comments[:10]:  # Limit to 10 comments
                body_preview = (c.get("body", "") or "")[:300]
                if len(c.get("body", "") or "") > 300:
                    body_preview += "..."
                comment_lines.append(
                    f"  - 💬 @{c.get('author', 'unknown')}: {body_preview}"
                )
            if comment_lines:
                discussion_section += "### Discussion Comments\n" + "\n".join(comment_lines) + "\n\n"
        
        # Format review comments (line-level)
        review_comments = discussion_context.get("review_comments", [])
        if review_comments:
            rc_lines = []
            for c in review_comments[:15]:  # Limit to 15 comments
                file_path = c.get("path", "")
                line = c.get("line", "?")
                body_preview = (c.get("body", "") or "")[:200]
                if len(c.get("body", "") or "") > 200:
                    body_preview += "..."
                rc_lines.append(
                    f"  - 📝 @{c.get('author', 'unknown')} on `{file_path}:{line}`: {body_preview}"
                )
            if rc_lines:
                discussion_section += "### Line-Level Review Comments\n" + "\n".join(rc_lines) + "\n\n"
    
    user_content = f"""# Pull Request Review Request

## PR Title
{title}

## PR Description
{body if body else "(No description provided)"}

## Changed Files
{file_summary}
"""
    
    # Add discussion context if available
    if discussion_section:
        user_content += f"""
## Existing Discussion & Reviews
Please consider the following existing discussion. Avoid repeating concerns already raised. If issues have been acknowledged or addressed, note that in your review.

{discussion_section}
"""
    
    user_content += f"""
## Diff (with line numbers)
Please analyze this diff carefully. Note the + lines (additions) and - lines (deletions).
Pay special attention to:
- New logic being added
- Modified conditions or algorithms
- Error handling changes
- API changes

```diff
{diff}
```
"""

    if code_context:
        user_content += f"""
## Additional Context (Full Source Files)
For reference, here are the complete source files being modified:
{code_context}
"""

    try:
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content)
        ]
        
        response = llm.invoke(messages)
        content = response.content
        
        # Try to parse JSON from response
        try:
            # Handle markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            review = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Failed to parse review as JSON, using raw content")
            review = {
                "summary": content,
                "raw_response": True,
                "overall_assessment": "COMMENT"
            }
        
        logger.info(f"✅ PR review completed: {review.get('overall_assessment', 'N/A')}")
        
        return {
            "review": review,
            "messages": [HumanMessage(content=user_content), response]
        }
        
    except Exception as e:
        logger.error(f"❌ PR review failed: {e}")
        return {
            "error": str(e),
            "review": {
                "summary": f"Review failed: {e}",
                "error": True,
                "overall_assessment": "ERROR"
            }
        }


def run_pr_review(
    cfg: Config,
    repo: str,
    pr_number: int,
    pr_url: str,
    title: str,
    body: str,
    diff: str,
    files: List[Dict[str, Any]],
    local_repo_path: Optional[str] = None,
    discussion_context: Optional[Dict[str, Any]] = None,
) -> PRReviewResult:
    """
    Run the PR review workflow.
    
    Args:
        cfg: Application configuration
        repo: Repository full name (owner/repo)
        pr_number: PR number
        pr_url: PR URL
        title: PR title
        body: PR body/description
        diff: PR diff content
        files: List of changed files with metadata
        local_repo_path: Optional path to local repo for additional context
        discussion_context: Optional existing PR discussion (comments, reviews)
    
    Returns:
        PRReviewResult with review data
    """
    logger.info(f"🚀 Starting PR review for {repo}#{pr_number}")
    
    if discussion_context:
        total_items = discussion_context.get("total_comments", 0) + discussion_context.get("total_reviews", 0)
        logger.info(f"📚 Including {total_items} existing discussion items in context")
    
    # Initialize LLM
    llm = ChatOpenAI(
        base_url=cfg.llm.base_url,
        api_key=cfg.llm.api_key or "dummy",
        model=cfg.llm.model,
        temperature=0.3,
    )
    
    # Build the review graph
    workflow = StateGraph(PRReviewState)
    
    # Add nodes
    workflow.add_node("retrieve_context", retrieve_pr_context_node)
    workflow.add_node("review", lambda s: pr_review_node(s, llm))
    
    # Define edges
    workflow.add_edge("retrieve_context", "review")
    workflow.add_edge("review", END)
    
    # Set entry point
    workflow.set_entry_point("retrieve_context")
    
    # Compile the graph
    app = workflow.compile()
    
    # Initial state
    initial_state = {
        "repo": repo,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "title": title,
        "body": body or "",
        "diff": diff,
        "files": files,
        "local_repo_path": local_repo_path,
        "messages": [],
        "review": None,
        "error": None,
        "code_context": None,
        "discussion_context": discussion_context,
    }
    
    # Run the workflow
    try:
        result = app.invoke(initial_state)
        
        files_reviewed = [f.get("filename", "") for f in files]
        
        model_info = {
            "model": cfg.llm.model,
            "base_url": cfg.llm.base_url,
        }
        
        logger.info(f"✅ PR review completed for {repo}#{pr_number}")
        
        return PRReviewResult(
            review=result.get("review", {}),
            model_info=model_info,
            files_reviewed=files_reviewed,
        )
        
    except Exception as e:
        logger.error(f"❌ PR review workflow failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        return PRReviewResult(
            review={"error": str(e), "overall_assessment": "ERROR"},
            model_info={"model": cfg.llm.model},
            files_reviewed=[],
        )
