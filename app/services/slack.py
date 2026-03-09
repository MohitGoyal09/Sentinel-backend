import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from app.config import get_settings
import asyncio
from typing import Optional

logger = logging.getLogger("sentinel.slack")

settings = get_settings()

class SlackService:
    """Handles actual Slack communication for nudges"""
    
    def __init__(self):
        self.client = WebClient(token=settings.slack_bot_token) if settings.slack_bot_token else None
    
    async def send_nudge(self, email: str, message: str, risk_level: str) -> bool:
        """
        Send DM to employee based on risk level.
        Returns True if sent successfully.
        """
        if not self.client:
            logger.info("[MOCK SLACK] To %s: %s...", email, message[:50])
            return True
        
        try:
            # Lookup user by email
            response = self.client.users_lookupByEmail(email=email)
            user_id = response["user"]["id"]
            
            # Format message based on risk level
            emoji = "🟢" if risk_level == "LOW" else "🟡" if risk_level == "ELEVATED" else "🔴"
            formatted_message = f"{emoji} *Sentinel Insight*\n\n{message}\n\n_This is an automated wellness check based on your work patterns. Reply STOP to disable._"
            
            # Send DM
            # Using loop.run_in_executor for async wrapper around sync slack client calls
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.client.chat_postMessage(
                    channel=user_id,
                    text=formatted_message,
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": formatted_message
                            }
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "Schedule Break"},
                                    "action_id": "schedule_break",
                                    "style": "primary"
                                },
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "Dismiss"},
                                    "action_id": "dismiss_nudge"
                                }
                            ]
                        }
                    ]
                )
            )
            
            return True
            
        except SlackApiError as e:
            logger.error("Slack API Error: %s", e.response['error'])
            return False
    
    async def send_manager_alert(self, manager_email: str, anonymous_id: str, team_risk: str):
        """Send aggregated alert to manager (no individual names)"""
        if not self.client:
            logger.info("[MOCK SLACK] To Manager %s: Team member %s showing %s", manager_email, anonymous_id, team_risk)
            return True
        
        try:
            response = self.client.users_lookupByEmail(email=manager_email)
            manager_id = response["user"]["id"]
            
            message = (
                f"📊 *Team Health Alert*\n\n"
                f"Team member `{anonymous_id}` is showing {team_risk} intensity patterns.\n"
                f"Consider scheduling a casual 1:1 or team retrospective.\n\n"
                f"_Individual identity protected. Employee has been notified directly._"
            )
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.client.chat_postMessage(
                    channel=manager_id,
                    text=message
                )
            )
            return True
            
        except SlackApiError as e:
            logger.error("Slack Manager Alert Error: %s", e)
            return False

slack_service = SlackService()
