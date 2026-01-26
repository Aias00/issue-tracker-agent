from dataclasses import dataclass
from typing import Any, Dict, List, TypedDict, Annotated, Optional
import json
import logging
import operator
import re

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from langgraph.graph import StateGraph, END

from app.config import Config

logger = logging.getLogger(__name__)

@dataclass
class AgentResult:
    analysis: Dict[str, Any]
    model_info: Dict[str, Any]
    card_data: Dict[str, Any]

class AgentState(TypedDict):
    repo: str
    title: str
    body: str
    issue_url: str
    local_repo_path: Optional[str] # New field
    
    messages: Annotated[List[BaseMessage], operator.add]
    analysis: Optional[Dict[str, Any]]
    error: Optional[str]
    retry_count: int
    architecture_plan: Optional[str]
    should_analyze: Optional[bool]
    bug_root_cause: Optional[str]
    code_context: Optional[str] # New field

def retrieve_context_node(state: AgentState):
    """Retrieve code context using vector search if available, fallback to grep"""
    path = state.get("local_repo_path")
    
    # Try to get from global memory store if available
    try:
        from app.web.server import MEMORY_STORE
        memory_store = MEMORY_STORE
    except:
        memory_store = None
    
    if not path:
        logger.info("No local repo path provided, skipping context retrieval")
        return {"code_context": None}
    
    import os
    import subprocess
    
    if not os.path.exists(path):
        logger.warning(f"Local path not found: {path}")
        return {"code_context": f"Error: Local path {path} not found"}

    # Extract keywords from title and body
    keywords = re.sub(r'[^\w\s]', '', state['title']).split()
    keywords.extend(re.sub(r'[^\w\s]', '', state['body'][:200]).split())
    keywords = [k for k in keywords if len(k) > 3 and k.lower() not in ['bug', 'feature', 'issue', 'request', 'fail', 'error', 'title']]
    keywords = list(set(keywords))[:5]  # Unique top 5
    
    if not keywords:
        return {"code_context": "No specific keywords found for search."}

    context_parts = []
    
    # Strategy 1: Vector Search (if memory store is available and has embeddings)
    if memory_store and hasattr(memory_store, 'embedding_function') and memory_store.embedding_function:
        try:
            logger.info("Using vector search for context retrieval")
            query_text = f"{state['title']} {state['body'][:500]}"
            query_embedding = memory_store.embed_text(query_text)
            
            results = memory_store.search_code_embeddings(
                query_embedding=query_embedding,
                repo=state['repo'],
                limit=5
            )
            
            if results:
                vector_context = "**Vector Search Results:**\n"
                for i, result in enumerate(results, 1):
                    similarity = result.get('similarity', 0)
                    if similarity > 0.5:  # Only include relevant results
                        vector_context += f"\n{i}. {result['file_path']} (similarity: {similarity:.2f})\n"
                        vector_context += f"```\n{result['chunk_text'][:500]}...\n```\n"
                
                if len(vector_context) > 100:
                    context_parts.append(vector_context)
                    logger.info(f"Found {len(results)} relevant code chunks via vector search")
        except Exception as e:
            logger.warning(f"Vector search failed, falling back to grep: {e}")
    
    # Strategy 2: Grep fallback (if vector search didn't find enough or failed)
    if not context_parts:
        logger.info("Using grep search for context retrieval")
        try:
            # File name search
            found_files = []
            for root, dirs, files in os.walk(path):
                if '.git' in dirs: dirs.remove('.git')
                if '__pycache__' in dirs: dirs.remove('__pycache__')
                if 'node_modules' in dirs: dirs.remove('node_modules')
                
                for file in files:
                    for k in keywords:
                        if k.lower() in file.lower():
                            full_p = os.path.join(root, file)
                            rel_p = os.path.relpath(full_p, path)
                            found_files.append(rel_p)
                            break
                if len(found_files) > 5: break
            
            if found_files:
                context_parts.append(f"**Matching Files:**\n" + "\n".join(found_files))
                
        except Exception as e:
            logger.error(f"File search failed: {e}")

        # Grep content search
        try:
            search_term = keywords[0] if keywords else ""
            if search_term:
                cmd = ["grep", "-r", "-i", "-n", "-I", "-C", "2", "--exclude-dir=.*", "--exclude-dir=node_modules", search_term, "."]
                proc = subprocess.run(cmd, cwd=path, capture_output=True, text=True, timeout=5)
                output = proc.stdout
                if len(output) > 2000:
                    output = output[:2000] + "...(truncated)"
                
                if output.strip():
                    context_parts.append(f"**Grep Search Results for '{search_term}':**\n{output}")
        except Exception as e:
            logger.error(f"Grep failed: {e}")
    
    # Strategy 3: Search similar past analyses
    if memory_store and hasattr(memory_store, 'embedding_function') and memory_store.embedding_function:
        try:
            query_text = f"{state['title']} {state['body'][:300]}"
            query_embedding = memory_store.embed_text(query_text)
            
            similar_analyses = memory_store.search_similar_analyses(
                query_embedding=query_embedding,
                limit=3
            )
            
            if similar_analyses:
                history_context = "\n**Similar Past Issues:**\n"
                for i, analysis in enumerate(similar_analyses, 1):
                    similarity = analysis.get('similarity', 0)
                    if similarity > 0.6:  # Only highly similar cases
                        history_context += f"\n{i}. {analysis['issue_title']} (similarity: {similarity:.2f})\n"
                        history_context += f"   Category: {analysis.get('issue_category', 'N/A')}\n"
                        history_context += f"   Solution: {analysis['solution_summary'][:200]}...\n"
                
                if len(history_context) > 100:
                    context_parts.append(history_context)
                    logger.info(f"Found {len(similar_analyses)} similar past analyses")
        except Exception as e:
            logger.warning(f"Similar analysis search failed: {e}")

    final_context = "\n\n".join(context_parts) if context_parts else "No relevant context found."
    logger.info(f"Retrieved context length: {len(final_context)}")
    return {"code_context": final_context}


