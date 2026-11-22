"""
Enterprise Security, RBAC, Chinese Wall, WORM Audit Trail & FastAPI Service Layer.
"""
from __future__ import annotations

from api.audit import AUDIT_ACTIONS, GENESIS_HASH, AuditLogger
from api.auth import (
    ROLE_PERMISSIONS,
    Role,
    TenantPolicy,
    TokenError,
    UserPrincipal,
    can_access_clean_team,
    can_access_deal,
    create_access_token,
    has_permission,
    map_oidc_claims,
    redact_pii_mnpi,
    rehydrate_pii_mnpi,
    sanitize_prompt_injection,
    verify_access_token,
)

__all__ = [
    "Role",
    "TenantPolicy",
    "UserPrincipal",
    "TokenError",
    "ROLE_PERMISSIONS",
    "has_permission",
    "can_access_deal",
    "can_access_clean_team",
    "create_access_token",
    "verify_access_token",
    "map_oidc_claims",
    "redact_pii_mnpi",
    "rehydrate_pii_mnpi",
    "sanitize_prompt_injection",
    "AuditLogger",
    "AUDIT_ACTIONS",
    "GENESIS_HASH",
]
