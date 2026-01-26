from __future__ import annotations

from dataclasses import dataclass

from app.agent.graph import run_issue_agent
from app.agent.preprocess import clip_text

from app.config import Config
from app.notifiers.feishu.renderer import render_card_template_b
from app.storage.pg_store import PostgresStateStore


@dataclass
class Budget:
    remaining: int


def process_repo_with_budget(
    *,
    repo: str,
    cfg: Config,
    store: PostgresStateStore,
    gh,
    feishu,
    budget: Budget,
) -> int:
    """Process new issues in a repo under both per-repo and global budgets.

    Dedup key: (repo, issue_number).

    Returns number of newly processed issues in this run.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"🚀 Starting to process repo: {repo}")
    logger.info(f"📊 Budget: {budget.remaining} remaining, per-repo max: {cfg.agent.limits.max_new_issues_per_repo}")
    
    try:
        logger.info(f"📡 Fetching issues from GitHub...")
        issues = gh.list_recent_issues(
            repo_full_name=repo,
            limit=cfg.github.per_repo_fetch_limit,
            state="open",
        )
        
        logger.info(f"📥 Retrieved {len(issues)} issues from {repo}")

        per_repo_max = cfg.agent.limits.max_new_issues_per_repo
        max_body = cfg.agent.text.max_body_chars
        max_title = cfg.agent.text.max_title_chars

        processed = 0
        skipped_seen = 0

        for idx, issue in enumerate(issues, 1):
            logger.debug(f"🔄 Processing issue {idx}/{len(issues)}: #{issue.number} - {issue.title[:50]}...")
            
            if budget.remaining <= 0:
                logger.warning(f"⏸️  Budget exhausted, stopping processing")
                break
            if processed >= per_repo_max:
                logger.warning(f"⏸️  Reached per-repo limit ({per_repo_max}), stopping")
                break

            # Dedup: repo + issue_number
            if store.has_issue(repo, issue.number):
                skipped_seen += 1
                logger.debug(f"⏭️  Issue #{issue.number} already exists in database, skipping")
                continue
            
            logger.info(f"✨ New issue found: #{issue.number} - {issue.title[:60]}")

            try:
                issue_row_id = store.upsert_issue(
                    repo=repo,
                    issue_number=issue.number,
                    issue_id=issue.id,
                    issue_url=issue.html_url,
                    title=issue.title,
                    author_login=issue.user.login,
                    state=issue.state,
                    created_at=issue.created_at.isoformat(),
                )
                logger.info(f"💾 Saved issue to database with ID: {issue_row_id}")
            except Exception as db_error:
                logger.error(f"❌ Failed to save issue #{issue.number} to database: {db_error}")
                import traceback
                logger.error(f"   Traceback: {traceback.format_exc()}")
                continue

            title = clip_text(issue.title, max_title)
            body = clip_text(issue.body or "", max_body)

            # Skip automatic analysis as requested
            # User will manually trigger "Re-analyze" which will use local code context if available.
            logger.info(f"⏭️  Skipping auto-analysis for issue #{issue.number} (manual analysis only)")
            
            # We don't create analysis or notification records yet.
            processed += 1
            budget.remaining -= 1
            
            logger.info(f"📈 Progress: {processed} processed, {budget.remaining} budget remaining")

        # Summary logging
        logger.info(f"✅ Repo {repo} processing complete:")
        logger.info(f"   📝 New issues processed: {processed}")
        logger.info(f"   ⏭️  Already seen (skipped): {skipped_seen}")
        logger.info(f"   💰 Budget remaining: {budget.remaining}")
        detail = f"new_issues_processed={processed}, skipped_seen={skipped_seen}, budget_remaining={budget.remaining}"
        if budget.remaining <= 0:
            detail += ", stopped_reason=global_budget_exhausted"
        elif processed >= per_repo_max:
            detail += ", stopped_reason=per_repo_limit_reached"

        store.log_run(repo, "success", detail=detail)
        return processed

    except Exception as e:
        logger.error(f"❌ Fatal error processing repo {repo}: {e}")
        import traceback
        logger.error(f"   Full traceback:\n{traceback.format_exc()}")
        try:
            store.log_run(repo, "failed", detail=str(e))
        except Exception as log_error:
            logger.error(f"❌ Failed to log run error: {log_error}")
        return 0
