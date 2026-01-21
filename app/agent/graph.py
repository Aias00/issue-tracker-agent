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
    
    messages: Annotated[List[BaseMessage], operator.add]
    analysis: Optional[Dict[str, Any]]
    error: Optional[str]
    retry_count: int
    architecture_plan: Optional[str]
    should_analyze: Optional[bool]  # AI triage result
    bug_root_cause: Optional[str]  # Bug root cause analysis

def create_langchain_client(cfg: Config):
    llm_cfg = cfg.llm
    if not llm_cfg.base_url:
        return None

    return ChatOpenAI(
        base_url=llm_cfg.base_url,
        api_key=llm_cfg.api_key or "dummy", # langchain might require non-empty key
        model=llm_cfg.model,
        temperature=0
    )

def triage_node(state: AgentState, llm: ChatOpenAI):
    """Quick AI triage to determine if issue needs detailed analysis"""
    logger.info(f"Triaging issue: {state['title'][:50]}")
    
    prompt = f"""
    You are a GitHub issue triage assistant. Quickly determine if this issue needs detailed AI analysis.
    
    Title: {state['title']}
    Body: {state['body'][:500]}
    
    Skip analysis for:
    - Simple release/version/package requests
    - Very short or unclear issues
    - Spam or off-topic content
    
    Respond with ONLY "YES" if it needs analysis, or "NO" if it should be skipped.
    """
    
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        decision = response.content.strip().upper()
        should_analyze = decision == "YES"
        
        logger.info(f"Triage decision: {'ANALYZE' if should_analyze else 'SKIP'}")
        return {
            "should_analyze": should_analyze,
            "messages": [response]
        }
    except Exception as e:
        logger.error(f"Triage failed: {e}, defaulting to analyze")
        return {"should_analyze": True}  # Default to analyzing on error

