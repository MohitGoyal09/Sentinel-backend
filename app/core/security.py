import base64
import hashlib
import hmac
import logging
import os
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

        try:
            # Try to use the key as-is (valid Fernet key)
            self.key = raw_key.encode() if isinstance(raw_key, str) else raw_key
            self.cipher = Fernet(self.key)
        except Exception:
            if os.getenv("ENVIRONMENT") == "production":
                raise ValueError(
                    "ENCRYPTION_KEY must be a valid Fernet key in production. "
                    "Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
                )
            logger.warning(
                "ENCRYPTION_KEY is not a valid Fernet key. Deriving key via SHA-256. "
                "This is insecure for production use."
            )
            self.key = base64.urlsafe_b64encode(
                hashlib.sha256(raw_key.encode()).digest()
            )
            self.cipher = Fernet(self.key)

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
