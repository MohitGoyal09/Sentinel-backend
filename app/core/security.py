import hashlib
import hmac
import logging
from cryptography.fernet import Fernet
from app.config import get_settings

logger = logging.getLogger("sentinel.security")

settings = get_settings()


class PrivacyEngine:
    @staticmethod
    def generate_key() -> str:
        """Generate a valid 32-byte base64 encoded Fernet key"""
        return Fernet.generate_key().decode()

    def __init__(self):
        raw_key = settings.encryption_key

        # Check if it looks like a valid Fernet key (44 chars ending in =)
        if len(raw_key) == 44 and raw_key.endswith("="):
            self.key = raw_key.encode()
        else:
            # Fallback: Hash the simple string to get 32 bytes, then base64 encode it
            # This ensures the app doesn't crash with a simple string password
            import base64

            self.key = base64.urlsafe_b64encode(
                hashlib.sha256(raw_key.encode()).digest()
            )

        try:
            self.cipher = Fernet(self.key)
        except Exception as e:
            raise RuntimeError(
                f"Failed to initialise encryption cipher. Check ENCRYPTION_KEY environment variable."
            ) from e

        self.salt = settings.vault_salt.encode()

    def hash_identity(self, email: str) -> str:
        return hmac.new(self.salt, email.lower().encode(), hashlib.sha256).hexdigest()[
            :32
        ]

    def encrypt(self, text: str) -> bytes:
        return self.cipher.encrypt(text.encode())

    def decrypt(self, encrypted: bytes) -> str:
        try:
            return self.cipher.decrypt(encrypted).decode()
        except Exception as e:
            logger.error(
                "Decryption failed (type=%s, data_len=%d): %s",
                type(e).__name__,
                len(encrypted) if encrypted else 0,
                e,
            )
            return "[decryption-failed]"


privacy = PrivacyEngine()
