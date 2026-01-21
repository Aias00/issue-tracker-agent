import re

def normalize_repo_name(repo: str) -> str:
    """
    Normalize repository name to owner/repo format.
    Supports:
    - owner/repo
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - git@github.com:owner/repo.git
    """
    repo = repo.strip()
    
    # Pattern 1: https://github.com/owner/repo or https://github.com/owner/repo.git
    match = re.match(r'https?://github\.com/([^/]+)/([^/\.]+)', repo)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    
    # Pattern 2: git@github.com:owner/repo.git
    match = re.match(r'git@github\.com:([^/]+)/(.+?)(?:\.git)?$', repo)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    
    # Pattern 3: already in owner/repo format
    if '/' in repo and not repo.startswith('http'):
        return repo.replace('.git', '')
    
    return repo
