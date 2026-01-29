import httpx
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from sqlalchemy.orm import Session
from app.config import get_settings
from app.models.analytics import Event

settings = get_settings()

class ContextEnricher:
    """
    Checks if late-night work is 'explained' by legitimate context.
    Prevents false positives in burnout detection.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.pagerduty_key = settings.pagerduty_api_key  # Add to config
        self.jira_token = settings.jira_api_key          # Add to config
        self.google_token = settings.google_calendar_key # Add to config
    
    async def is_explained(self, user_email: str, timestamp: datetime) -> Dict:
        """
        Check multiple context sources to explain late-night work.
        Returns: {
            "is_explained": bool,
            "explanation_type": str,  # 'on_call', 'sprint_end', 'pto', 'timezone', None
            "confidence": float       # 0.0-1.0 how sure we are
        }
        """
        checks = []
        
        # 1. Check PagerDuty (On-call rotation)
        try:
            on_call = await self._check_pagerduty(user_email, timestamp)
            if on_call["is_on_call"]:
                return {
                    "is_explained": True,
                    "explanation_type": "on_call",
                    "confidence": on_call["confidence"],
                    "details": f"On-call rotation: {on_call.get('schedule', 'Night shift')}"
                }
        except Exception as e:
            # Fail open: if API down, assume not explained (safer to flag)
            print(f"PagerDuty API error: {e}")
            checks.append(("on_call", False, 0.0))
        
        # 2. Check Jira (Sprint deadline within 48h)
        try:
            sprint_end = await self._check_sprint_deadline(user_email, timestamp)
            if sprint_end["is_sprint_end"]:
                return {
                    "is_explained": True,
                    "explanation_type": "sprint_end",
                    "confidence": 0.9,
                    "details": f"Sprint ends in {sprint_end.get('hours_remaining', 24)}h"
                }
        except Exception as e:
            print(f"Jira API error: {e}")
        
        # 3. Check Google Calendar (OOO/Focus time/Meetings)
        try:
            cal_status = await self._check_calendar(user_email, timestamp)
            if cal_status["is_pto"]:
                return {
                    "is_explained": True,
                    "explanation_type": "pto",
                    "confidence": 0.95,
                    "details": "PTO or OOO scheduled"
                }
        except Exception as e:
            print(f"Calendar API error: {e}")
        
        # 4. Check timezone (working late in PST vs EST)
        timezone_explained = self._check_timezone_bias(user_email, timestamp)
        if timezone_explained["is_explained"]:
            return {
                "is_explained": True,
                "explanation_type": "timezone",
                "confidence": 0.8,
                "details": timezone_explained["details"]
            }
        
        # 5. Check if it's a known "crunch period" (Black Friday, Tax season)
        seasonal = self._check_seasonal_crunch(timestamp)
        if seasonal["is_crunch"]:
            return {
                "is_explained": True,
                "explanation_type": "seasonal_crunch",
                "confidence": 0.7,
                "details": seasonal["details"]
            }
        
        # Default: Not explained (potential burnout signal)
        return {
            "is_explained": False,
            "explanation_type": None,
            "confidence": 1.0,
            "details": "No contextual explanation found"
        }
    
    async def _check_pagerduty(self, email: str, timestamp: datetime) -> Dict:
        """Query PagerDuty On-Call API"""
        if not self.pagerduty_key or settings.simulation_mode:
            # Mock for demo: 10% of late nights are on-call
            import random
            is_mock_oncall = random.random() < 0.1 and timestamp.hour > 20
            return {
                "is_on_call": is_mock_oncall,
                "confidence": 0.9,
                "schedule": "Backend Rotation" if is_mock_oncall else None
            }
        
        # Real PagerDuty API
        url = f"https://api.pagerduty.com/oncalls"
        headers = {
            "Authorization": f"Bearer {self.pagerduty_key}",
            "Content-Type": "application/json"
        }
        params = {
            "user_ids[]": email,  # PD uses email or user ID
            "since": timestamp.isoformat(),
            "until": (timestamp + timedelta(hours=1)).isoformat()
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, params=params)
            data = resp.json()
            on_calls = data.get("oncalls", [])
            
            return {
                "is_on_call": len(on_calls) > 0,
                "confidence": 0.95,
                "schedule": on_calls[0]["schedule"]["summary"] if on_calls else None
            }
    
    async def _check_sprint_deadline(self, email: str, timestamp: datetime) -> Dict:
        """Query Jira for sprint end date"""
        if not self.jira_token or settings.simulation_mode:
            # Mock: Check if it's a Friday night (common sprint end)
            is_friday = timestamp.weekday() == 4
            is_near_end = timestamp.hour > 18 and is_friday
            return {
                "is_sprint_end": is_near_end,
                "hours_remaining": 24 if is_near_end else 0
            }
        
        # Real Jira API - find active sprint for user
        url = f"https://your-domain.atlassian.net/rest/agile/1.0/board/123/sprint"
        headers = {"Authorization": f"Bearer {self.jira_token}"}
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers)
            sprints = resp.json().get("values", [])
            
            for sprint in sprints:
                if sprint.get("state") == "active":
                    end_date = datetime.fromisoformat(sprint["endDate"].replace("Z", "+00:00"))
                    hours_until = (end_date - timestamp).total_seconds() / 3600
                    
                    if 0 < hours_until < 48:  # Within 48h of sprint end
                        return {
                            "is_sprint_end": True,
                            "hours_remaining": hours_until,
                            "sprint_name": sprint["name"]
                        }
            
            return {"is_sprint_end": False, "hours_remaining": 0}
    
    async def _check_calendar(self, email: str, timestamp: datetime) -> Dict:
        """Check Google Calendar for OOO/PTO events"""
        if not self.google_token or settings.simulation_mode:
            return {"is_pto": False}  # Assume working
        
        # Google Calendar API would go here
        # For now, return False (assume no PTO during late night work)
        return {"is_pto": False}
    
    def _check_timezone_bias(self, email: str, timestamp: datetime) -> Dict:
        """
        Check if user is working late in their local timezone vs HQ timezone.
        Example: User in IST (India) working "late" at 10 PM IST = 
        Actually 9 AM PST (normal HQ hours) - explained.
        """
        # In production, lookup user timezone from Slack profile or directory
        # For demo, assume 5% of users are in different timezones with offset hours
        import random
        
        if random.random() < 0.05:  # 5% timezone offset cases
            local_hour = (timestamp.hour + 12) % 24  # Mock 12h offset
            if 9 <= local_hour <= 17:  # Normal hours in their timezone
                return {
                    "is_explained": True,
                    "details": f"User timezone offset: Working {timestamp.hour}:00 local is business hours in their zone"
                }
        
        return {"is_explained": False, "details": None}
    
    def _check_seasonal_crunch(self, timestamp: datetime) -> Dict:
        """Check for known industry crunch periods"""
        month = timestamp.month
        
        # Black Friday prep (Oct-Nov)
        if month in [10, 11] and timestamp.weekday() >= 4:  # Fri/Sat/Sun
            return {
                "is_crunch": True,
                "details": "Black Friday/Holiday prep period"
            }
        
        # Tax season (Mar-Apr) for fintech
        if month in [3, 4]:
            return {
                "is_crunch": True,
                "details": "Tax season peak"
            }
        
        # End of quarter (Mar, Jun, Sep, Dec)
        if timestamp.day >= 25 and ((month % 3) == 0):
            return {
                "is_crunch": True,
                "details": "End of quarter push"
            }
        
        return {"is_crunch": False, "details": None}
    
    def mark_events_explained(self, events: List[Event], user_email: str) -> List[Event]:
        """
        Batch process events to mark explained late nights.
        Called by SafetyValve before calculating velocity.
        """
        # This runs synchronously for speed (no API calls for batch)
        # In production, cache context checks for 1 hour
        for event in events:
            if event.metadata_ and event.metadata_.get("after_hours"):
                # Check if it's a weekend (often explained for on-call)
                if event.timestamp.weekday() >= 5:  # Saturday/Sunday
                    # 30% chance it's explained (on-call rotation)
                    import random
                    if random.random() < 0.3:
                        if event.metadata_ is None: event.metadata_ = {}
                        event.metadata_["explained"] = True
                        event.metadata_["explanation_type"] = "weekend_on_call"
                    else:
                        if event.metadata_ is None: event.metadata_ = {}
                        event.metadata_["explained"] = False
                else:
                    # Weekday late night - default unexplained unless proven otherwise
                    if event.metadata_ is None: event.metadata_ = {}
                    event.metadata_["explained"] = False
        
        return events