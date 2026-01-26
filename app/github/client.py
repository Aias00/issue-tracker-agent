from __future__ import annotations
import requests
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
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
    labels: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> GitHubIssue:
        # Handle timezone Z manually if needed, or rely on fromisoformat in modern python
        # GitHub uses "2011-04-10T20:09:31Z"
        created_at_str = data["created_at"]
        if created_at_str.endswith("Z"):
            created_at_str = created_at_str[:-1] + "+00:00"
        
        labels = [label["name"] for label in data.get("labels", [])]
        
        return cls(
            number=data["number"],
            id=data["id"],
            html_url=data["html_url"],
            title=data["title"],
            user=GitHubUser(login=data["user"]["login"]),
            state=data["state"],
            created_at=datetime.fromisoformat(created_at_str),
            body=data.get("body"),
            labels=labels
        )


@dataclass
class GitHubPR:
    """Pull Request data class"""
    number: int
    id: int
    html_url: str
    title: str
    user: GitHubUser
    state: str  # open, closed
    merged: bool
    head_ref: str  # source branch
    base_ref: str  # target branch
    head_sha: str  # latest commit SHA
    created_at: datetime
    updated_at: datetime
    merged_at: Optional[datetime]
    body: Optional[str]
    labels: List[str] = field(default_factory=list)
    diff_url: Optional[str] = None
    files_changed: int = 0
    additions: int = 0
    deletions: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> GitHubPR:
        def parse_datetime(s):
            if not s:
                return None
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s)
        
        labels = [label["name"] for label in data.get("labels", [])]
        
        return cls(
            number=data["number"],
            id=data["id"],
            html_url=data["html_url"],
            title=data["title"],
            user=GitHubUser(login=data["user"]["login"]),
            state=data["state"],
            merged=data.get("merged", False),
            head_ref=data["head"]["ref"] if "head" in data else "",
            base_ref=data["base"]["ref"] if "base" in data else "",
            head_sha=data["head"]["sha"] if "head" in data else "",
            created_at=parse_datetime(data["created_at"]),
            updated_at=parse_datetime(data.get("updated_at")),
            merged_at=parse_datetime(data.get("merged_at")),
            body=data.get("body"),
            labels=labels,
            diff_url=data.get("diff_url"),
            files_changed=data.get("changed_files", 0),
            additions=data.get("additions", 0),
            deletions=data.get("deletions", 0)
        )


