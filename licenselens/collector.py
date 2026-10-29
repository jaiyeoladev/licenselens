"""Collects licensing data from Microsoft Graph (read-only) and normalises it."""

from __future__ import annotations

import logging

from .catalog import sku_name
from .core.context import Context
from .core.db import iso, parse_dt
from .core.graph import GraphClient, GraphError, graph_client_from_config

log = logging.getLogger(__name__)

USER_FIELDS = (
    "id,displayName,userPrincipalName,mail,accountEnabled,userType,createdDateTime,"
    "department,jobTitle,assignedLicenses"
)

GRAPH_PERMISSIONS = [
    "User.Read.All",
    "Organization.Read.All",
    "LicenseAssignment.Read.All",
    "AuditLog.Read.All (for last sign-in dates; needs Entra ID P1/P2)",
]


def normalise(org: dict, skus_raw: list[dict], subs_raw: list[dict], users_raw: list[dict],
              signin_known: bool, warnings: list[str]) -> dict:
    sku_parts = {s["skuId"]: s.get("skuPartNumber", s["skuId"]) for s in skus_raw}
    skus = []
    for s in skus_raw:
        prepaid = s.get("prepaidUnits") or {}
        part = s.get("skuPartNumber", s["skuId"])
        skus.append({
            "skuId": s["skuId"],
            "partNumber": part,
            "name": sku_name(part),
            "enabled": int(prepaid.get("enabled") or 0),
            "suspended": int(prepaid.get("suspended") or 0),
            "warning": int(prepaid.get("warning") or 0),
            "consumed": int(s.get("consumedUnits") or 0),
            "capabilityStatus": s.get("capabilityStatus", "Enabled"),
            "appliesTo": s.get("appliesTo", "User"),
        })
    subscriptions = []
    for sub in subs_raw:
        subscriptions.append({
            "skuId": sub.get("skuId"),
            "partNumber": sub.get("skuPartNumber") or sku_parts.get(sub.get("skuId"), ""),
            "totalLicenses": int(sub.get("totalLicenses") or 0),
            "status": sub.get("status", ""),
            "isTrial": bool(sub.get("isTrial")),
            "nextLifecycleDateTime": iso(parse_dt(sub.get("nextLifecycleDateTime"))),
            "createdDateTime": iso(parse_dt(sub.get("createdDateTime"))),
        })
    users = []
    for u in users_raw:
        licences = [a.get("skuId") for a in (u.get("assignedLicenses") or []) if a.get("skuId")]
        if not licences:
            continue
        activity = u.get("signInActivity") or {}
        dates = [
            parse_dt(activity.get(k))
            for k in ("lastSignInDateTime", "lastNonInteractiveSignInDateTime", "lastSuccessfulSignInDateTime")
        ]
        dates = [d for d in dates if d]
        users.append({
            "id": u["id"],
            "displayName": u.get("displayName") or u.get("userPrincipalName"),
            "upn": u.get("userPrincipalName") or u.get("mail") or u["id"],
            "enabled": bool(u.get("accountEnabled", True)),
            "userType": u.get("userType") or "Member",
            "created": iso(parse_dt(u.get("createdDateTime"))),
            "department": u.get("department") or "Unassigned",
            "jobTitle": u.get("jobTitle") or "",
            "skus": [sku_parts.get(s, s) for s in licences],
            "lastActivity": iso(max(dates)) if dates else None,
            "signinKnown": signin_known,
        })
    return {
        "tenant": {"name": org.get("displayName", "Your tenant"), "id": org.get("id", "")},
        "skus": skus,
        "subscriptions": subscriptions,
        "users": users,
        "warnings": warnings,
        "counts": {
            "licensed_users": len(users),
            "skus": len(skus),
            "subscriptions": len(subscriptions),
        },
    }


def fetch_users(client: GraphClient, warnings: list[str]) -> tuple[list[dict], bool]:
    try:
        return client.get_all("users", params={"$select": USER_FIELDS + ",signInActivity", "$top": "500"}), True
    except GraphError as exc:
        if exc.status not in (400, 403):
            raise
        warnings.append(
            "Last sign-in dates are unavailable (needs AuditLog.Read.All and Entra ID P1/P2); "
            "inactivity rules were skipped."
        )
        log.warning("signInActivity unavailable: %s", exc)
    return client.get_all("users", params={"$select": USER_FIELDS, "$top": "999"}), False


def collect_live(ctx: Context, client: GraphClient | None = None) -> dict:
    warnings: list[str] = []
    own = client is None
    client = client or graph_client_from_config(ctx.config)
    try:
        orgs = client.get_all("organization", params={"$select": "id,displayName"})
        skus = client.get_all("subscribedSkus")
        try:
            subs = client.get_all("directory/subscriptions")
        except GraphError as exc:
            warnings.append(f"Subscription renewal dates unavailable ({exc.status}); renewal rules were skipped.")
            subs = []
        users, signin_known = fetch_users(client, warnings)
        data = normalise(orgs[0] if orgs else {}, skus, subs, users, signin_known, warnings)
        data["counts"]["graph_calls"] = client.calls
        return data
    finally:
        if own:
            client.close()
