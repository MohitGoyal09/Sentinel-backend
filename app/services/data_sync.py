"""
Data Sync Service - pulls real behavioral data from connected tools via Composio.
"""
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.integrations.composio_client import composio_client
from app.services.connectors.git_connector import GitConnector
from app.services.connectors.slack_connector import SlackConnector
from app.core.security import privacy
from app.models.analytics import Event, GraphEdge
from app.models.identity import UserIdentity
from app.core.database import SessionLocal

logger = logging.getLogger("sentinel.data_sync")


class DataSyncService:
    """Pulls real behavioral data from connected tools via Composio and stores as Events."""

    def __init__(self, db: Session):
        self.db = db
        self._max_events_per_call = 100

    async def sync_github(self, entity_id: str, user_hash: str,
                          tenant_id: Optional[str] = None, days: int = 7) -> dict:
        """Pull GitHub commits via Composio and store as Events."""
        if not composio_client.is_available():
            return {"success": False, "error": "Composio not configured", "ingested": 0}

        ingested = 0
        errors = 0
        repos_scanned = 0

        try:
            # Step 1: Discover user's repos
            repos_result = await composio_client.execute_tool(
                "github", "list_repos", {"per_page": 10, "sort": "pushed"}, entity_id
            )

            repos = []
            if repos_result.get("success"):
                result_data = repos_result.get("result", {})
                # Handle various response shapes
                if isinstance(result_data, dict):
                    data = result_data.get("data", result_data)
                    if isinstance(data, list):
                        repos = data
                    elif isinstance(data, dict):
                        repos = data.get("items", data.get("repositories", []))
                elif isinstance(result_data, list):
                    repos = result_data

            if not repos:
                logger.warning("No repos found for entity %s", entity_id[:8])
                return {"success": True, "ingested": 0, "repos_scanned": 0, "errors": 0,
                        "message": "No GitHub repos found"}

            # Step 2: For each repo (max 5), fetch recent commits
            since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

            for repo_data in repos[:5]:
                try:
                    # Extract owner/repo from various response shapes
                    full_name = repo_data.get("full_name", "")
                    if not full_name:
                        full_name = f"{repo_data.get('owner', {}).get('login', '')}/{repo_data.get('name', '')}"
                    if "/" not in full_name:
                        continue

                    owner, repo = full_name.split("/", 1)
                    repos_scanned += 1

                    commits_result = await composio_client.execute_tool(
                        "github", "list_commits",
                        {"owner": owner, "repo": repo, "since": since, "per_page": self._max_events_per_call},
                        entity_id
                    )

                    if not commits_result.get("success"):
                        errors += 1
                        continue

                    commits = []
                    result_data = commits_result.get("result", {})
                    if isinstance(result_data, dict):
                        data = result_data.get("data", result_data)
                        commits = data if isinstance(data, list) else data.get("items", [])
                    elif isinstance(result_data, list):
                        commits = result_data

                    for commit in commits:
                        try:
                            # Extract commit data for parse_commit
                            commit_info = commit.get("commit", commit)
                            author = commit_info.get("author", {})

                            commit_data = {
                                "sha": commit.get("sha", ""),
                                "author_email": author.get("email", "unknown@unknown.com"),
                                "timestamp": author.get("date", datetime.now(timezone.utc).isoformat()),
                                "message": commit_info.get("message", ""),
                                "files_changed": commit.get("stats", {}).get("total", 0),
                                "additions": commit.get("stats", {}).get("additions", 0),
                                "deletions": commit.get("stats", {}).get("deletions", 0),
                            }

                            normalized = GitConnector.parse_commit(commit_data)

                            # Dedup check
                            source_id = commit_data["sha"]
                            existing = self.db.query(Event).filter(
                                Event.user_hash == user_hash,
                                Event.event_type == "commit",
                            ).filter(
                                Event.metadata_["source_id"].astext == source_id
                            ).first()

                            if existing:
                                continue

                            event = Event(
                                user_hash=user_hash,
                                tenant_id=tenant_id,
                                timestamp=normalized.timestamp,
                                event_type=normalized.event_type,
                                metadata_=normalized.metadata,
                            )
                            self.db.add(event)
                            ingested += 1

                        except Exception as e:
                            logger.debug("Failed to parse commit: %s", e)
                            errors += 1

                except Exception as e:
                    logger.warning("Failed to fetch commits for repo: %s", e)
                    errors += 1

            if ingested > 0:
                self.db.commit()

        except Exception as e:
            logger.error("sync_github failed: %s", e)
            self.db.rollback()
            return {"success": False, "error": str(e), "ingested": ingested, "errors": errors}

        return {
            "success": True,
            "source": "github",
            "ingested": ingested,
            "repos_scanned": repos_scanned,
            "errors": errors,
        }

    async def sync_slack(self, entity_id: str, user_hash: str,
                         tenant_id: Optional[str] = None, days: int = 7) -> dict:
        """Pull Slack messages via Composio and store as Events."""
        if not composio_client.is_available():
            return {"success": False, "error": "Composio not configured", "ingested": 0}

        ingested = 0
        errors = 0

        try:
            result = await composio_client.execute_tool(
                "slack", "search_messages",
                {"query": f"from:me after:{days}d", "count": self._max_events_per_call, "sort": "timestamp"},
                entity_id
            )

            if not result.get("success"):
                return {"success": False, "error": "Slack search failed", "ingested": 0}

            messages = []
            result_data = result.get("result", {})
            if isinstance(result_data, dict):
                data = result_data.get("data", result_data)
                if isinstance(data, dict):
                    messages = data.get("messages", {}).get("matches", [])
                elif isinstance(data, list):
                    messages = data

            for msg in messages:
                try:
                    msg_data = {
                        "user_email": "self",  # Will use the provided user_hash
                        "timestamp": datetime.fromtimestamp(
                            float(msg.get("ts", 0)), tz=timezone.utc
                        ).isoformat() if msg.get("ts") else datetime.now(timezone.utc).isoformat(),
                        "channel": msg.get("channel", {}).get("name", "unknown"),
                        "is_reply": bool(msg.get("thread_ts") and msg.get("thread_ts") != msg.get("ts")),
                        "reaction_count": len(msg.get("reactions", [])),
                        "mentions": msg.get("mentions", []),
                        "ts": msg.get("ts", ""),
                    }

                    normalized = SlackConnector.parse_message(msg_data)

                    # Dedup by source_id (Slack ts)
                    source_id = msg_data["ts"]
                    if source_id:
                        existing = self.db.query(Event).filter(
                            Event.user_hash == user_hash,
                            Event.event_type == "slack_message",
                        ).filter(
                            Event.metadata_["source_id"].astext == source_id
                        ).first()
                        if existing:
                            continue

                    event = Event(
                        user_hash=user_hash,
                        tenant_id=tenant_id,
                        timestamp=normalized.timestamp,
                        event_type=normalized.event_type,
                        metadata_=normalized.metadata,
                    )
                    self.db.add(event)
                    ingested += 1

                    # Create GraphEdge for replies (replier -> original poster)
                    if msg_data["is_reply"] and msg.get("previous_message", {}).get("user"):
                        try:
                            original_user = msg["previous_message"]["user"]
                            # We can't hash the original user without their email
                            # For now, use the Slack user ID as a proxy hash
                            target_hash = privacy.hash_identity(original_user)
                            edge = GraphEdge(
                                source_hash=user_hash,
                                target_hash=target_hash,
                                tenant_id=tenant_id,
                                weight=1.0,
                                last_interaction=normalized.timestamp,
                                edge_type="collaboration",
                            )
                            self.db.add(edge)
                        except Exception:
                            pass  # Graph edge creation is best-effort

                except Exception as e:
                    logger.debug("Failed to parse Slack message: %s", e)
                    errors += 1

            if ingested > 0:
                self.db.commit()

        except Exception as e:
            logger.error("sync_slack failed: %s", e)
            self.db.rollback()
            return {"success": False, "error": str(e), "ingested": ingested, "errors": errors}

        return {
            "success": True,
            "source": "slack",
            "ingested": ingested,
            "errors": errors,
        }

    async def sync_all_connected(self, entity_id: str, user_hash: str,
                                  tenant_id: Optional[str] = None) -> dict:
        """Sync all connected tools for a user."""
        connected = await composio_client.get_connected_integrations(entity_id)
        connected_lower = [s.lower() for s in connected]
        results = {}

        if "github" in connected_lower:
            results["github"] = await self.sync_github(entity_id, user_hash, tenant_id)
        if "slack" in connected_lower or "slackbot" in connected_lower:
            results["slack"] = await self.sync_slack(entity_id, user_hash, tenant_id)

        total_ingested = sum(r.get("ingested", 0) for r in results.values())
        return {
            "success": True,
            "sources": results,
            "total_ingested": total_ingested,
            "connected_tools": connected_lower,
        }


def background_sync(entity_id: str, user_hash: str, tenant_id: str):
    """Background task wrapper with own DB session."""
    try:
        with SessionLocal() as db:
            service = DataSyncService(db)
            asyncio.run(service.sync_all_connected(entity_id, user_hash, tenant_id))
        # Trigger engine recomputation with separate session
        from app.api.v1.endpoints.engines import run_all_engines
        run_all_engines(user_hash)
    except Exception as e:
        logger.error("Background sync failed for %s: %s", user_hash[:8], e)