def analyze_node(state: AgentState, llm: ChatOpenAI):
    logger.info(f"Analyzing issue for {state['repo']}")
    
    error_context = ""
    if state.get("error"):
        error_context = f"\n\nPREVIOUS ATTEMPT ALLAYED. ERROR: {state['error']}\nPlease fix the JSON format."

    code_context_section = ""
    if state.get("code_context"):
        code_context_section = f"\n\nLocal Code Context:\n{state['code_context']}\n"

    prompt = f"""
    You are an expert software engineer analyzing GitHub issues.
    
    Repo: {state['repo']}
    Title: {state['title']}
    Body:
    {state['body']}
    {code_context_section}
    {error_context}
    
    Please analyze this issue and provide a structured JSON response with the following fields:
    - summary: A concise summary of the issue.
    - priority: High, Medium, or Low. Based on urgency and impact.
    - category: Bug, Feature, Question, Documentation, or Other.
    - key_points: A list of string key points extracted from the issue.
    
    Return ONLY valid JSON. Do not include any explanation outside the JSON.
    """
    
    messages = [HumanMessage(content=prompt)]
    try:
        response = llm.invoke(messages)
        return {
            "messages": [response], 
            "analysis": None, 
            "error": None,
            "retry_count": state["retry_count"] + 1
        }
    except Exception as e:
        logger.error(f"LLM invoke failed: {e}")
        return {"error": str(e), "retry_count": state["retry_count"] + 1}