class GitHubClient:
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://api.github.com"

    def _headers(self, accept: str = "application/vnd.github.v3+json") -> Dict[str, str]:
        """Get request headers with optional authentication"""
        headers = {"Accept": accept}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _log_rate_limit(self, resp: requests.Response):
        """Log rate limit info from response headers"""
        if 'X-RateLimit-Remaining' in resp.headers:
            remaining = resp.headers['X-RateLimit-Remaining']
            limit_total = resp.headers.get('X-RateLimit-Limit', 'unknown')
            logger.info(f"📊 GitHub API Rate Limit: {remaining}/{limit_total} remaining")

    def list_recent_issues(self, repo_full_name: str, limit: int = 100, state: str = "open") -> List[GitHubIssue]:
        logger.info(f"🔍 Fetching issues from {repo_full_name} (state={state}, limit={limit})")
        
        headers = self._headers()
        if self.token:
            logger.info(f"✅ Using authenticated GitHub API (token provided)")
        else:
            logger.warning(f"⚠️  Using anonymous GitHub API (rate limit: 60/hour)")
        
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
        
        logger.debug(f"📡 API Request: GET {url}")
        logger.debug(f"📋 Parameters: {params}")
        
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            self._log_rate_limit(resp)
            resp.raise_for_status()
            items = resp.json()
            
            logger.info(f"📦 Received {len(items)} items from GitHub API")
            
            # Filter out Pull Requests which are technically issues in GitHub API
            issues = [GitHubIssue.from_dict(i) for i in items if "pull_request" not in i]
            
            pr_count = len(items) - len(issues)
            if pr_count > 0:
                logger.debug(f"🔀 Filtered out {pr_count} Pull Requests")
            
            logger.info(f"✅ Found {len(issues)} actual issues (excluding PRs)")
            
            return issues
        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ GitHub API HTTP Error for {repo_full_name}: {e}")
            logger.error(f"   Status Code: {e.response.status_code}")
            logger.error(f"   Response: {e.response.text[:200]}")
            raise
            raise
        except Exception as e:
            logger.error(f"❌ GitHub API Error for {repo_full_name}: {e}")
            raise

    def get_issue(self, repo_full_name: str, issue_number: int) -> GitHubIssue:
        """Fetch a single Issue's details"""
        logger.info(f"🔍 Fetching Issue #{issue_number} from {repo_full_name}")
        
        url = f"{self.base_url}/repos/{repo_full_name}/issues/{issue_number}"
        
        try:
            resp = requests.get(url, headers=self._headers(), timeout=30)
            self._log_rate_limit(resp)
            resp.raise_for_status()
            data = resp.json()
            
            # Verify it's not a PR
            if "pull_request" in data:
                logger.warning(f"⚠️ Item #{issue_number} is a PR, not a regular issue")
            
            issue = GitHubIssue.from_dict(data)
            logger.info(f"✅ Fetched Issue #{issue_number}: {issue.title[:50]}")
            
            return issue
        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ GitHub API HTTP Error fetching Issue: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ GitHub API Error fetching Issue: {e}")
            raise

    # ============================================
    # Pull Request methods
    # ============================================
    
    @staticmethod
    def parse_github_url(url: str) -> Tuple[str, int, str]:
        """
        Parse a GitHub URL (PR or Issue) and extract repo, number, and type.
        
        Supports formats:
        - https://github.com/owner/repo/pull/123  -> (owner/repo, 123, "pr")
        - https://github.com/owner/repo/issues/123 -> (owner/repo, 123, "issue")
        - owner/repo#123 -> (owner/repo, 123, "unknown") (Caller must verify)
        - owner/repo/pull/123 -> (owner/repo, 123, "pr")
        - owner/repo/issues/123 -> (owner/repo, 123, "issue")
        
        Returns: (repo_full_name, number, type)
        """
        # Format: https://github.com/owner/repo/pull/123
        match = re.match(r'https?://github\.com/([^/]+/[^/]+)/pull/(\d+)', url)
        if match:
            return match.group(1), int(match.group(2)), "pr"

        # Format: https://github.com/owner/repo/issues/123
        match = re.match(r'https?://github\.com/([^/]+/[^/]+)/issues/(\d+)', url)
        if match:
            return match.group(1), int(match.group(2)), "issue"
        
        # Format: owner/repo#123
        match = re.match(r'([^/]+/[^#]+)#(\d+)', url)
        if match:
            return match.group(1), int(match.group(2)), "unknown"
        
        # Format: owner/repo/pull/123
        match = re.match(r'([^/]+/[^/]+)/pull/(\d+)', url)
        if match:
            return match.group(1), int(match.group(2)), "pr"
            
        # Format: owner/repo/issues/123
        match = re.match(r'([^/]+/[^/]+)/issues/(\d+)', url)
        if match:
            return match.group(1), int(match.group(2)), "issue"
            
        # Format: https://github.com/owner/repo/actions/runs/123/job/456
        # Return job_id as number, type as "action_job"
        match = re.search(r'github\.com/([^/]+/[^/]+)/actions/runs/\d+/job/(\d+)', url)
        if match:
             return match.group(1), int(match.group(2)), "action_job"

        # Format: https://github.com/owner/repo/actions/runs/123
        # Return run_id as number, type as "action_run"
        match = re.search(r'github\.com/([^/]+/[^/]+)/actions/runs/(\d+)', url)
        if match:
             return match.group(1), int(match.group(2)), "action_run"
        
        raise ValueError(f"Invalid GitHub URL format: {url}")
    
    def download_job_logs(self, repo_full_name: str, job_id: int) -> str:
        """
        Download raw logs for a workflow job.
        Note: GitHub API redirects to a raw text file log.
        """
        logger.info(f"📜 Fetching logs for Job #{job_id} in {repo_full_name}")
        
        # https://docs.github.com/en/rest/actions/workflow-jobs?apiVersion=2022-11-28#download-job-logs-for-a-workflow-run
        url = f"{self.base_url}/repos/{repo_full_name}/actions/jobs/{job_id}/logs"
        
        try:
            # allow_redirects=True is default, but just to be explicit
            resp = requests.get(url, headers=self._headers(), timeout=60, allow_redirects=True)
            self._log_rate_limit(resp)
            resp.raise_for_status()
            
            logs = resp.text
            
            if not logs:
                logger.warning(f"⚠️ Empty logs for Job #{job_id}")
                return ""
            
            logger.info(f"✅ Fetched logs: {len(logs)} characters")
            
            # Simple truncation strategy if logs are huge
            # Keep header info and the tail where errors likely are
            if len(logs) > 100000:
                logger.info("⚠️ Logs too large, truncating...")
                head = logs[:5000]
                tail = logs[-20000:]
                return f"{head}\n\n... [Log Truncated due to size] ...\n\n{tail}"
            
            return logs
            
        except Exception as e:
            logger.error(f"❌ Failed to download job logs: {e}")
            raise

    def get_pr(self, repo_full_name: str, pr_number: int) -> GitHubPR:
        """Fetch a single PR's details"""
        logger.info(f"🔍 Fetching PR #{pr_number} from {repo_full_name}")
        
        url = f"{self.base_url}/repos/{repo_full_name}/pulls/{pr_number}"
        
        try:
            resp = requests.get(url, headers=self._headers(), timeout=30)
            self._log_rate_limit(resp)
            resp.raise_for_status()
            data = resp.json()
            
            pr = GitHubPR.from_dict(data)
            logger.info(f"✅ Fetched PR #{pr_number}: {pr.title[:50]}")
            logger.info(f"   📊 +{pr.additions}/-{pr.deletions} in {pr.files_changed} files")
            
            return pr
        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ GitHub API HTTP Error fetching PR: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ GitHub API Error fetching PR: {e}")
            raise

    def get_pr_diff(self, repo_full_name: str, pr_number: int) -> str:
        """Fetch the diff content for a PR"""
        logger.info(f"📄 Fetching diff for PR #{pr_number}")
        
        url = f"{self.base_url}/repos/{repo_full_name}/pulls/{pr_number}"
        headers = self._headers(accept="application/vnd.github.v3.diff")
        
        try:
            resp = requests.get(url, headers=headers, timeout=60)
            self._log_rate_limit(resp)
            resp.raise_for_status()
            
            diff = resp.text
            logger.info(f"✅ Fetched diff: {len(diff)} characters")
            
            return diff
        except Exception as e:
            logger.error(f"❌ Failed to fetch PR diff: {e}")
            raise

    def get_pr_files(self, repo_full_name: str, pr_number: int) -> List[Dict[str, Any]]:
        """Fetch the list of files changed in a PR"""
        logger.info(f"📁 Fetching files for PR #{pr_number}")
        
        url = f"{self.base_url}/repos/{repo_full_name}/pulls/{pr_number}/files"
        
        try:
            resp = requests.get(url, headers=self._headers(), params={"per_page": 100}, timeout=30)
            self._log_rate_limit(resp)
            resp.raise_for_status()
            
            files = resp.json()
            logger.info(f"✅ Fetched {len(files)} changed files")
            
            return files
        except Exception as e:
            logger.error(f"❌ Failed to fetch PR files: {e}")
            raise

    def get_pr_by_url(self, pr_url: str) -> GitHubPR:
        """Fetch a PR by its URL"""
        repo, pr_number, _ = self.parse_github_url(pr_url)
        # Note: We ignore the type check here and let get_pr handle it/fail if ID doesn't exist as PR
        return self.get_pr(repo, pr_number)

    def list_recent_prs(self, repo_full_name: str, limit: int = 30, state: str = "open") -> List[GitHubPR]:
        """Fetch recent PRs for a repo"""
        logger.info(f"🔍 Fetching PRs from {repo_full_name} (state={state}, limit={limit})")
        
        url = f"{self.base_url}/repos/{repo_full_name}/pulls"
        params = {
            "state": state,
            "per_page": min(limit, 100),
            "sort": "created",
            "direction": "desc"
        }
        
        try:
            resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
            self._log_rate_limit(resp)
            resp.raise_for_status()
            
            items = resp.json()
            prs = [GitHubPR.from_dict(item) for item in items]
            
            logger.info(f"✅ Found {len(prs)} PRs")
            return prs
        except Exception as e:
            logger.error(f"❌ Failed to fetch PRs: {e}")
            raise

    def get_pr_comments(self, repo_full_name: str, pr_number: int) -> List[Dict[str, Any]]:
        """
        Fetch issue comments for a PR (general discussion comments).
        These are comments on the PR itself, not on specific lines of code.
        """
        logger.info(f"💬 Fetching issue comments for PR #{pr_number}")
        
        # PRs use the issues API for general comments
        url = f"{self.base_url}/repos/{repo_full_name}/issues/{pr_number}/comments"
        
        try:
            resp = requests.get(url, headers=self._headers(), params={"per_page": 100}, timeout=30)
            self._log_rate_limit(resp)
            resp.raise_for_status()
            
            comments = resp.json()
            logger.info(f"✅ Fetched {len(comments)} issue comments")
            
            # Format comments for easier use
            formatted = []
            for c in comments:
                formatted.append({
                    "id": c["id"],
                    "author": c["user"]["login"],
                    "body": c.get("body", ""),
                    "created_at": c["created_at"],
                    "updated_at": c.get("updated_at"),
                    "html_url": c["html_url"],
                    "type": "issue_comment"
                })
            
            return formatted
        except Exception as e:
            logger.error(f"❌ Failed to fetch PR comments: {e}")
            return []

    def get_pr_review_comments(self, repo_full_name: str, pr_number: int) -> List[Dict[str, Any]]:
        """
        Fetch review comments for a PR (line-level code review comments).
        These are comments on specific lines of code in the diff.
        """
        logger.info(f"📝 Fetching review comments for PR #{pr_number}")
        
        url = f"{self.base_url}/repos/{repo_full_name}/pulls/{pr_number}/comments"
        
        try:
            resp = requests.get(url, headers=self._headers(), params={"per_page": 100}, timeout=30)
            self._log_rate_limit(resp)
            resp.raise_for_status()
            
            comments = resp.json()
            logger.info(f"✅ Fetched {len(comments)} review comments")
            
            # Format comments for easier use
            formatted = []
            for c in comments:
                formatted.append({
                    "id": c["id"],
                    "author": c["user"]["login"],
                    "body": c.get("body", ""),
                    "path": c.get("path", ""),  # File path
                    "line": c.get("line"),  # Line number in the diff
                    "original_line": c.get("original_line"),
                    "diff_hunk": c.get("diff_hunk", ""),  # Code context
                    "created_at": c["created_at"],
                    "updated_at": c.get("updated_at"),
                    "html_url": c["html_url"],
                    "in_reply_to_id": c.get("in_reply_to_id"),  # For threaded replies
                    "type": "review_comment"
                })
            
            return formatted
        except Exception as e:
            logger.error(f"❌ Failed to fetch PR review comments: {e}")
            return []

    def get_pr_reviews(self, repo_full_name: str, pr_number: int) -> List[Dict[str, Any]]:
        """
        Fetch PR reviews (approval, changes requested, commented).
        Each review represents a formal review action with an optional body.
        """
        logger.info(f"📋 Fetching reviews for PR #{pr_number}")
        
        url = f"{self.base_url}/repos/{repo_full_name}/pulls/{pr_number}/reviews"
        
        try:
            resp = requests.get(url, headers=self._headers(), params={"per_page": 100}, timeout=30)
            self._log_rate_limit(resp)
            resp.raise_for_status()
            
            reviews = resp.json()
            logger.info(f"✅ Fetched {len(reviews)} reviews")
            
            # Format reviews for easier use
            formatted = []
            for r in reviews:
                formatted.append({
                    "id": r["id"],
                    "author": r["user"]["login"],
                    "body": r.get("body", ""),
                    "state": r["state"],  # APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED, PENDING
                    "submitted_at": r.get("submitted_at"),
                    "html_url": r["html_url"],
                    "type": "review"
                })
            
            return formatted
        except Exception as e:
            logger.error(f"❌ Failed to fetch PR reviews: {e}")
            return []

    def get_all_pr_discussion(self, repo_full_name: str, pr_number: int) -> Dict[str, Any]:
        """
        Fetch all discussion activity for a PR, combining:
        - Issue comments (general discussion)
        - Review comments (line-level feedback)
        - Reviews (approval/changes requested)
        
        Returns a combined and sorted timeline of all activity.
        """
        logger.info(f"📚 Fetching all discussion for PR #{pr_number}")
        
        issue_comments = self.get_pr_comments(repo_full_name, pr_number)
        review_comments = self.get_pr_review_comments(repo_full_name, pr_number)
        reviews = self.get_pr_reviews(repo_full_name, pr_number)
        
        # Combine all and sort by created_at
        all_items = issue_comments + review_comments + reviews
        
        # Sort by timestamp
        def get_timestamp(item):
            ts = item.get("created_at") or item.get("submitted_at") or ""
            return ts
        
        all_items.sort(key=get_timestamp)
        
        summary = {
            "total_comments": len(issue_comments) + len(review_comments),
            "total_reviews": len(reviews),
            "issue_comments": issue_comments,
            "review_comments": review_comments,
            "reviews": reviews,
            "timeline": all_items
        }
        
        logger.info(f"✅ Total discussion items: {len(all_items)}")
        return summary

