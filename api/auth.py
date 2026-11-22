"""
Institutional Security, RBAC, Chinese Wall ACLs, Stateless HS256 Auth & PII/MNPI Guardrails.

Implements:
  - `Role` enum, `TenantPolicy`, `UserPrincipal`
  - Role permission matrix (`has_permission`)
  - Chinese Wall enforcement (`can_access_deal`) & Clean Team gate (`can_access_clean_team`)
  - Stateless HMAC-SHA256 JWT token issuer/verifier (`create_access_token`, `verify_access_token`)
  - SSO / OIDC claims mapper (`map_oidc_claims`)
  - Reversible PII / MNPI redaction vault (`redact_pii_mnpi`, `rehydrate_pii_mnpi`)
  - VDR prompt-injection detector & neutralizer (`sanitize_prompt_injection`)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class Role(str, Enum):
    MD = "MD"
    VP = "VP"
    ASSOCIATE = "ASSOCIATE"
    LEGAL_COUNSEL = "LEGAL_COUNSEL"
    CLEAN_TEAM = "CLEAN_TEAM"
    EXTERNAL_ADVISOR = "EXTERNAL_ADVISOR"
    AUDITOR = "AUDITOR"
    ADMIN = "ADMIN"


class TokenError(ValueError):
    """Raised when an access token is malformed, invalid, or expired."""


@dataclass
class TenantPolicy:
    """Tenant-level security controls: IP allowlist, Zero Data Retention, Chinese Wall ACLs."""
    tenant_id: str = "default"
    ip_allowlist: list[str] = field(default_factory=list)   # Empty = allow all; supports IP or CIDR
    zdr_enforced: bool = True                               # Zero Data Retention flag
    allowed_deals: Optional[list[str]] = None               # None or ["*"] = unrestricted within tenant
    blocked_deals: list[str] = field(default_factory=list)  # Explicit Chinese Wall deny list

    def is_ip_allowed(self, client_ip: str) -> bool:
        if not self.ip_allowlist:
            return True
        if not client_ip:
            return False
        try:
            addr = ipaddress.ip_address(client_ip.strip())
        except ValueError:
            return False
        for entry in self.ip_allowlist:
            try:
                if "/" in entry:
                    if addr in ipaddress.ip_network(entry.strip(), strict=False):
                        return True
                else:
                    if addr == ipaddress.ip_address(entry.strip()):
                        return True
            except ValueError:
                continue
        return False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UserPrincipal:
    """Authenticated caller context with RBAC role, tenant policy & deal-level ACLs."""
    sub: str
    name: str
    role: Role
    tenant_id: str = "default"
    allowed_deals: Optional[list[str]] = None               # None or ["*"] = all tenant deals not blocked
    blocked_deals: list[str] = field(default_factory=list)  # Chinese Wall conflict list
    clean_team_deals: list[str] = field(default_factory=list)
    policy: TenantPolicy = field(default_factory=TenantPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.role, Role):
            self.role = Role(str(self.role).strip().upper())
        if self.policy.tenant_id == "default" and self.tenant_id != "default":
            self.policy = TenantPolicy(
                tenant_id=self.tenant_id,
                ip_allowlist=list(self.policy.ip_allowlist),
                zdr_enforced=self.policy.zdr_enforced,
                allowed_deals=list(self.policy.allowed_deals) if self.policy.allowed_deals is not None else None,
                blocked_deals=list(self.policy.blocked_deals),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub": self.sub,
            "name": self.name,
            "role": self.role.value,
            "tenant_id": self.tenant_id,
            "allowed_deals": self.allowed_deals,
            "blocked_deals": self.blocked_deals,
            "clean_team_deals": self.clean_team_deals,
            "policy": self.policy.to_dict(),
        }


# =====================================================================
# RBAC Permission Matrix, Chinese Wall & Clean Team Gates
# =====================================================================

ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.ADMIN: frozenset({
        "deal:read", "deal:write", "deal:analyze", "deal:export",
        "dcf:run", "legal:run",
        "qa:read", "qa:write", "qa:verify",
        "claim:verify",
        "ic:read", "ic:vote",
        "audit:read", "audit:verify",
        "security:redact", "admin:manage",
    }),
    Role.MD: frozenset({
        "deal:read", "deal:write", "deal:analyze", "deal:export",
        "dcf:run", "legal:run",
        "qa:read", "qa:write", "qa:verify",
        "claim:verify",
        "ic:read", "ic:vote",
        "clean_team:read", "clean_team:write",
        "audit:read", "audit:verify",
        "security:redact",
    }),
    Role.VP: frozenset({
        "deal:read", "deal:write", "deal:analyze", "deal:export",
        "dcf:run", "legal:run",
        "qa:read", "qa:write", "qa:verify",
        "claim:verify",
        "ic:read", "ic:vote",
        "security:redact",
    }),
    Role.ASSOCIATE: frozenset({
        "deal:read", "deal:write", "deal:analyze", "deal:export",
        "dcf:run", "legal:run",
        "qa:read", "qa:write",
        "claim:verify",
        "ic:read",
        "security:redact",
    }),
    Role.LEGAL_COUNSEL: frozenset({
        "deal:read", "deal:export",
        "legal:run",
        "qa:read", "qa:write", "qa:verify",
        "claim:verify",
        "ic:read",
        "security:redact",
    }),
    Role.CLEAN_TEAM: frozenset({
        "deal:read",
        "dcf:run", "legal:run",
        "qa:read",
        "clean_team:read", "clean_team:write",
        "security:redact",
    }),
    Role.EXTERNAL_ADVISOR: frozenset({
        "deal:read",
        "qa:read", "qa:write",
        "security:redact",
    }),
    Role.AUDITOR: frozenset({
        "deal:read",
        "qa:read",
        "ic:read",
        "audit:read", "audit:verify",
    }),
}


def has_permission(principal: UserPrincipal, action: str) -> bool:
    """Check whether `principal.role` is authorized for `action`."""
    allowed = ROLE_PERMISSIONS.get(principal.role, frozenset())
    return action in allowed


def can_access_deal(principal: UserPrincipal, deal_id: str) -> bool:
    """
    Enforce Chinese Wall ACLs across both TenantPolicy and UserPrincipal.
    Explicit deny (`blocked_deals`) ALWAYS overrides any allowlist.
    """
    if not deal_id:
        return False
    if deal_id in principal.blocked_deals or deal_id in principal.policy.blocked_deals:
        return False

    if principal.policy.allowed_deals is not None:
        if "*" not in principal.policy.allowed_deals and deal_id not in principal.policy.allowed_deals:
            return False

    if principal.allowed_deals is not None:
        if "*" not in principal.allowed_deals and deal_id not in principal.allowed_deals:
            return False

    return True


def can_access_clean_team(principal: UserPrincipal, deal_id: str) -> bool:
    """
    Enforce antitrust Clean Team isolation: only CLEAN_TEAM or MD roles who also
    pass the Chinese Wall check (and optional deal-level clean-team roster) may access.
    """
    if principal.role not in {Role.CLEAN_TEAM, Role.MD}:
        return False
    if not can_access_deal(principal, deal_id):
        return False
    if principal.clean_team_deals:
        if "*" not in principal.clean_team_deals and deal_id not in principal.clean_team_deals:
            return False
    return True


# =====================================================================
# Stateless HMAC-SHA256 JWT Token Issuer / Verifier & OIDC Mapper
# =====================================================================

_DEFAULT_DEV_SECRET = "ma-dd-copilot-dev-hs256-secret-change-in-prod"


def _resolve_secret(secret: Optional[str]) -> bytes:
    raw = secret or os.getenv("API_JWT_SECRET") or _DEFAULT_DEV_SECRET
    return raw.encode("utf-8")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode((segment + padding).encode("ascii"))


def create_access_token(
    principal: UserPrincipal,
    secret: Optional[str] = None,
    expires_minutes: int = 120,
) -> str:
    """Issue a standard 3-segment HS256 JWT encoding the UserPrincipal & TenantPolicy."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": principal.sub,
        "name": principal.name,
        "role": principal.role.value,
        "tenant_id": principal.tenant_id,
        "allowed_deals": principal.allowed_deals,
        "blocked_deals": principal.blocked_deals,
        "clean_team_deals": principal.clean_team_deals,
        "policy": principal.policy.to_dict(),
        "iat": now,
        "exp": now + max(1, int(expires_minutes)) * 60,
    }
    h_seg = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    p_seg = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{h_seg}.{p_seg}".encode("ascii")
    sig = hmac.new(_resolve_secret(secret), signing_input, hashlib.sha256).digest()
    return f"{h_seg}.{p_seg}.{_b64url_encode(sig)}"


