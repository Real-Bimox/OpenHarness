"""Per-provider API-key credential pool with failure cooldowns.

Learned from hermes-agent's ``agent/credential_pool.py`` (spec + deviations
in docs/proposals/error-recovery.md). This is the API-key rotation case: a
provider with several keys, rotated on rate-limit/auth/billing failures with
cooldowns so an exhausted key is skipped until it recovers.

Honest scope, stated plainly: this rotates API keys, not OAuth accounts.
hermes maintains per-provider OAuth token pools with single-use-refresh
bracketing (its largest single module); OpenHarness keeps the existing
singleton OAuth refresh and pools API keys only. Multi-account OAuth pools
are out of scope and documented as the one real capability gap.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# Cooldown after a failure, by recovery reason value.
_COOLDOWN_SECONDS = {
    "auth": 300.0,
    "rate_limit": 3600.0,
    "billing": 3600.0,
}
_DEFAULT_COOLDOWN = 3600.0


@dataclass
class _Credential:
    key: str
    cooldown_until: float = 0.0
    dead: bool = False
    uses: int = 0


@dataclass
class CredentialPool:
    """Fill-first pool of API keys for one provider."""

    provider: str
    _credentials: list[_Credential] = field(default_factory=list)
    _active_index: int = 0

    @classmethod
    def from_keys(cls, provider: str, keys: list[str]) -> "CredentialPool":
        seen: set[str] = set()
        creds: list[_Credential] = []
        for key in keys:
            if key and key not in seen:
                seen.add(key)
                creds.append(_Credential(key=key))
        return cls(provider=provider, _credentials=creds)

    def __len__(self) -> int:
        return len(self._credentials)

    def has_alternatives(self) -> bool:
        """True when rotating could land on a different, usable key."""
        now = time.monotonic()
        usable = [c for c in self._credentials if not c.dead and c.cooldown_until <= now]
        return len(usable) > 1 or (len(usable) == 1 and self._credentials[self._active_index] not in usable)

    def current(self) -> str | None:
        if not self._credentials:
            return None
        return self._credentials[self._active_index].key

    def _select(self) -> str | None:
        now = time.monotonic()
        for offset in range(len(self._credentials)):
            idx = (self._active_index + offset) % len(self._credentials)
            cred = self._credentials[idx]
            if not cred.dead and cred.cooldown_until <= now:
                self._active_index = idx
                cred.uses += 1
                return cred.key
        return None

    def select(self) -> str | None:
        """Return a usable key, lazily clearing expired cooldowns."""
        return self._select()

    def mark_failure(self, reason: str, *, retry_after: float | None = None) -> str | None:
        """Cool down (or kill) the active key, then rotate. Returns the new key."""
        if not self._credentials:
            return None
        cred = self._credentials[self._active_index]
        if reason == "auth" and len(self._credentials) == 1:
            cred.dead = True
        else:
            cooldown = retry_after if retry_after is not None else _COOLDOWN_SECONDS.get(reason, _DEFAULT_COOLDOWN)
            cred.cooldown_until = time.monotonic() + cooldown
        return self._select()


def build_credential_pools(settings) -> dict[str, CredentialPool]:
    """Build pools from ``settings.credential_pools`` ({provider: [keys]})."""
    raw = getattr(settings, "credential_pools", None) or {}
    pools: dict[str, CredentialPool] = {}
    for provider, keys in raw.items():
        if isinstance(keys, list) and len(keys) > 1:
            pool = CredentialPool.from_keys(str(provider), [str(k) for k in keys])
            if len(pool) > 1:
                pools[str(provider)] = pool
    return pools
