"""Dual-path OTP interlock: Sparrow SMS + email, 3-minute verification window."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from enum import StrEnum


class OTPChannel(StrEnum):
    SMS = "sparrow_sms"
    EMAIL = "email"


class OTPStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    EXPIRED = "expired"
    FAILED = "failed"


VERIFICATION_WINDOW_SECONDS: int = 180  # 3 minutes


@dataclass
class ChannelOTP:
    channel: OTPChannel
    code: str
    status: OTPStatus = OTPStatus.PENDING
    sent_at: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.sent_at) > VERIFICATION_WINDOW_SECONDS


@dataclass
class OTPChallenge:
    transaction_id: str
    user_id: str
    sms: ChannelOTP
    email: ChannelOTP
    created_at: float = field(default_factory=time.time)

    @property
    def both_verified(self) -> bool:
        return self.sms.status == OTPStatus.VERIFIED and self.email.status == OTPStatus.VERIFIED

    @property
    def any_expired(self) -> bool:
        return self.sms.is_expired or self.email.is_expired

    @property
    def should_auto_block(self) -> bool:
        if self.both_verified:
            return False
        if self.any_expired:
            return True
        if self.sms.status == OTPStatus.FAILED or self.email.status == OTPStatus.FAILED:
            return True
        return False


def _generate_code(length: int = 6) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


class OTPInterlock:
    """In-memory OTP store with mock Sparrow SMS and email dispatch."""

    def __init__(self) -> None:
        self._challenges: dict[str, OTPChallenge] = {}
        self._lock = asyncio.Lock()

    async def initiate(self, transaction_id: str, user_id: str, phone: str, email: str) -> OTPChallenge:
        async with self._lock:
            challenge = OTPChallenge(
                transaction_id=transaction_id,
                user_id=user_id,
                sms=ChannelOTP(channel=OTPChannel.SMS, code=_generate_code()),
                email=ChannelOTP(channel=OTPChannel.EMAIL, code=_generate_code()),
            )
            self._challenges[transaction_id] = challenge
            await self._dispatch_sms(phone, challenge.sms.code)
            await self._dispatch_email(email, challenge.email.code)
            return challenge

    async def verify(
        self,
        transaction_id: str,
        *,
        sms_code: str | None = None,
        email_code: str | None = None,
    ) -> OTPChallenge:
        async with self._lock:
            challenge = self._require_challenge(transaction_id)
            self._expire_if_needed(challenge)

            if sms_code is not None:
                self._verify_channel(challenge.sms, sms_code)
            if email_code is not None:
                self._verify_channel(challenge.email, email_code)

            if challenge.should_auto_block and not challenge.both_verified:
                if challenge.any_expired:
                    challenge.sms.status = OTPStatus.EXPIRED
                    challenge.email.status = OTPStatus.EXPIRED
            return challenge

    async def get_status(self, transaction_id: str) -> OTPChallenge:
        async with self._lock:
            challenge = self._require_challenge(transaction_id)
            self._expire_if_needed(challenge)
            return challenge

    def _require_challenge(self, transaction_id: str) -> OTPChallenge:
        challenge = self._challenges.get(transaction_id)
        if challenge is None:
            raise KeyError(f"No OTP challenge for transaction {transaction_id}")
        return challenge

    @staticmethod
    def _expire_if_needed(challenge: OTPChallenge) -> None:
        if challenge.sms.is_expired and challenge.sms.status == OTPStatus.PENDING:
            challenge.sms.status = OTPStatus.EXPIRED
        if challenge.email.is_expired and challenge.email.status == OTPStatus.PENDING:
            challenge.email.status = OTPStatus.EXPIRED

    @staticmethod
    def _verify_channel(channel_otp: ChannelOTP, submitted_code: str) -> None:
        if channel_otp.status in (OTPStatus.VERIFIED, OTPStatus.EXPIRED):
            return
        if channel_otp.is_expired:
            channel_otp.status = OTPStatus.EXPIRED
            return
        if secrets.compare_digest(submitted_code, channel_otp.code):
            channel_otp.status = OTPStatus.VERIFIED
        else:
            channel_otp.status = OTPStatus.FAILED

    @staticmethod
    async def _dispatch_sms(phone: str, code: str) -> None:
        # Mock Sparrow SMS API — replace with real HTTP client in production.
        await asyncio.sleep(0)
        print(f"[Sparrow SMS mock] sent OTP {code} to {phone}")

    @staticmethod
    async def _dispatch_email(email: str, code: str) -> None:
        await asyncio.sleep(0)
        print(f"[Email mock] sent OTP {code} to {email}")