def verify_access_token(
    token: str,
    secret: Optional[str] = None,
) -> UserPrincipal:
    """Verify HS256 signature and expiration; return reconstructed `UserPrincipal`."""
    if not token or not isinstance(token, str):
        raise TokenError("Missing access token.")
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise TokenError("Malformed JWT token structure.")
    h_seg, p_seg, s_seg = parts
    try:
        header = json.loads(_b64url_decode(h_seg).decode("utf-8"))
        if header.get("alg") != "HS256":
            raise TokenError(f"Unsupported JWT algorithm: {header.get('alg')}")
        signing_input = f"{h_seg}.{p_seg}".encode("ascii")
        expected_sig = hmac.new(_resolve_secret(secret), signing_input, hashlib.sha256).digest()
        provided_sig = _b64url_decode(s_seg)
        if not hmac.compare_digest(expected_sig, provided_sig):
            raise TokenError("Invalid token signature.")
        payload = json.loads(_b64url_decode(p_seg).decode("utf-8"))
    except TokenError:
        raise
    except Exception as exc:
        raise TokenError(f"Unable to decode token: {exc}") from exc

    exp = payload.get("exp")
    if exp is None or int(time.time()) > int(exp):
        raise TokenError("Access token has expired.")

    try:
        role = Role(str(payload["role"]).upper())
    except Exception as exc:
        raise TokenError(f"Invalid role claim: {payload.get('role')}") from exc

    pol_raw = payload.get("policy") or {}
    policy = TenantPolicy(
        tenant_id=str(pol_raw.get("tenant_id") or payload.get("tenant_id") or "default"),
        ip_allowlist=list(pol_raw.get("ip_allowlist") or []),
        zdr_enforced=bool(pol_raw.get("zdr_enforced", True)),
        allowed_deals=pol_raw.get("allowed_deals"),
        blocked_deals=list(pol_raw.get("blocked_deals") or []),
    )
    return UserPrincipal(
        sub=str(payload["sub"]),
        name=str(payload.get("name") or payload["sub"]),
        role=role,
        tenant_id=str(payload.get("tenant_id") or "default"),
        allowed_deals=payload.get("allowed_deals"),
        blocked_deals=list(payload.get("blocked_deals") or []),
        clean_team_deals=list(payload.get("clean_team_deals") or []),
        policy=policy,
    )