def architect_node(state: AgentState, llm: ChatOpenAI):
    """Generates an architectural plan for coding tasks"""
    logger.info("Executing Architect Node")
    analysis = state.get("analysis", {})
    code_context_section = f"\nCode Context:\n{state.get('code_context', 'N/A')}" if state.get('code_context') else ""
    
    prompt = f"""
    You are a Senior System Architect.
    
    Issue Analysis:
    Summary: {analysis.get('summary')}
    Category: {analysis.get('category')}
    Key Points: {analysis.get('key_points')}
    
    Origin Issue:
    Title: {state['title']}
    Body: {state['body']}
    {code_context_section}
    
    The initial analysis suggests this is a coding task. 
    Please provide a high-level architectural design or implementation plan.
    Focus on:
    1. Files likely to be modified.
    2. Key components or classes involved.
    3. Step-by-step implementation strategy.
    4. Potential risks or edge cases.
    
    Keep it professional and concise.
    """
    
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        return {"architecture_plan": response.content}
    except Exception as e:
        logger.error(f"Architect node failed: {e}")
        return {"architecture_plan": "Architecture planning failed."}

def bug_analysis_node(state: AgentState, llm: ChatOpenAI):
    """Analyze bug root cause if the issue is a bug"""
    logger.info(f"Analyzing bug root cause for: {state['title'][:50]}")
    
    analysis = state.get("analysis", {})
    code_context_section = f"\nCode Context:\n{state.get('code_context', 'N/A')}" if state.get('code_context') else ""
    
    prompt = f"""
    You are a analyzing a bug report. Provide a root cause analysis.
    
    Issue Title: {state['title']}
    Issue Body: {state['body']}
    {code_context_section}
    
    Initial Analysis:
    - Category: {analysis.get('category', 'Unknown')}
    - Priority: {analysis.get('priority', 'Unknown')}
    - Summary: {analysis.get('summary', 'N/A')}
    
    Please analyze:
    1. **Possible Root Cause**: What might be causing this bug?
    2. **Affected Components**: Which parts of the system are likely affected?
    3. **Reproduction Steps**: If mentioned, summarize how to reproduce.
    4. **Impact Assessment**: What's the impact on users/system?
    5. **Suggested Investigation**: Where should developers look first?
    
    Provide a concise but thorough analysis in markdown format.
    """
    
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        logger.info(f"Bug root cause analysis completed")
        return {"bug_root_cause": response.content}
    except Exception as e:
        logger.error(f"Bug analysis node failed: {e}")
        return {"bug_root_cause": "Bug analysis failed."}

# --- Graph DSL Support ---

# Updated graph to include retrieve_context
CURRENT_GRAPH_CONFIG = {
    "nodes": [
        {"id": "retrieve_context", "type": "function", "function": "retrieve_context_node"},
        {"id": "triage", "type": "function", "function": "triage_node"},
        {"id": "analyze", "type": "function", "function": "analyze_node"},
        {"id": "parse", "type": "function", "function": "parse_node"},
        {"id": "routing", "type": "function", "function": "router_node"},
        {"id": "bug_analysis", "type": "function", "function": "bug_analysis_node"},
        {"id": "architect", "type": "function", "function": "architect_node"}
    ],
    "edges": [
        {"source": "retrieve_context", "target": "triage"}, # Start retrieval first, then triage
        {"source": "analyze", "target": "parse"},
        {"source": "bug_analysis", "target": "architect"}
    ],
    "conditional_edges": [
        {
            "source": "triage",
            "condition": "should_proceed_with_analysis",
            "paths": {
                "skip": "__end__",
                "analyze": "analyze"
            }
        },
        {
            "source": "parse",
            "condition": "should_retry",
            "paths": {
                "end": "__end__",
                "continue": "routing",
                "analyze": "analyze"
            }
        },
        {
            "source": "routing",
            "condition": "should_analyze_bug",
            "paths": {
                "bug_analysis": "bug_analysis",
                "architect": "architect",
                "end": "__end__"
            }
        }
    ],
    "entry_point": "retrieve_context"
}

def get_current_graph_config() -> Dict[str, Any]:
    return CURRENT_GRAPH_CONFIG

# ... (cache helpers) ...

