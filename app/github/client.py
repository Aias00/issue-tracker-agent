from __future__ import annotations
import requests
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

@dataclass
class GitHubUser:
    login: str

@dataclass
class GitHubIssue:
    number: int
    id: int
    html_url: str
    title: str
    user: GitHubUser
    state: str
    created_at: datetime
    body: Optional[str]

    @classmethod
    def from_dict(cls, data: dict) -> GitHubIssue:
        # Handle timezone Z manually if needed, or rely on fromisoformat in modern python
        # GitHub uses "2011-04-10T20:09:31Z"
        created_at_str = data["created_at"]
        if created_at_str.endswith("Z"):
            created_at_str = created_at_str[:-1] + "+00:00"
        
        return cls(
            number=data["number"],
            id=data["id"],
            html_url=data["html_url"],
            title=data["title"],
            user=GitHubUser(login=data["user"]["login"]),
            state=data["state"],
            created_at=datetime.fromisoformat(created_at_str),
            body=data.get("body")
        )

class GitHubClient:
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://api.github.com"

    def list_recent_issues(self, repo_full_name: str, limit: int = 100, state: str = "open") -> List[GitHubIssue]:
        headers = {
            "Accept": "application/vnd.github.v3+json"
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        # GitHub API pagination defaults to 30, max 100.
        # If limit > 100, we might need multiple pages, but for simplicity let's cap per request at 100.
        per_page = min(limit, 100)
        
        params = {
            "state": state,
            "per_page": per_page,
            "sort": "created",
            "direction": "desc"
        }
        url = f"{self.base_url}/repos/{repo_full_name}/issues"
        
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            items = resp.json()
            # Filter out Pull Requests which are technically issues in GitHub API
            issues = [GitHubIssue.from_dict(i) for i in items if "pull_request" not in i]
            return issues
        except Exception as e:
            logger.error(f"GitHub API Error for {repo_full_name}: {e}")
            raise