_OIDC_ROLE_MAP: dict[str, Role] = {
    "md": Role.MD,
    "managing_director": Role.MD,
    "partner": Role.MD,
    "vp": Role.VP,
    "vice_president": Role.VP,
    "director": Role.VP,
    "associate": Role.ASSOCIATE,
    "analyst": Role.ASSOCIATE,
    "legal": Role.LEGAL_COUNSEL,
    "legal_counsel": Role.LEGAL_COUNSEL,
    "counsel": Role.LEGAL_COUNSEL,
    "clean_team": Role.CLEAN_TEAM,
    "cleanteam": Role.CLEAN_TEAM,
    "external": Role.EXTERNAL_ADVISOR,
    "external_advisor": Role.EXTERNAL_ADVISOR,
    "auditor": Role.AUDITOR,
    "compliance": Role.AUDITOR,
    "admin": Role.ADMIN,
    "superadmin": Role.ADMIN,
}


def map_oidc_claims(
    claims: dict[str, Any],
    tenant_policy: Optional[TenantPolicy] = None,
) -> UserPrincipal:
    """
    Map standard SSO / OIDC ID-token claims (Okta, Entra ID / Azure AD, Auth0, Google)
    into a least-privilege `UserPrincipal`.
    """
    sub = str(claims.get("sub") or claims.get("oid") or claims.get("email") or "unknown")
    name = str(claims.get("name") or claims.get("preferred_username") or claims.get("email") or sub)
    tenant_id = str(claims.get("tid") or claims.get("tenant_id") or claims.get("org_id") or "default")

    raw_roles = claims.get("roles") or claims.get("groups") or claims.get("role") or []
    if isinstance(raw_roles, str):
        raw_roles = [raw_roles]

    mapped_role = Role.EXTERNAL_ADVISOR  # secure least-privilege default
    priority_order = [
        Role.ADMIN, Role.MD, Role.CLEAN_TEAM, Role.LEGAL_COUNSEL,
        Role.VP, Role.ASSOCIATE, Role.AUDITOR, Role.EXTERNAL_ADVISOR,
    ]
    matched: set[Role] = set()
    for r in raw_roles:
        norm = re.sub(r"[^a-z0-9]+", "_", str(r).strip().lower()).strip("_")
        # Support group prefixes like "ma_dd_vp" -> match suffix too
        if norm in _OIDC_ROLE_MAP:
            matched.add(_OIDC_ROLE_MAP[norm])
        else:
            for k, v in _OIDC_ROLE_MAP.items():
                if norm.endswith(f"_{k}") or norm == k:
                    matched.add(v)
                    break

    for candidate in priority_order:
        if candidate in matched:
            mapped_role = candidate
            break

    policy = tenant_policy or TenantPolicy(tenant_id=tenant_id)
    return UserPrincipal(
        sub=sub,
        name=name,
        role=mapped_role,
        tenant_id=tenant_id,
        allowed_deals=claims.get("allowed_deals"),
        blocked_deals=list(claims.get("blocked_deals") or []),
        clean_team_deals=list(claims.get("clean_team_deals") or []),
        policy=policy,
    )


