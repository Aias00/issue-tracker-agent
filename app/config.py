import os

class Config:
    def __init__(self):
        self.github = self.GitHubConfig()
        self.agent = self.AgentConfig()
        self.notifications = self.NotificationsConfig()
        self.validate_env_variables()

    class GitHubConfig:
        per_repo_fetch_limit = os.getenv('PER_REPO_FETCH_LIMIT')

    class AgentConfig:
        limits = {
            'max_new_issues_per_repo': os.getenv('MAX_NEW_ISSUES_PER_REPO'),
            'max_new_issues_total': os.getenv('MAX_NEW_ISSUES_TOTAL'),
        }
        text = {
            'max_body_chars': os.getenv('MAX_BODY_CHARS'),
            'max_title_chars': os.getenv('MAX_TITLE_CHARS'),
        }

    class NotificationsConfig:
        feishu = {
            'message': {
                'completeness': {
                    'max_missing_items': os.getenv('MAX_MISSING_ITEMS')
                }
            }
        }

    def validate_env_variables(self):
        required_vars = [
            'GITHUB_TOKEN',
            'REPOS',
            'SQLITE_PATH',
            'PER_REPO_FETCH_LIMIT',
            'MAX_NEW_ISSUES_PER_REPO',
            'MAX_NEW_ISSUES_TOTAL',
            'MAX_BODY_CHARS',
            'MAX_TITLE_CHARS',
            'MAX_MISSING_ITEMS',
            'FEISHU_WEBHOOK_URL',
            'LLM_BASE_URL',
            'LLM_API_KEY',
            'LLM_MODEL'
        ]
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            raise EnvironmentError(f"Missing environment variables: {', '.join(missing_vars)}")
