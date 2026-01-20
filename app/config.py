import os
from dataclasses import dataclass, field
from typing import Dict

@dataclass
class GitHubConfig:
    token: str
    repos: str

@dataclass
class AgentLimits:
    per_repo_fetch_limit: int = field(default=100)
    max_new_issues_per_repo: int = field(default=5)
    max_new_issues_total: int = field(default=20)
    max_body_chars: int = field(default=2000)
    max_title_chars: int = field(default=100)
    max_missing_items: int = field(default=10)

@dataclass
class Notifications:
    feishu_message: str

@dataclass
class Config:
    github: GitHubConfig
    agent_limits: AgentLimits
    notifications: Notifications


def load_config_from_env() -> Config:
    github_config = GitHubConfig(
        token=os.getenv('GITHUB_TOKEN'),
        repos=os.getenv('REPOS')
    )

    agent_limits = AgentLimits(
        per_repo_fetch_limit=int(os.getenv('PER_REPO_FETCH_LIMIT', 100)),
        max_new_issues_per_repo=int(os.getenv('MAX_NEW_ISSUES_PER_REPO', 5)),
        max_new_issues_total=int(os.getenv('MAX_NEW_ISSUES_TOTAL', 20)),
        max_body_chars=int(os.getenv('MAX_BODY_CHARS', 2000)),
        max_title_chars=int(os.getenv('MAX_TITLE_CHARS', 100)),
        max_missing_items=int(os.getenv('MAX_MISSING_ITEMS', 10))
    )

    notifications = Notifications(
        feishu_message=os.getenv('FEISHU_WEBHOOK_URL')
    )

    return Config(github=github_config, agent_limits=agent_limits, notifications=notifications)