# =====================================================================
# PII / MNPI Reversible Redaction & Prompt-Injection Sanitization
# =====================================================================

_IBAN_RE = re.compile(
    r"\b[A-Z]{2}\d{2}(?:[\s\-]?[A-Z0-9]{4}){3,7}(?:[\s\-]?[A-Z0-9]{1,4})?\b"
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_SSN_RE = re.compile(r"\b(?!000|666|9\d\d)\d{3}[\-\s](?!00)\d{2}[\-\s](?!0000)\d{4}\b")
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+\d{1,3}[\s\-\.]?)?(?:\(\d{2,4}\)|\d{2,4})[\s\-\.]\d{3,4}[\s\-\.]\d{3,4}(?!\w)"
)


def redact_pii_mnpi(
    text: str,
    code_words: Optional[list[str]] = None,
) -> tuple[str, dict[str, str]]:
    """
    Redact PII (emails, SSNs, IBANs, phone numbers) and optional MNPI deal code-words
    before sending VDR text to external LLMs.

    Returns `(redacted_text, vault)` where `vault` maps placeholder tokens back to
    original values for local post-processing via `rehydrate_pii_mnpi`.
    """
    if not text:
        return "", {}

    vault: dict[str, str] = {}
    reverse: dict[str, str] = {}
    counters = {"MNPI": 0, "IBAN": 0, "EMAIL": 0, "SSN": 0, "PHONE": 0}

    def _token_for(kind: str, original: str) -> str:
        key = f"{kind}::{original}"
        if key in reverse:
            return reverse[key]
        counters[kind] += 1
        token = f"[REDACTED_{kind}_{counters[kind]}]"
        vault[token] = original
        reverse[key] = token
        return token

    out = text

    # 1. Custom MNPI deal code-words (longest-first to avoid partial overlap)
    if code_words:
        cleaned_words = sorted({w.strip() for w in code_words if w and w.strip()}, key=len, reverse=True)
        for cw in cleaned_words:
            pat = re.compile(rf"(?<!\w){re.escape(cw)}(?!\w)", re.IGNORECASE)
            out = pat.sub(lambda m: _token_for("MNPI", m.group(0)), out)

    # 2. IBANs (before phone numbers so digits inside IBANs are captured as IBAN)
    def _iban_sub(match: re.Match[str]) -> str:
        compact = re.sub(r"[\s\-]", "", match.group(0))
        if 15 <= len(compact) <= 34:
            return _token_for("IBAN", match.group(0))
        return match.group(0)

    out = _IBAN_RE.sub(_iban_sub, out)

    # 3. Emails
    out = _EMAIL_RE.sub(lambda m: _token_for("EMAIL", m.group(0)), out)

    # 4. US SSNs
    out = _SSN_RE.sub(lambda m: _token_for("SSN", m.group(0)), out)

    # 5. Phone numbers (guard against matching dates like 2024-01-15)
    def _phone_sub(match: re.Match[str]) -> str:
        val = match.group(0)
        if re.fullmatch(r"(?:19|20)\d{2}[\-\.]\d{2}[\-\.]\d{2}", val):
            return val
        digits = re.sub(r"\D", "", val)
        if len(digits) < 10:
            return val
        return _token_for("PHONE", val)

    out = _PHONE_RE.sub(_phone_sub, out)
    return out, vault


