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
from app.services.connectors.gmail_connector import GmailConnector
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

                            # Create GraphEdge for PR reviews
                            if normalized.event_type in ("pr_review", "code_review"):
                                author_email = commit_data.get("author_email", "")
                                if author_email and author_email != "unknown@unknown.com":
                                    author_hash = privacy.hash_identity(author_email)
                                    if author_hash != user_hash:
                                        author_exists = self.db.query(UserIdentity).filter_by(
                                            user_hash=author_hash
                                        ).first()
                                        if author_exists:
                                            comment_len = normalized.metadata.get("comment_length", 100)
                                            weight = min(comment_len / 500, 1.0)
                                            existing_edge = self.db.query(GraphEdge).filter_by(
                                                source_hash=user_hash,
                                                target_hash=author_hash,
                                                edge_type="code_review",
                                            ).first()
                                            if existing_edge:
                                                existing_edge.weight = max(existing_edge.weight, weight)
                                                existing_edge.last_interaction = normalized.timestamp
                                            else:
                                                self.db.add(GraphEdge(
                                                    source_hash=user_hash,
                                                    target_hash=author_hash,
                                                    tenant_id=tenant_id,
                                                    weight=weight,
                                                    last_interaction=normalized.timestamp,
                                                    edge_type="code_review",
                                                ))

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

    async def sync_calendar(self, entity_id: str, user_hash: str,
                            tenant_id: Optional[str] = None, days: int = 14) -> dict:
        """Pull Google Calendar events via Composio and store as Events."""
        if not composio_client.is_available():
            return {"success": False, "error": "Composio not configured", "ingested": 0}

        ingested = 0
        errors = 0

        try:
            time_min = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            time_max = datetime.now(timezone.utc).isoformat()

            result = await composio_client.execute_tool(
                "googlecalendar", "list_events",
                {"timeMin": time_min, "timeMax": time_max, "maxResults": self._max_events_per_call},
                entity_id
            )

            if not result.get("success"):
                return {"success": False, "error": "Calendar fetch failed", "ingested": 0}

            events_data = []
            result_data = result.get("result", {})
            if isinstance(result_data, dict):
                data = result_data.get("data", result_data)
                if isinstance(data, dict):
                    events_data = data.get("items", [])
                elif isinstance(data, list):
                    events_data = data
            elif isinstance(result_data, list):
                events_data = result_data

            for cal_event in events_data:
                try:
                    start = cal_event.get("start", {})
                    start_str = start.get("dateTime", start.get("date", ""))
                    if not start_str:
                        continue

                    try:
                        event_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    except ValueError:
                        continue

                    end = cal_event.get("end", {})
                    end_str = end.get("dateTime", end.get("date", ""))
                    duration_minutes = 30
                    if end_str:
                        try:
                            end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                            duration_minutes = max(1, int((end_time - event_time).total_seconds() / 60))
                        except ValueError:
                            pass

                    hour = event_time.hour
                    after_hours = hour >= 18 or hour < 8
                    attendee_count = len(cal_event.get("attendees", []))
                    event_id = cal_event.get("id", "")

                    if event_id:
                        existing = self.db.query(Event).filter(
                            Event.user_hash == user_hash,
                            Event.event_type == "meeting",
                        ).filter(
                            Event.metadata_["source_id"].astext == event_id
                        ).first()
                        if existing:
                            continue

                    event = Event(
                        user_hash=user_hash,
                        tenant_id=tenant_id,
                        timestamp=event_time,
                        event_type="meeting",
                        metadata_={
                            "duration_minutes": duration_minutes,
                            "attendee_count": attendee_count,
                            "after_hours": after_hours,
                            "is_recurring": bool(cal_event.get("recurringEventId")),
                            "source": "google_calendar",
                            "source_id": event_id,
                        },
                    )
                    self.db.add(event)
                    ingested += 1

                except Exception as e:
                    logger.debug("Failed to parse calendar event: %s", e)
                    errors += 1

            if ingested > 0:
                self.db.commit()

        except Exception as e:
            logger.error("sync_calendar failed: %s", e)
            self.db.rollback()
            return {"success": False, "error": str(e), "ingested": ingested, "errors": errors}

        return {"success": True, "source": "calendar", "ingested": ingested, "errors": errors}

    async def sync_gmail(self, entity_id: str, user_hash: str,
                         tenant_id: Optional[str] = None, days: int = 7) -> dict:
        """Pull Gmail sent email metadata via Composio and store as Events."""
        if not composio_client.is_available():
            return {"success": False, "error": "Composio not configured", "ingested": 0}

        ingested = 0
        errors = 0

        try:
            after_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y/%m/%d")
            result = await composio_client.execute_tool(
                "gmail", "list_messages",
                {"q": f"from:me after:{after_date}", "maxResults": self._max_events_per_call},
                entity_id
            )

            if not result.get("success"):
                return {"success": False, "error": "Gmail fetch failed", "ingested": 0}

            messages = []
            result_data = result.get("result", {})
            if isinstance(result_data, dict):
                data = result_data.get("data", result_data)
                if isinstance(data, dict):
                    messages = data.get("messages", [])
                elif isinstance(data, list):
                    messages = data
            elif isinstance(result_data, list):
                messages = result_data

            for msg in messages:
                try:
                    headers = {}
                    payload = msg.get("payload", {})
                    for h in payload.get("headers", []):
                        headers[h.get("name", "").lower()] = h.get("value", "")

                    # Parse timestamp from internalDate (ms since epoch)
                    internal_date = msg.get("internalDate")
                    if internal_date:
                        event_time = datetime.fromtimestamp(
                            int(internal_date) / 1000, tz=timezone.utc
                        )
                    else:
                        event_time = datetime.now(timezone.utc)

                    # Count recipients from To + Cc (metadata only, not names)
                    to_field = headers.get("to", "")
                    cc_field = headers.get("cc", "")
                    recipient_count = len([r for r in (to_field + "," + cc_field).split(",") if r.strip()])

                    # Detect reply from In-Reply-To or References headers
                    is_reply = bool(headers.get("in-reply-to") or headers.get("references"))

                    hour = event_time.hour
                    after_hours = hour >= 18 or hour < 8
                    message_id = msg.get("id", "")

                    # Dedup
                    if message_id:
                        existing = self.db.query(Event).filter(
                            Event.user_hash == user_hash,
                            Event.event_type == "email_sent",
                        ).filter(
                            Event.metadata_["source_id"].astext == message_id
                        ).first()
                        if existing:
                            continue

                    email_data = {
                        "user_email": "self",
                        "timestamp": event_time.isoformat(),
                        "recipient_count": recipient_count,
                        "is_reply": is_reply,
                        "message_id": message_id,
                    }
                    normalized = GmailConnector.parse_email(email_data)

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
                    logger.debug("Failed to parse Gmail message: %s", e)
                    errors += 1

            if ingested > 0:
                self.db.commit()

        except Exception as e:
            logger.error("sync_gmail failed: %s", e)
            self.db.rollback()
            return {"success": False, "error": str(e), "ingested": ingested, "errors": errors}

        return {"success": True, "source": "gmail", "ingested": ingested, "errors": errors}

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
        if "googlecalendar" in connected_lower or "google_calendar" in connected_lower:
            results["calendar"] = await self.sync_calendar(entity_id, user_hash, tenant_id)
        if "gmail" in connected_lower or "google_mail" in connected_lower:
            results["gmail"] = await self.sync_gmail(entity_id, user_hash, tenant_id)

        total_ingested = sum(r.get("ingested", 0) for r in results.values())
        return {
            "success": True,
            "sources": results,
            "total_ingested": total_ingested,
            "connected_tools": connected_lower,
        }


