"""
Tool-Augmented LLM Service

Enhances the standard LLM service with external tool integration via Composio.
Detects when user queries need real-time data from external sources and
automatically fetches it to provide enriched responses.
"""

import logging
import re
from typing import Dict, Any, Optional, List
from app.services.llm import llm_service
from app.integrations.composio_client import composio_client

logger = logging.getLogger("sentinel.tool_augmented_llm")


class ToolAugmentedLLM:
    """
    LLM service enhanced with external tool capabilities

    Workflow:
    1. Detect if query needs external data (calendar, Slack, etc.)
    2. Execute appropriate tool via Composio
    3. Inject tool results into LLM context
    4. Generate enhanced response with real-time data
    """

    # Query patterns that indicate need for external tools
    TOOL_PATTERNS = {
        "calendar": [
            r"how many meetings",
            r"meeting (load|schedule|calendar)",
            r"calendar",
            r"too many meetings",
            r"back.to.back meetings",
            r"meeting hours",
            r"(check|show|view|see|what).*(schedule|events?|appointments?)",
            r"(schedule|book|create|cancel|reschedule).*(meeting|event|appointment|call)",
            r"(free|available|open) (time|slots?)",
            r"(next|upcoming|today).*(meeting|event|call)",
        ],
        "slack": [
            r"slack (messages|activity)",
            r"communication (load|patterns)",
            r"messages sent",
            r"(check|read|show|view|see).*(slack|channels?|DMs?)",
            r"(send|post|write|message).*(slack|channel)",
            r"any new.*(slack|messages)",
        ],
        "github": [
            r"commits",
            r"pull requests?",
            r"code (activity|contributions)",
            r"(check|show|view|see|list|review).*(PRs?|pull requests?|issues?|repos?)",
            r"(create|open|submit|close|merge).*(PR|pull request|issue|branch)",
            r"(my|open|pending) (PRs?|pull requests?|issues?)",
        ],
        "email": [
            r"(check|read|show|get|open|view|see|look at|any new).*(email|emails|mail|inbox)",
            r"(send|compose|write|draft|reply|forward).*(email|mail|message)",
            r"\binbox\b",
            r"(unread|new) (email|mail|message)s?",
        ],
    }

    @staticmethod
    def detect_tool_need(query: str) -> Optional[str]:
        """
        Detect if query requires external tool data

        Args:
            query: User's question

        Returns:
            Tool name if detected, None otherwise
        """
        query_lower = query.lower()

        for tool, patterns in ToolAugmentedLLM.TOOL_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, query_lower):
                    logger.info(f"Detected {tool} tool need for query: {query[:50]}")
                    return tool

        return None

    @staticmethod
    def extract_user_reference(query: str, context: Dict[str, Any]) -> Optional[str]:
        """
        Extract which user is being asked about

        Checks for:
        - User names mentioned in query
        - Contextual user from conversation
        - Current user (default)

        Returns:
            user_hash or entity_id
        """
        # For now, use current user from context
        # In production, implement NER to extract names and map to user_hash
        return context.get("user_hash") or context.get("current_user_hash")

    @staticmethod
    async def fetch_calendar_context(entity_id: str, days: int = 7) -> Dict[str, Any]:
        """Fetch calendar data for context enrichment"""
        if not composio_client.is_available():
            return {
                "available": False,
                "note": "Calendar integration not configured",
            }

        analysis = await composio_client.analyze_meeting_load(entity_id, days=days)

        if not analysis.get("success"):
            return {"available": False, "error": analysis.get("error")}

        # Format for LLM consumption
        metrics = analysis.get("metrics", {})
        risk = analysis.get("risk_assessment", {})
        baseline = analysis.get("comparison_to_baseline", {})

        return {
            "available": True,
            "tool": "calendar",
            "data": {
                "total_meetings": metrics.get("total_meetings"),
                "total_hours": metrics.get("total_hours"),
                "average_hours_per_day": metrics.get("average_hours_per_day"),
                "back_to_back_count": metrics.get("back_to_back_count"),
                "risk_level": risk.get("level"),
                "risk_factors": risk.get("factors", []),
                "percentage_above_baseline": baseline.get("percentage_above_baseline"),
            },
            "summary": (
                f"User has {metrics.get('total_meetings')} meetings totaling "
                f"{metrics.get('total_hours')}h this week "
                f"({metrics.get('average_hours_per_day')}h/day average). "
                f"Risk level: {risk.get('level')}."
            ),
        }

    @staticmethod
    async def fetch_slack_context(entity_id: str, days: int = 7) -> Dict[str, Any]:
        """Fetch Slack activity for context enrichment"""
        if not composio_client.is_available():
            return {"available": False}

        result = await composio_client.get_slack_activity(entity_id, days=days)

        if not result.get("success"):
            return {"available": False, "error": result.get("error")}

        return {
            "available": True,
            "tool": "slack",
            "data": {
                "total_messages": result.get("total_messages"),
                "average_per_day": result.get("average_per_day"),
                "period_days": days,
            },
            "summary": (
                f"User sent {result.get('total_messages')} Slack messages "
                f"in the last {days} days ({result.get('average_per_day'):.1f}/day average)."
            ),
        }

    @staticmethod
    async def fetch_email_context(entity_id: str) -> Dict[str, Any]:
        """Fetch email/inbox data for context enrichment."""
        if not composio_client.is_available():
            return {
                "available": False,
                "note": "Email integration not configured",
            }

        try:
            result = await composio_client.execute_tool(
                tool="email",
                action="list_inbox",
                params={"maxResults": 10, "labelIds": ["INBOX"], "q": "is:unread"},
                entity_id=entity_id,
            )

            if not result.get("success"):
                return {"available": False, "error": result.get("error")}

            messages = result.get("result", {}).get("data", {}).get("messages", [])
            count = len(messages)

            return {
                "available": True,
                "tool": "email",
                "data": {
                    "unread_count": count,
                    "messages": messages[:5],  # First 5 for summary
                },
                "summary": (
                    f"User has {count} unread email(s) in their inbox."
                ),
            }
        except Exception as e:
            logger.error(f"Email context fetch failed for {entity_id}: {e}")
            return {"available": False, "error": str(e)}

    @staticmethod
    async def augment_context_with_tools(
        query: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Detect tool need and fetch relevant data

        Args:
            query: User's question
            context: Existing context dict

        Returns:
            Enhanced context with tool data
        """
        # Detect if external tools are needed
        tool_needed = ToolAugmentedLLM.detect_tool_need(query)

        if not tool_needed:
            return context

        # Extract which user is being asked about
        entity_id = ToolAugmentedLLM.extract_user_reference(query, context)

        if not entity_id:
            logger.warning("Could not determine user for tool query")
            return context

        # Fetch tool data
        tool_data = None

        if tool_needed == "calendar":
            tool_data = await ToolAugmentedLLM.fetch_calendar_context(entity_id)
        elif tool_needed == "slack":
            tool_data = await ToolAugmentedLLM.fetch_slack_context(entity_id)
        elif tool_needed == "email":
            tool_data = await ToolAugmentedLLM.fetch_email_context(entity_id)

        # Inject into context
        if tool_data and tool_data.get("available"):
            context["external_tool_data"] = tool_data
            context["tool_enriched"] = True

            logger.info(
                f"Context enriched with {tool_needed} data: {tool_data.get('summary')}"
            )
        else:
            context["tool_enriched"] = False
            logger.warning(f"Tool {tool_needed} data not available")

        return context

    @staticmethod
    def format_tool_data_for_llm(context: Dict[str, Any]) -> str:
        """
        Format tool data for injection into LLM prompt

        Returns:
            Formatted string to append to system prompt
        """
        if not context.get("tool_enriched"):
            return ""

        tool_data = context.get("external_tool_data", {})

        if not tool_data.get("available"):
            return ""

        tool_name = tool_data.get("tool", "external_tool")
        summary = tool_data.get("summary", "")
        data = tool_data.get("data", {})

        # Format data as structured text
        formatted = f"\n\n=== REAL-TIME {tool_name.upper()} DATA ===\n"
        formatted += f"{summary}\n\n"

        if tool_name == "calendar" and data:
            formatted += "Meeting Load Analysis:\n"
            formatted += f"- Total meetings: {data.get('total_meetings')}\n"
            formatted += f"- Total hours: {data.get('total_hours')}h\n"
            formatted += f"- Average per day: {data.get('average_hours_per_day')}h\n"
            formatted += f"- Back-to-back meetings: {data.get('back_to_back_count')}\n"
            formatted += f"- Risk level: {data.get('risk_level')}\n"

            if data.get("risk_factors"):
                formatted += "\nRisk Factors:\n"
                for factor in data.get("risk_factors", []):
                    formatted += f"- {factor}\n"

        elif tool_name == "slack" and data:
            formatted += "Communication Activity:\n"
            formatted += f"- Total messages: {data.get('total_messages')}\n"
            formatted += f"- Average per day: {data.get('average_per_day'):.1f}\n"

        elif tool_name == "email" and data:
            formatted += "Email Inbox:\n"
            formatted += f"- Unread emails: {data.get('unread_count', 0)}\n"

        formatted += "=== END TOOL DATA ===\n"

        return formatted

    @staticmethod
    async def generate_augmented_response(
        query: str,
        context: Dict[str, Any],
        system_prompt: str,
        conversation_history: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """
        Generate LLM response augmented with external tool data

        Args:
            query: User's question
            context: Conversation context
            system_prompt: Base system prompt
            conversation_history: Previous messages

        Returns:
            Dict with response and metadata about tools used
        """
        # Step 1: Augment context with tool data if needed
        enriched_context = await ToolAugmentedLLM.augment_context_with_tools(
            query, context
        )

        # Step 2: Format tool data for LLM
        tool_context_str = ToolAugmentedLLM.format_tool_data_for_llm(enriched_context)

        # Step 3: Build enhanced system prompt
        enhanced_system_prompt = system_prompt + tool_context_str

        # Step 4: Build message list
        messages = [{"role": "system", "content": enhanced_system_prompt}]

        if conversation_history:
            messages.extend(conversation_history[-10:])  # Last 10 for context window

        messages.append({"role": "user", "content": query})

        # Step 5: Generate response
        try:
            llm_response = llm_service.generate_chat_response(messages)

            return {
                "success": True,
                "response": llm_response,
                "tool_used": enriched_context.get("tool_enriched", False),
                "tool_type": (
                    enriched_context.get("external_tool_data", {}).get("tool")
                    if enriched_context.get("tool_enriched")
                    else None
                ),
                "tool_data": (
                    enriched_context.get("external_tool_data")
                    if enriched_context.get("tool_enriched")
                    else None
                ),
            }

        except Exception as e:
            logger.error(f"Augmented LLM generation failed: {e}")
            # Fallback to standard response without tool data
            messages = [{"role": "system", "content": system_prompt}]
            messages.append({"role": "user", "content": query})

            llm_response = llm_service.generate_chat_response(messages)

            return {
                "success": True,
                "response": llm_response,
                "tool_used": False,
                "error": "Tool augmentation failed, using standard response",
            }


# Global instance
tool_augmented_llm = ToolAugmentedLLM()
