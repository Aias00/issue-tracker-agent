from __future__ import annotations

from dataclasses import dataclass

from app.agent.graph import run_issue_agent
from app.agent.preprocess import clip_text

from app.config import Config
from app.notifiers.feishu.renderer import render_card_template_b
from app.storage.sqlite_store import SQLiteStateStore


@dataclass
class Budget:
    remaining: int


def process_repo_with_budget(
    *,
    repo: str,
    cfg: Config,
    store: SQLiteStateStore,
    gh,
    feishu,
    budget: Budget,
) -> int:
    """Process new issues in a repo under both per-repo and global budgets.

    Dedup key: (repo, issue_number).

    Returns number of newly processed issues in this run.
    """
    try:
        issues = gh.list_recent_issues(
            repo_full_name=repo,
            limit=cfg.github.per_repo_fetch_limit,
            state="open",
        )

        per_repo_max = cfg.agent.limits.max_new_issues_per_repo
        max_body = cfg.agent.text.max_body_chars
        max_title = cfg.agent.text.max_title_chars

        processed = 0
        skipped_seen = 0

        for issue in issues:
            if budget.remaining <= 0:
                break
            if processed >= per_repo_max:
                break

            # Dedup: repo + issue_number
            if store.has_issue(repo, issue.number):
                skipped_seen += 1
                continue

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

            title = clip_text(issue.title, max_title)
            body = clip_text(issue.body or "", max_body)

            result = run_issue_agent(
                cfg=cfg,
                repo=repo,
                title=title,
                body=body,
                issue_url=issue.html_url,
            )

            analysis_id = store.insert_issue_analysis(
                issue_row_id=issue_row_id,
                analysis=result.analysis,
                model_info=result.model_info,
            )

            card_cfg = cfg.notifications.feishu.message
            card = render_card_template_b(
                result.card_data,
                max_missing_items=card_cfg.completeness.max_missing_items,
            )

            try:
                resp = feishu.send_card(card)
                status = "sent"
                if isinstance(resp, dict) and resp.get("status") == "skipped":
                    status = "skipped"
                
                store.insert_notification(
                    issue_row_id=issue_row_id,
                    analysis_id=analysis_id,
                    channel="feishu",
                    status=status,
                    provider_response=resp if isinstance(resp, dict) else None,
                )
            except Exception as send_err:
                store.insert_notification(
                    issue_row_id=issue_row_id,
                    analysis_id=analysis_id,
                    channel="feishu",
                    status="failed",
                    error=str(send_err),
                )
                raise

            processed += 1
            budget.remaining -= 1

        # fixed f-string
        detail = f"new_issues_processed={processed}, skipped_seen={skipped_seen}, budget_remaining={budget.remaining}"
        if budget.remaining <= 0:
            detail += ", stopped_reason=global_budget_exhausted"
        elif processed >= per_repo_max:
            detail += ", stopped_reason=per_repo_limit_reached"

        store.log_run(repo, "success", detail=detail)
        return processed

    except Exception as e:
        store.log_run(repo, "failed", detail=str(e))
        return 0