def background_sync(entity_id: str, user_hash: str, tenant_id: str, source: str = "all"):
    """Background task wrapper with own DB session. Stores results in pipeline metrics."""
    from app.api.v1.endpoints.ingestion import _pipeline_metrics
    try:
        with SessionLocal() as db:
            service = DataSyncService(db)
            if source == "github":
                result = asyncio.run(service.sync_github(entity_id, user_hash, tenant_id))
                results = {"github": result}
            elif source == "slack":
                result = asyncio.run(service.sync_slack(entity_id, user_hash, tenant_id))
                results = {"slack": result}
            elif source == "calendar":
                result = asyncio.run(service.sync_calendar(entity_id, user_hash, tenant_id))
                results = {"calendar": result}
            elif source == "gmail":
                result = asyncio.run(service.sync_gmail(entity_id, user_hash, tenant_id))
                results = {"gmail": result}
            else:
                full = asyncio.run(service.sync_all_connected(entity_id, user_hash, tenant_id))
                results = full.get("sources", {})

        # Track ingested counts per source
        for src_name, src_result in results.items():
            count = src_result.get("ingested", 0)
            if count > 0:
                _pipeline_metrics["events_by_source"][src_name] = (
                    _pipeline_metrics["events_by_source"].get(src_name, 0) + count
                )
                _pipeline_metrics["total_ingested"] += count

        # Trigger engine recomputation
        from app.api.v1.endpoints.engines import run_all_engines
        run_all_engines(user_hash)

        # Record engine run result
        _pipeline_metrics["last_engine_run"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_hash": user_hash[:8],
            "sources_synced": {k: v.get("ingested", 0) for k, v in results.items()},
        }

    except Exception as e:
        logger.error("Background sync failed for %s: %s", user_hash[:8], e)
