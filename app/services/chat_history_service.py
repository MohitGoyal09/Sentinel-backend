"""
Chat History Service — persist and retrieve Ask Sentinel conversation turns.

All queries are strictly scoped to the calling user's ``user_hash``.
No cross-user data access is possible via this service.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.chat_history import ChatHistory, ChatSession


class ChatHistoryService:
    """Persist and retrieve chat turns for Ask Sentinel.

    All database access is synchronous (``Session``, ``db.query()``).
    Every query includes ``user_hash`` as a mandatory filter to enforce
    strict per-user data scoping.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def persist_turn(
        self,
        user_hash: str,
        tenant_id: str,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
        session_id: Optional[str] = None,
        turn_type: str = "message",
    ) -> ChatHistory:
        """Insert a single chat turn and flush it to the session.

        Args:
            user_hash:        Caller's anonymised identifier.
            tenant_id:        Caller's tenant.
            conversation_id:  UUID or stable ID for the conversation.
            role:             "user" or "assistant".
            content:          The full message text.
            metadata:         Optional arbitrary JSON metadata.
            session_id:       Optional ChatSession FK for session-based lookups.
            turn_type:        Turn type — "message", "tool_call", "connection_link", etc.

        Returns:
            The newly created ``ChatHistory`` ORM object.
        """
        effective_session_id = session_id or conversation_id
        turn = ChatHistory(
            user_hash=user_hash,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            session_id=effective_session_id,
            role=role,
            type=turn_type,
            content=content,
            created_at=datetime.now(timezone.utc),
            metadata_=metadata,
        )
        self.db.add(turn)
        self.db.flush()

        # Touch the parent session's updated_at so sidebar ordering stays
        # accurate (most-recently-active session surfaces first).
        if effective_session_id:
            parent_session = (
                self.db.query(ChatSession)
                .filter(ChatSession.id == effective_session_id)
                .first()
            )
            if parent_session:
                parent_session.updated_at = datetime.now(timezone.utc)
                self.db.flush()

        return turn

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_conversations(
        self,
        user_hash: str,
        tenant_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """Return a summary of recent conversations for *user_hash*.

        Each entry contains:
          - ``conversation_id``
          - ``title``     — first user message, truncated to 50 chars
          - ``preview``   — first assistant message, truncated to 100 chars
          - ``updated_at`` — timestamp of the most recent turn
          - ``turn_count`` — total number of turns in the conversation

        Results are ordered by most-recently-updated conversation first.

        Args:
            user_hash:  Caller's anonymised identifier.
            tenant_id:  Caller's tenant.
            limit:      Maximum number of conversations to return.
        """
        # Two-step approach to avoid loading all turns into memory:
        # 1. SQL GROUP BY to get conversation aggregates (count, max timestamp)
        # 2. Per-conversation query for first few turns (title + preview)
        from sqlalchemy import func as sa_func

        # Step 1: aggregate query — bounded by limit
        conv_aggs = (
            self.db.query(
                ChatHistory.conversation_id,
                sa_func.count().label("turn_count"),
                sa_func.max(ChatHistory.created_at).label("updated_at"),
            )
            .filter(
                ChatHistory.user_hash == user_hash,
                ChatHistory.tenant_id == tenant_id,
            )
            .group_by(ChatHistory.conversation_id)
            .order_by(sa_func.max(ChatHistory.created_at).desc())
            .limit(limit)
            .all()
        )

        # Step 2: for each conversation, fetch only the first few turns
        # to extract title (first user msg) and preview (first assistant msg).
        # Total rows loaded: limit * 4 (e.g. 80 rows for limit=20).
        summaries: list[dict] = []
        for conv_id, turn_count, updated_at in conv_aggs:
            first_turns = (
                self.db.query(ChatHistory.role, ChatHistory.content)
                .filter(
                    ChatHistory.user_hash == user_hash,
                    ChatHistory.tenant_id == tenant_id,
                    ChatHistory.conversation_id == conv_id,
                )
                .order_by(ChatHistory.created_at.asc())
                .limit(4)
                .all()
            )
            first_user_msg = next(
                (content for role, content in first_turns if role == "user"), ""
            )
            first_assistant_msg = next(
                (content for role, content in first_turns if role == "assistant"), ""
            )
            summaries.append(
                {
                    "conversation_id": conv_id,
                    "title": first_user_msg[:50],
                    "preview": first_assistant_msg[:100],
                    "updated_at": updated_at,
                    "turn_count": turn_count,
                }
            )

        return summaries

    def get_conversation_turns(
        self,
        user_hash: str,
        tenant_id: str,
        conversation_id: str,
    ) -> list[ChatHistory]:
        """Return all turns for *conversation_id*, scoped to *user_hash* and *tenant_id*.

        Results are ordered chronologically (oldest first) so callers can
        feed them directly into an LLM context window.

        Args:
            user_hash:        Caller's anonymised identifier (enforces scoping).
            tenant_id:        Caller's tenant (prevents cross-tenant access).
            conversation_id:  The conversation to retrieve.
        """
        return (
            self.db.query(ChatHistory)
            .filter(
                ChatHistory.user_hash == user_hash,
                ChatHistory.tenant_id == tenant_id,
                ChatHistory.conversation_id == conversation_id,
            )
            .order_by(ChatHistory.created_at.asc())
            .all()
        )

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def create_session(
        self,
        user_hash: str,
        tenant_id: str,
        title: str = "Untitled Chat",
    ) -> ChatSession:
        """Create a new chat session for *user_hash* within *tenant_id*.

        Args:
            user_hash:  Caller's anonymised identifier.
            tenant_id:  Caller's tenant.
            title:      Display name for the session (default: "Untitled Chat").

        Returns:
            The newly created and flushed ``ChatSession`` ORM object.
        """
        session = ChatSession(
            user_hash=user_hash,
            tenant_id=tenant_id,
            title=title,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.db.add(session)
        self.db.flush()
        return session

    def get_sessions(
        self,
        user_hash: str,
        tenant_id: str,
        limit: int = 20,
        offset: int = 0,
        search: Optional[str] = None,
    ) -> list[ChatSession]:
        """Return paginated active sessions for *user_hash* within *tenant_id*.

        Results are ordered by ``updated_at DESC`` so the most recently used
        session appears first, matching the KaraX sidebar ordering.

        Args:
            user_hash:  Caller's anonymised identifier.
            tenant_id:  Caller's tenant.
            limit:      Maximum number of sessions to return.
            offset:     Number of sessions to skip (for pagination).
            search:     Optional case-insensitive substring to filter by title.

        Returns:
            List of active ``ChatSession`` objects.
        """
        query = self.db.query(ChatSession).filter(
            ChatSession.user_hash == user_hash,
            ChatSession.tenant_id == tenant_id,
            ChatSession.is_active.is_(True),
        )

        if search:
            query = query.filter(ChatSession.title.ilike(f"%{search}%"))

        return (
            query.order_by(ChatSession.updated_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def get_session(
        self,
        user_hash: str,
        tenant_id: str,
        session_id: str,
    ) -> Optional[ChatSession]:
        """Return a single active session by ID, scoped to *user_hash* and *tenant_id*.

        Args:
            user_hash:  Caller's anonymised identifier.
            tenant_id:  Caller's tenant.
            session_id: Primary key of the target ``ChatSession``.

        Returns:
            The ``ChatSession`` if found and active, otherwise ``None``.
        """
        return (
            self.db.query(ChatSession)
            .filter(
                ChatSession.user_hash == user_hash,
                ChatSession.tenant_id == tenant_id,
                ChatSession.id == session_id,
                ChatSession.is_active.is_(True),
            )
            .first()
        )

    def rename_session(
        self,
        user_hash: str,
        tenant_id: str,
        session_id: str,
        title: str,
    ) -> Optional[ChatSession]:
        """Rename a session.  Only the session owner within the same tenant may rename.

        Args:
            user_hash:  Caller's anonymised identifier.
            tenant_id:  Caller's tenant.
            session_id: Primary key of the target ``ChatSession``.
            title:      New display name (caller is responsible for length validation).

        Returns:
            The updated ``ChatSession`` if found, otherwise ``None``.
        """
        session = self.get_session(user_hash, tenant_id, session_id)
        if session is None:
            return None

        session.title = title
        session.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return session

    def delete_session(
        self,
        user_hash: str,
        tenant_id: str,
        session_id: str,
    ) -> bool:
        """Soft-delete a session by setting ``is_active=False``.

        The underlying ``ChatHistory`` rows are preserved for audit/recovery.

        Args:
            user_hash:  Caller's anonymised identifier.
            tenant_id:  Caller's tenant.
            session_id: Primary key of the target ``ChatSession``.

        Returns:
            ``True`` if the session was found and deactivated, ``False`` otherwise.
        """
        session = self.get_session(user_hash, tenant_id, session_id)
        if session is None:
            return False

        session.is_active = False
        session.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return True

    def toggle_favorite(
        self,
        user_hash: str,
        tenant_id: str,
        session_id: str,
    ) -> Optional[ChatSession]:
        """Toggle the ``is_favorite`` flag on a session.

        Pinned sessions should be surfaced at the top of the sidebar by
        the calling endpoint; this method only persists the state change.

        Args:
            user_hash:  Caller's anonymised identifier.
            tenant_id:  Caller's tenant.
            session_id: Primary key of the target ``ChatSession``.

        Returns:
            The updated ``ChatSession`` if found, otherwise ``None``.
        """
        session = self.get_session(user_hash, tenant_id, session_id)
        if session is None:
            return None

        session.is_favorite = not session.is_favorite
        session.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return session

    def auto_title_session(
        self,
        user_hash: str,
        tenant_id: str,
        session_id: str,
        first_message: str,
    ) -> Optional[ChatSession]:
        """Generate a concise title for the session based on the first user message.

        Uses the LLM to create a 5-7 word title.  Falls back to truncating
        the first message if the LLM is unavailable or returns an empty result.

        The method is a no-op when the session already has a custom title
        (i.e. its title is not the default ``"Untitled Chat"``).

        Args:
            user_hash:     Caller's anonymised identifier.
            tenant_id:     Caller's tenant.
            session_id:    Primary key of the target ``ChatSession``.
            first_message: The first user message in the session, used as the
                           prompt context for title generation.

        Returns:
            The updated ``ChatSession`` if found, otherwise ``None``.
        """
        session = self.get_session(user_hash, tenant_id, session_id)
        if session is None or session.title != "Untitled Chat":
            return session  # Already titled or not found

        # Try LLM-based title generation; fall back to truncation on any error.
        from app.services.llm import llm_service

        try:
            raw_title = llm_service.generate_chat_response([
                {
                    "role": "system",
                    "content": (
                        "Generate a concise 5-7 word title for this conversation. "
                        "Return ONLY the title, no quotes, no explanation."
                    ),
                },
                {"role": "user", "content": first_message[:500]},
            ])
            title = raw_title.strip().strip('"').strip("'")[:100]
            if len(title) < 3:
                title = first_message[:50].strip()
        except Exception:
            title = first_message[:50].strip()

        session.title = title
        session.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return session