def rehydrate_pii_mnpi(text: str, vault: dict[str, str]) -> str:
    """Restore redacted placeholder tokens using the session-local `vault`."""
    if not text or not vault:
        return text
    out = text
    for token, original in vault.items():
        out = out.replace(token, original)
    return out


_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"(?:ignore|disregard|forget|override)\s+(?:all\s+)?(?:previous|prior|above|system|developer)\s+"
            r"(?:instructions|prompts|rules|directives|constraints)",
            re.IGNORECASE,
        ),
    ),
    (
        "role_hijack",
        re.compile(
            r"(?:you\s+are\s+now|act\s+as\s+(?:a|an)?\s*(?:unrestricted|jailbroken|DAN)|"
            r"enter\s+developer\s+mode|jailbreak\s+mode)",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt_exfiltration",
        re.compile(
            r"(?:reveal|print|output|dump|show|leak)\s+(?:your\s+)?(?:system\s+prompt|"
            r"hidden\s+instructions|api\s*keys?|secret\s+tokens?)",
            re.IGNORECASE,
        ),
    ),
    (
        "delimiter_spoofing",
        re.compile(
            r"(?:<\|im_start\|>|<\|im_end\|>|\[INST\]|\[/INST\]|</?system>|###\s*SYSTEM\s*:)",
            re.IGNORECASE,
        ),
    ),
    (
        "ic_verdict_manipulation",
        re.compile(
            r"(?:AI\s+assistant\s*:|note\s+to\s+AI\s*:|hidden\s+instruction\s*:|"
            r"(?:always|must)\s+recommend\s+['\"]?go['\"]?|"
            r"set\s+(?:overall[_\s])?conviction[_\s]score\s+to\s+100)",
            re.IGNORECASE,
        ),
    ),
)


def sanitize_prompt_injection(text: str) -> tuple[str, list[str]]:
    """
    Detect and neutralize prompt-injection payloads hidden inside uploaded VDR
    filings or seller Q&A responses.

    Returns `(sanitized_text, detected_labels)`.
    """
    if not text:
        return "", []
    sanitized = text
    detected: list[str] = []
    for label, pattern in _INJECTION_PATTERNS:
        if pattern.search(sanitized):
            detected.append(label)
            sanitized = pattern.sub("[NEUTRALIZED_INJECTION]", sanitized)
    return sanitized, detected
