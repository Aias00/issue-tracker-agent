from __future__ import annotations

import os
from dataclasses import dataclass


def _require(name: str) -> str:
    v = os.getenv(name)
    if v is None or not str(v).strip():
        raise RuntimeError(f"Missing required env var: {name}")
    return v.strip()


def _get_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except Exception as e:
        raise RuntimeError(f"Invalid int env var {name}={v!r}") from e


@dataclass(frozen=True)
class GitHubConfig:
    token: str
    repos: str
    per_repo_fetch_limit: int


@dataclass(frozen=True)
class AgentLimitsConfig:
    max_new_issues_per_repo: int
    max_new_issues_total: int


@dataclass(frozen=True)
class AgentTextConfig:
    max_body_chars: int
    max_title_chars: int


@dataclass(frozen=True)
class AgentConfig:
    limits: AgentLimitsConfig
    text: AgentTextConfig


@dataclass(frozen=True)
class FeishuCompletenessConfig:
    max_missing_items: int


@dataclass(frozen=True)
class FeishuMessageConfig:
    webhook_url: str
    completeness: FeishuCompletenessConfig


@dataclass(frozen=True)
class FeishuConfig:
    message: Feishu其他MessageConfig


@dataclass(frozen=True)
class NotificationsConfig:
    feishu: FeishuConfig


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class AppConfig:
    sqlite_path: str


@dataclass(frozen=True)
class Config:
    github: GitHubConfig
    agent: AgentConfig
    notifications: NotificationsConfig
    llm: LLMConfig
    app: AppConfig


def load_config_from_env() -> Config:
    github_token = _require("GITHUB_TOKEN")
    repos = _require("REPOS")
    sqlite_path = _require("SQLITE_PATH")

    llm_base_url = _require("LLM_BASE_URL")
    llm_api_key = _require("LLM_API_KEY")
    llm_model = _require("LLM_MODEL")

    feishu_webhook_url = _require("FEISHU_WEBHOOK_URL")

    per_repo_fetch_limit = _get_int("PER_REPO_FETCH_LIMIT", 100)
    max_new_issues_per_repo = _get_int("MAX_NEW_ISSUES_PER_REPO", 5)
    max_new_issues_total = _get_int("MAX_NEW_ISSUES_TOTAL", 20)
    max_body_chars = _get_int("MAX_BODY_CHARS", 2000)
    max_title_chars = _get_int("MAX_TITLE_CHARS", 100)
    max_missing_items = _get_int("MAX_MISSING_ITEMS", 10)

    return Config(
        github=GitHubConfig(
            token=github_token,
            repos=repos,
            per_repo_fetch_limit=per_repo_fetch_limit,
        ),
        agent=AgentConfig(
            limits=AgentLimitsConfig(
                max_new_issues_per_repo=max_new_issues_per_repo,
                max_new_issues_total=max_new_issues_total,
            ),
            text=AgentTextConfig(
                max_body_chars=max_body_chars,
                max_title_chars=max_title_chars,
            ),
        ),
        notifications=NotificationsConfig(
            feishu=FeishuConfig(
                message=FeishuMessageConfig(
                    webhook_url=feishu_webhook_url,
                    completeness=FeishuCompletenessConfig

