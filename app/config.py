import os
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class GitHubConfig:
    token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    repos: str = field(default_factory=lambda: os.getenv("REPOS", ""))
    per_repo_fetch_limit: int = field(default_factory=lambda: int(os.getenv("PER_REPO_FETCH_LIMIT", 100)))  # Default to 100

@dataclass
class AgentLimits:
    max_new_issues_per_repo: int = field(default_factory=lambda: int(os.getenv("MAX_NEW_ISSUES_PER_REPO", 5)))  # Default to 5
    max_new_issues_total: int = field(default_factory=lambda: int(os.getenv("MAX_NEW_ISSUES_TOTAL", 20)))  # Default to 20
    max_body_chars: int = field(default_factory=lambda: int(os.getenv("MAX_BODY_CHARS", 1000)))  # Default to 1000
    max_title_chars: int = field(default_factory=lambda: int(os.getenv("MAX_TITLE_CHARS", 80)))  # Default to 80
    max_missing_items: int = field(default_factory=lambda: int(os.getenv("MAX_MISSING_ITEMS", 10)))  # Default to 10

dataclass
class AppConfig:
    sqlite_path: str = field(default_factory=lambda: os.getenv("SQLITE_PATH", "sqlite.db"))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "http://example.com"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "default-model"))
    webhook_url: str = field(default_factory=lambda: os.getenv("FEISHU_WEBHOOK_URL", ""))

def load_config_from_env() -> dict:
    return {
        "github": GitHubConfig(),
        "agent": AgentLimits(),
        "app": AppConfig(),
    }