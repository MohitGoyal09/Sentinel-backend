"""
SSO Service — Unified OAuth 2.0 / OIDC provider abstraction.
Supports Google, Azure AD, and SAML (stub).
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger("sentinel.sso")


@dataclass
class SSOUserInfo:
    """Normalized user info from any SSO provider."""

    email: str
    name: str
    provider: str
    provider_user_id: str
    avatar_url: Optional[str] = None
    tenant_domain: Optional[str] = None  # e.g., company.com for workspace restriction
    groups: list[str] = None  # IdP groups (for role mapping)

    def __post_init__(self):
        if self.groups is None:
            self.groups = []


class SSOProvider(ABC):
    """Base class for SSO providers."""

    @abstractmethod
    def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        """Get the URL to redirect user to for authentication."""
        pass

    @abstractmethod
    async def exchange_code(
        self, code: str, redirect_uri: str
    ) -> Optional[SSOUserInfo]:
        """Exchange authorization code for user info."""
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        pass


class GoogleSSOProvider(SSOProvider):
    """Google OAuth 2.0 / OpenID Connect provider."""

    def __init__(
        self, client_id: str, client_secret: str, allowed_domains: list[str] = None
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.allowed_domains = allowed_domains or []

    def get_provider_name(self) -> str:
        return "google"

    def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        import urllib.parse

        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"

    async def exchange_code(
        self, code: str, redirect_uri: str
    ) -> Optional[SSOUserInfo]:
        import httpx

        # Exchange code for tokens
        token_response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )

        if token_response.status_code != 200:
            logger.error("Google token exchange failed: %s", token_response.text)
            return None

        tokens = token_response.json()
        access_token = tokens.get("access_token")

        # Get user info
        userinfo_response = httpx.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        if userinfo_response.status_code != 200:
            logger.error("Google userinfo fetch failed: %s", userinfo_response.text)
            return None

        user_data = userinfo_response.json()
        email = user_data.get("email", "")

        # Check domain restriction
        if self.allowed_domains:
            domain = email.split("@")[1] if "@" in email else ""
            if domain not in self.allowed_domains:
                logger.warning(
                    "Google login rejected: domain %s not in allowed list", domain
                )
                return None

        return SSOUserInfo(
            email=email,
            name=user_data.get("name", email),
            provider="google",
            provider_user_id=user_data.get("id", ""),
            avatar_url=user_data.get("picture"),
            tenant_domain=email.split("@")[1] if "@" in email else None,
        )


class AzureADSSOProvider(SSOProvider):
    """Azure AD (Microsoft Entra ID) OAuth 2.0 / OIDC provider."""

    def __init__(self, client_id: str, client_secret: str, tenant_id: str = "common"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id

    def get_provider_name(self) -> str:
        return "azure_ad"

    def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        import urllib.parse

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "response_mode": "query",
        }
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"

    async def exchange_code(
        self, code: str, redirect_uri: str
    ) -> Optional[SSOUserInfo]:
        import httpx

        token_response = httpx.post(
            f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )

        if token_response.status_code != 200:
            logger.error("Azure AD token exchange failed: %s", token_response.text)
            return None

        tokens = token_response.json()
        access_token = tokens.get("access_token")

        # Get user info from Microsoft Graph
        userinfo_response = httpx.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        if userinfo_response.status_code != 200:
            logger.error("Azure AD userinfo fetch failed: %s", userinfo_response.text)
            return None

        user_data = userinfo_response.json()

        return SSOUserInfo(
            email=user_data.get("mail") or user_data.get("userPrincipalName", ""),
            name=user_data.get("displayName", ""),
            provider="azure_ad",
            provider_user_id=user_data.get("id", ""),
            tenant_domain=(
                user_data.get("mail") or user_data.get("userPrincipalName", "")
            ).split("@")[1]
            if "@" in (user_data.get("mail") or user_data.get("userPrincipalName", ""))
            else None,
        )


class SAMLSSOProvider(SSOProvider):
    """SAML 2.0 provider (stub for hackathon demo)."""

    def __init__(self, entity_id: str = "", sso_url: str = "", certificate: str = ""):
        self.entity_id = entity_id
        self.sso_url = sso_url
        self.certificate = certificate

    def get_provider_name(self) -> str:
        return "saml"

    def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        # Stub: return a demo page URL
        return f"/sso/saml/setup?state={state}"

    async def exchange_code(
        self, code: str, redirect_uri: str
    ) -> Optional[SSOUserInfo]:
        # Stub: not implemented
        logger.warning("SAML SSO not yet implemented")
        return None


class SSOService:
    """Unified SSO service managing all providers."""

    def __init__(self):
        self._providers: dict[str, SSOProvider] = {}

    def register_provider(self, name: str, provider: SSOProvider):
        self._providers[name] = provider

    def get_provider(self, name: str) -> Optional[SSOProvider]:
        return self._providers.get(name)

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())

    def get_available_providers(self) -> list[dict]:
        """Return list of available providers for the login page."""
        return [
            {"name": name, "display_name": self._get_display_name(name)}
            for name in self._providers.keys()
        ]

    def _get_display_name(self, name: str) -> str:
        display_names = {
            "google": "Google",
            "azure_ad": "Microsoft",
            "saml": "SAML SSO",
            "okta": "Okta",
        }
        return display_names.get(name, name.title())


# Global instance
sso_service = SSOService()