class GraphBuilder:
    def __init__(self, llm: ChatOpenAI):
        self.llm = llm
        self.functions = {
            "triage_node": lambda s: triage_node(s, self.llm),
            "analyze_node": lambda s: analyze_node(s, self.llm),
            "parse_node": parse_node,
            "router_node": router_node,
            "bug_analysis_node": lambda s: bug_analysis_node(s, self.llm),
            "architect_node": lambda s: architect_node(s, self.llm),
            "retrieve_context_node": retrieve_context_node
        }
    # ...

# Updated run_issue_agent to use dynamic builder
def run_issue_agent(
    cfg: Config,
    repo: str,
    title: str,
    body: str,
    issue_url: str,
    local_repo_path: Optional[str] = None
) -> AgentResult:
    
    llm = create_langchain_client(cfg)
    
    if not llm:
        # Fallback if LLM is not configured
        return AgentResult(
            analysis={
                "summary": "AI Analysis Skipped (LLM not configured)",
                "priority": "N/A",
                "category": "N/A"
            },
            model_info={"model": "unknown", "status": "skipped"},
            card_data={
                "title": f"[{repo}] {title}",
                "summary": "AI Analysis Skipped",
                "priority": "N/A",
                "category": "N/A",
                "issue_url": issue_url
            }
        )

    # Use GraphBuilder with caching
    app = get_or_build_graph(llm, CURRENT_GRAPH_CONFIG)
    
    initial_state = {
        "repo": repo,
        "title": title,
        "body": body,
        "issue_url": issue_url,
        "local_repo_path": local_repo_path,
        "messages": [],
        "analysis": None,
        "error": None,
        "retry_count": 0,
        "code_context": None
    }
    
    final_state = app.invoke(initial_state)
    
    analysis = final_state.get("analysis")
    if not analysis:
        analysis = {
            "summary": "Failed to analyze issue.",
            "priority": "Unknown",
            "category": "Unknown",
            "error": final_state.get("error")
        }

    # Merge architecture plan if available
    if final_state.get("architecture_plan"):
        analysis["architecture_plan"] = final_state["architecture_plan"]
    
    # Merge bug root cause analysis if available
    if final_state.get("bug_root_cause"):
        analysis["bug_root_cause"] = final_state["bug_root_cause"]
    
    # Save to analysis memory (episodic memory) if successful
    if analysis.get("category") and analysis.get("summary"):
        try:
            from app.web.server import MEMORY_STORE
            if MEMORY_STORE and hasattr(MEMORY_STORE, 'embedding_function') and MEMORY_STORE.embedding_function:
                # Create embedding for this analysis
                memory_text = f"{title} {analysis.get('summary', '')} {analysis.get('bug_root_cause', '')[:500]}"
                embedding = MEMORY_STORE.embed_text(memory_text)
                
                # Save to memory (we don't have issue_id yet, will be set later)
                MEMORY_STORE.insert_analysis_memory(
                    issue_id=None,  # Will be updated later when we have the DB ID
                    issue_title=title,
                    issue_category=analysis.get("category"),
                    solution_summary=analysis.get("bug_root_cause") or analysis.get("architecture_plan") or analysis.get("summary"),
                    embedding=embedding
                )
                logger.info("Saved analysis to episodic memory")
        except Exception as e:
            logger.warning(f"Failed to save analysis memory: {e}")
        
    card_data = {
        "title": f"[{repo}] {title}",
        "summary": analysis.get("summary", ""),
        "priority": analysis.get("priority", "Unknown"),
        "category": analysis.get("category", "Unknown"),
        "issue_url": issue_url
    }
    
    model_info = {
        "model": llm.model_name,
        "execution_mode": "LangGraph Dynamic",
        "graph_nodes": len(CURRENT_GRAPH_CONFIG.get("nodes", [])),
        "raw_response_length": len(str(final_state.get("messages", [])))
    }
    
    return AgentResult(analysis=analysis, model_info=model_info, card_data=card_data)