def analyze_node(state: AgentState, llm: ChatOpenAI):
    logger.info(f"Analyzing issue for {state['repo']}")
    
    # If this is a retry, we might want to add the error to the prompt
    error_context = ""
    if state.get("error"):
        error_context = f"\n\nPREVIOUS ATTEMPT ALLAYED. ERROR: {state['error']}\nPlease fix the JSON format."

    prompt = f"""
    You are an expert software engineer analyzing GitHub issues.
    
    Repo: {state['repo']}
    Title: {state['title']}
    Body:
    {state['body']}
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
        content = response.content
        return {
            "messages": [response], 
            "analysis": None, 
            "error": None,
            "retry_count": state["retry_count"] + 1
        }
    except Exception as e:
        logger.error(f"LLM invoke failed: {e}")
        return {"error": str(e), "retry_count": state["retry_count"] + 1}

def parse_node(state: AgentState):
    """Parses the last message content into JSON"""
    if not state["messages"]:
        return {"error": "No messages to parse"}
        
    last_message = state["messages"][-1]
    content = last_message.content
    
    try:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            json_str = match.group(0)
            analysis = json.loads(json_str)
            # basic validation
            required = ["summary", "priority", "category"]
            if not all(k in analysis for k in required):
                return {"error": f"Missing required fields. Got: {list(analysis.keys())}"}
                
            return {"analysis": analysis, "error": None}
        else:
             return {"error": "No JSON object found in response"}
    except Exception as e:
        return {"error": f"JSON parse error: {str(e)}"}

def architect_node(state: AgentState, llm: ChatOpenAI):
    """Generates an architectural plan for coding tasks"""
    logger.info("Executing Architect Node")
    analysis = state.get("analysis", {})
    
    prompt = f"""
    You are a Senior System Architect.
    
    Issue Analysis:
    Summary: {analysis.get('summary')}
    Category: {analysis.get('category')}
    Key Points: {analysis.get('key_points')}
    
    Origin Issue:
    Title: {state['title']}
    Body: {state['body']}
    
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
    
    prompt = f"""
    You are analyzing a bug report. Provide a root cause analysis.
    
    Issue Title: {state['title']}
    Issue Body: {state['body']}
    
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

def should_retry(state: AgentState):
    if state["analysis"]:
        return "continue" # Go to next check
    if state["retry_count"] >= 2: # Max 2 retries
        return "end"
    return "analyze"

def should_architect(state: AgentState):
    """Decide if we need an architect"""
    analysis = state.get("analysis", {})
    category = analysis.get("category", "").lower()
    priority = analysis.get("priority", "").lower()
    
    # Trigger for Features or High priority items (bugs already analyzed)
    if category == "feature" or priority == "high":
        return "architect"
    return "end"

def should_proceed_with_analysis(state: AgentState):
    """Check triage result to decide if we should analyze"""
    if state.get("should_analyze") is False:
        return "skip"
    return "analyze"

def should_analyze_bug(state: AgentState):
    """Check if this is a bug that needs root cause analysis"""
    analysis = state.get("analysis", {})
    category = analysis.get("category", "").lower()
    
    if category == "bug":
        return "bug_analysis"
    # If not a bug, check if we need architect
    priority = analysis.get("priority", "").lower()
    if category == "feature" or priority == "high":
        return "architect"
    return "end"

# --- Graph DSL Support ---

# Current active graph definition (JSON)
CURRENT_GRAPH_CONFIG = {
    "nodes": [
        {"id": "analyze", "type": "function", "function": "analyze_node"},
        {"id": "parse", "type": "function", "function": "parse_node"},
        {"id": "architect", "type": "function", "function": "architect_node"}
    ],
    "edges": [
        {"source": "analyze", "target": "parse"}
    ],
    "conditional_edges": [
        {
            "source": "parse",
            "condition": "should_retry",
            "paths": {
                "end": "__end__",
                "continue": "check_architect",
                "analyze": "analyze"
            }
        },
        {
            # Virtual node for check, actually handled by conditional edge from parse logic?
            # LangGraph conditional edges are typically strictly from a node.
            # To chaining conditions, we might need a dummy node or let 'parse' output determine next.
            # Here I used "continue" path from should_retry to point to a new conditional check.
            # But conditional edges come FROM a node. 
            # So 'parse' node output goes to 'should_retry' condition.
            # The 'should_retry' condition returns a path key.
            # If we want to chain, we usually insert a passthrough node.
            # Let's add a simple 'router' node or just handle it.
            # To make it simple for the user DSL, let's assume 'continue' goes to 'router'.
            "source": "router", 
            "condition": "should_architect",
            "paths": {
                "architect": "architect",
                "end": "__end__"
            }
        }
    ],
    # Wait, the above JSON structure for conditional edges is List[Dict] where each Dict describes one conditional edge.
    # We need a node to attach 'should_architect' to.
    # Let's add a light-weight 'router' node that just passes state.
    "entry_point": "analyze"
}

# Actually, to properly support the flow analyze -> parse -> (retry?) -> (architect?) -> end
# using standard nodes is cleaner.
def router_node(state: AgentState):
    return {} # No-op, just for routing

CURRENT_GRAPH_CONFIG = {
    "nodes": [
        {"id": "analyze", "type": "function", "function": "analyze_node"},
        {"id": "parse", "type": "function", "function": "parse_node"},
        {"id": "architect", "type": "function", "function": "architect_node"}
    ],
    "edges": [
        {"source": "analyze", "target": "parse"},
        {"source": "architect", "target": "__end__"}
    ],
    "conditional_edges": [
        {
            "source": "parse",
            "condition": "should_retry",
            "paths": {
                "end": "__end__", # Retry failed
                "continue": "architect_check", # Success, check if we need architect
                "analyze": "analyze" # Retry
            }
        },
        {
            "source": "architect_check", # We need to map this in GraphBuilder to a real node/condition
            # Wait, 'architect_check' is not a node yet.
            # Let's use a router node called 'routing'
            "condition": "should_architect",
            "paths": {
                "architect": "architect",
                "end": "__end__"
            }
        }
    ],
    "entry_point": "analyze"
}

# Final graph configuration with AI triage and bug analysis
CURRENT_GRAPH_CONFIG = {
    "nodes": [
        {"id": "triage", "type": "function", "function": "triage_node"},
        {"id": "analyze", "type": "function", "function": "analyze_node"},
        {"id": "parse", "type": "function", "function": "parse_node"},
        {"id": "routing", "type": "function", "function": "router_node"},
        {"id": "bug_analysis", "type": "function", "function": "bug_analysis_node"},
        {"id": "architect", "type": "function", "function": "architect_node"}
    ],
    "edges": [
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
    "entry_point": "triage"
}

def get_current_graph_config() -> Dict[str, Any]:
    return CURRENT_GRAPH_CONFIG

def update_current_graph_config(config: Dict[str, Any]):
    global CURRENT_GRAPH_CONFIG, _CACHED_GRAPH
    CURRENT_GRAPH_CONFIG = config
    _CACHED_GRAPH = None  # Invalidate cache when config changes
    logger.info("Graph configuration updated, cache invalidated")

# Cache for compiled graph
_CACHED_GRAPH = None
_CACHED_GRAPH_CONFIG_HASH = None
_CACHED_LLM_MODEL = None

def _get_config_hash(config: Dict[str, Any]) -> str:
    """Generate a hash of the config for cache invalidation"""
    import hashlib
    import json
    config_str = json.dumps(config, sort_keys=True)
    return hashlib.md5(config_str.encode()).hexdigest()

def get_or_build_graph(llm: ChatOpenAI, config: Dict[str, Any]):
    """Get cached graph or build a new one if config/llm changed"""
    global _CACHED_GRAPH, _CACHED_GRAPH_CONFIG_HASH, _CACHED_LLM_MODEL
    
    config_hash = _get_config_hash(config)
    current_model = llm.model_name
    
    # Check if we can use cached graph
    if (_CACHED_GRAPH is not None and 
        _CACHED_GRAPH_CONFIG_HASH == config_hash and
        _CACHED_LLM_MODEL == current_model):
        logger.debug("Using cached LangGraph workflow")
        return _CACHED_GRAPH
    
    # Build new graph
    logger.info(f"Building LangGraph workflow with {len(config.get('nodes', []))} nodes")
    builder = GraphBuilder(llm)
    app = builder.build(config)
    
    # Cache it
    _CACHED_GRAPH = app
    _CACHED_GRAPH_CONFIG_HASH = config_hash
    _CACHED_LLM_MODEL = current_model
    
    return app

class GraphBuilder:
    def __init__(self, llm: ChatOpenAI):
        self.llm = llm
        self.functions = {
            "triage_node": lambda s: triage_node(s, self.llm),
            "analyze_node": lambda s: analyze_node(s, self.llm),
            "parse_node": parse_node,
            "router_node": router_node,
            "bug_analysis_node": lambda s: bug_analysis_node(s, self.llm),
            "architect_node": lambda s: architect_node(s, self.llm)
        }
        self.conditions = {
            "should_retry": should_retry,
            "should_architect": should_architect,
            "should_proceed_with_analysis": should_proceed_with_analysis,
            "should_analyze_bug": should_analyze_bug
        }

    def build(self, config: Dict[str, Any]):
        workflow = StateGraph(AgentState)
        
        # Add Nodes
        for node in config.get("nodes", []):
            func_name = node.get("function")
            if func_name in self.functions:
                workflow.add_node(node["id"], self.functions[func_name])
            else:
                logger.warning(f"Unknown function {func_name} for node {node['id']}")

        # Add Edges
        for edge in config.get("edges", []):
            workflow.add_edge(edge["source"], edge["target"])

        # Add Conditional Edges
        for c_edge in config.get("conditional_edges", []):
            cond_name = c_edge.get("condition")
            if cond_name in self.conditions:
                paths = c_edge.get("paths", {})
                # Replace string "__end__" with END constant
                clean_paths = {k: (END if v == "__end__" else v) for k, v in paths.items()}
                workflow.add_conditional_edges(
                    c_edge["source"],
                    self.conditions[cond_name],
                    clean_paths
                )

        # Set Entry Point
        entry = config.get("entry_point")
        if entry:
            workflow.set_entry_point(entry)
            
        return workflow.compile()

# ... (AgentState definition remains)

def create_langchain_client(cfg: Config):
    llm_cfg = cfg.llm
    if not llm_cfg.base_url:
        return None

    return ChatOpenAI(
        base_url=llm_cfg.base_url,
        api_key=llm_cfg.api_key or "dummy", # langchain might require non-empty key
        model=llm_cfg.model,
        temperature=0
    )

# Updated run_issue_agent to use dynamic builder
def run_issue_agent(
    cfg: Config,
    repo: str,
    title: str,
    body: str,
    issue_url: str
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
    
    # ... execution logic same as before ...
    initial_state = {
        "repo": repo,
        "title": title,
        "body": body,
        "issue_url": issue_url,
        "messages": [],
        "analysis": None,
        "error": None,
        "retry_count": 0
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
