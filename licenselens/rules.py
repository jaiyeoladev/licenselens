"""Licence and SaaS spend rules. Pure functions: dataset + settings in, findings out."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from fnmatch import fnmatch
from typing import Any

from .catalog import DEFAULT_SUPERSEDES, sku_name, sku_price
from .core.db import parse_dt, utcnow
from .core.findings import Finding, RuleInfo

RULES = {r.rule_id: r for r in [
    RuleInfo("LIC-001", "Licences on a disabled account", "high", "Microsoft 365",
             "A blocked or disabled account still has paid licences assigned. This is usually a leaver whose "
             "licences were never reclaimed.",
             "Convert to a shared mailbox or apply retention before removing licences if data must be kept."),
    RuleInfo("LIC-002", "Licensed user inactive", "medium", "Microsoft 365",
             "An enabled user with paid licences has not signed in (interactively or non-interactively) for "
             "longer than the inactivity threshold."),
    RuleInfo("LIC-003", "Licensed user never signed in", "medium", "Microsoft 365",
             "The account was created more than the grace period ago, holds paid licences and has never signed in."),
    RuleInfo("LIC-004", "Overlapping licence", "medium", "Microsoft 365",
             "The user holds a licence whose features are already included in a larger plan they also hold "
             "(for example Exchange Online Plan 1 alongside Microsoft 365 E3)."),
    RuleInfo("LIC-005", "Unassigned purchased seats", "low", "Microsoft 365",
             "Seats have been bought but are not assigned to anyone. Annual commitments can usually only be "
             "reduced at renewal, so plan the reduction before the renewal date."),
    RuleInfo("LIC-006", "Guest account with paid licence", "medium", "Microsoft 365",
             "An external guest holds a paid licence. Guests rarely need one; check the business case."),
    RuleInfo("LIC-007", "Subscription renewal approaching", "low", "Renewals",
             "A Microsoft subscription renews or ends within the renewal window. This is the moment to "
             "right-size seat counts."),
    RuleInfo("LIC-008", "Subscription in warning or suspended state", "high", "Renewals",
             "A subscription is in a warning, suspended or locked-out state and users may lose access."),
    RuleInfo("SAAS-001", "Under-used SaaS seats", "medium", "SaaS register",
             "Fewer seats are in use than were purchased, below the utilisation target."),
    RuleInfo("SAAS-002", "Renewal notice deadline approaching", "high", "SaaS register",
             "The last date to give notice (renewal date minus notice period) is close. Missing it usually "
             "locks you into another term."),
    RuleInfo("SAAS-003", "Overlapping tools in the same category", "low", "SaaS register",
             "Two or more tools serve the same purpose. Consolidating usually saves money and reduces data sprawl."),
    RuleInfo("SAAS-004", "SaaS subscription without an owner", "low", "SaaS register",
             "No business owner is recorded, so nobody is accountable for usage, renewal or offboarding."),
]}


def _excluded(upn: str, patterns: list[str]) -> bool:
    upn = upn.lower()
    return any(fnmatch(upn, p.strip().lower()) for p in patterns if p.strip())


def user_cost(user: dict, prices: dict) -> float:
    return sum(sku_price(s, prices) for s in user["skus"])


def paid_skus(user: dict, prices: dict) -> list[str]:
    return [s for s in user["skus"] if sku_price(s, prices) > 0]


def analyse(data: dict, settings: dict[str, Any], ctx=None) -> list[Finding]:
    now = utcnow()
    prices = settings["sku_prices"] or {}
    supersedes = settings["supersedes"] or DEFAULT_SUPERSEDES
    patterns = (settings["excluded_upns"] or "").splitlines()
    inactive_days = int(settings["inactive_days"])
    grace_days = int(settings["never_signed_in_grace_days"])
    out: list[Finding] = []

    for u in data.get("users", []):
        if _excluded(u["upn"], patterns):
            continue
        paid = paid_skus(u, prices)
        if not paid:
            continue
        cost = user_cost(u, prices)
        names = ", ".join(sku_name(s) for s in paid)
        base = {"upn": u["upn"], "department": u["department"], "licences": names}
        last = parse_dt(u.get("lastActivity"))
        created = parse_dt(u.get("created"))

        if not u["enabled"]:
            out.append(Finding(
                "LIC-001", "high", f"Disabled account holds {len(paid)} paid licence(s)", "user", u["id"],
                u["displayName"], {**base, "last_activity": u.get("lastActivity") or "never"},
                "Confirm the person has left, preserve mailbox/OneDrive data if required (shared mailbox, "
                "retention or litigation hold), then remove the licences.",
                monthly_cost=cost,
            ))
            continue

        if u["userType"] == "Guest":
            out.append(Finding(
                "LIC-006", "medium", "Guest account holds a paid licence", "user", u["id"], u["displayName"],
                base, "Check whether the guest needs a licence. Most guest scenarios work without one; remove "
                "it or convert the person to a member account if they are staff.", monthly_cost=cost,
            ))

        if u.get("signinKnown"):
            if last is None and created and (now - created).days > grace_days:
                out.append(Finding(
                    "LIC-003", "medium", "Licensed user has never signed in", "user", u["id"], u["displayName"],
                    {**base, "created": u.get("created"), "days_since_created": (now - created).days},
                    "Confirm with the line manager whether this account is needed. Remove the licences "
                    "(or delete the account) if not.", monthly_cost=cost,
                ))
            elif last is not None and (now - last).days > inactive_days:
                out.append(Finding(
                    "LIC-002", "medium", f"No sign-in for {(now - last).days} days", "user", u["id"],
                    u["displayName"], {**base, "last_activity": u.get("lastActivity"),
                                       "days_inactive": (now - last).days},
                    "Confirm with the line manager. If the person is on long-term leave, consider a lower "
                    "plan; otherwise disable the account and reclaim the licences.", monthly_cost=cost,
                ))

        held = set(u["skus"])
        redundant: dict[str, str] = {}
        for big, smaller in supersedes.items():
            if big in held:
                for s in smaller:
                    if s in held and s != big and sku_price(s, prices) > 0:
                        redundant.setdefault(s, big)
        for small, big in redundant.items():
            out.append(Finding(
                "LIC-004", "medium", f"{sku_name(small)} is already included in {sku_name(big)}", "user",
                u["id"], u["displayName"], {**base, "redundant": sku_name(small), "included_in": sku_name(big)},
                f"Remove {sku_name(small)} from this user; {sku_name(big)} already provides its features. "
                "Check for any group-based licensing that re-applies it.",
                monthly_cost=sku_price(small, prices), key=small,
            ))

    min_unused = int(settings["min_unused_seats"])
    subs_by_sku = defaultdict(list)
    for sub in data.get("subscriptions", []):
        subs_by_sku[sub["partNumber"]].append(sub)
    for s in data.get("skus", []):
        price = sku_price(s["partNumber"], prices)
        unused = s["enabled"] - s["consumed"]
        if price > 0 and unused >= min_unused and s["enabled"] < 100000:
            cost = unused * price
            renew = sorted(filter(None, (x.get("nextLifecycleDateTime") for x in subs_by_sku[s["partNumber"]])))
            out.append(Finding(
                "LIC-005", "medium" if cost >= float(settings["unused_seat_medium_cost"]) else "low",
                f"{unused} unassigned {s['name']} seat(s)", "sku", s["skuId"], s["name"],
                {"part_number": s["partNumber"], "purchased": s["enabled"], "assigned": s["consumed"],
                 "unused": unused, "price_per_seat": price, "next_renewal": renew[0] if renew else "unknown"},
                "Reassign these seats to new starters before buying more, or reduce the seat count at the "
                "next renewal (monthly-term seats can be reduced straight away).", monthly_cost=cost,
            ))
        if s.get("capabilityStatus") in ("Warning", "Suspended", "LockedOut") or s.get("suspended") or s.get("warning"):
            out.append(Finding(
                "LIC-008", "high", f"{s['name']} is in {s.get('capabilityStatus')} state", "sku", s["skuId"],
                s["name"], {"status": s.get("capabilityStatus"), "suspended": s.get("suspended"),
                            "warning": s.get("warning")},
                "Check billing and renewal in the Microsoft 365 admin centre to avoid users losing access.",
            ))

    window = int(settings["renewal_window_days"])
    skus_by_part = {s["partNumber"]: s for s in data.get("skus", [])}
    for sub in data.get("subscriptions", []):
        when = parse_dt(sub.get("nextLifecycleDateTime"))
        if not when or not (now <= when <= now + timedelta(days=window)):
            continue
        sku = skus_by_part.get(sub["partNumber"], {})
        price = sku_price(sub["partNumber"], prices)
        unused = max(0, sku.get("enabled", 0) - sku.get("consumed", 0)) if sku else 0
        days = (when - now).days
        name = sku_name(sub["partNumber"])
        out.append(Finding(
            "LIC-007", "medium" if (days <= 30 and unused) or sub.get("isTrial") else "low",
            f"{name} {'trial ends' if sub.get('isTrial') else 'renews'} in {days} days", "subscription",
            f"{sub['skuId']}:{sub.get('nextLifecycleDateTime', '')[:10]}", name,
            {"renewal_date": sub.get("nextLifecycleDateTime"), "licences": sub["totalLicenses"],
             "unused_seats": unused, "trial": sub.get("isTrial"),
             "annual_value": round(price * sub["totalLicenses"] * 12, 2)},
            (f"Reduce the seat count by up to {unused} before renewal and remove licences flagged as "
             "inactive or overlapping first." if unused else
             "Review the licence findings for this product before it renews.")
            + (" This is a trial: decide whether to buy or let it lapse." if sub.get("isTrial") else ""),
            due_date=when.date().isoformat(),
        ))

    out.extend(analyse_saas(data.get("saas", []), settings))
    return out


def app_name(a: dict) -> str:
    vendor, product = (a.get("vendor") or "").strip(), (a.get("product") or "").strip()
    if not vendor or product.lower().startswith(vendor.lower()):
        return product or vendor
    return f"{vendor} {product}"


def analyse_saas(apps: list[dict], settings: dict[str, Any]) -> list[Finding]:
    now = utcnow()
    target = float(settings["saas_min_utilisation"])
    notice_window = int(settings["saas_notice_window_days"])
    out: list[Finding] = []
    by_category: dict[str, list[dict]] = defaultdict(list)
    for a in apps:
        name = app_name(a)
        seats, used, cost = int(a.get("seats_purchased") or 0), int(a.get("seats_used") or 0), float(a.get("monthly_cost") or 0)
        if a.get("category"):
            by_category[a["category"].strip().lower()].append(a)
        if seats > 0 and used < seats and used / seats < target:
            per_seat = cost / seats if seats else 0
            waste = (seats - used) * per_seat
            out.append(Finding(
                "SAAS-001", "medium" if waste >= 100 else "low",
                f"{seats - used} of {seats} seats unused ({used / seats:.0%} utilisation)", "saas_app",
                str(a["id"]), name, {"seats_purchased": seats, "seats_used": used, "monthly_cost": cost,
                                     "utilisation": f"{used / seats:.0%}", "owner": a.get("owner") or "—"},
                "Ask the owner to confirm who still needs access and reduce the seat count at renewal.",
                monthly_cost=waste, owner_hint=a.get("owner") or None,
            ))
        renewal = parse_dt(a.get("renewal_date"))
        if renewal:
            notice_by = renewal - timedelta(days=int(a.get("notice_days") or 0))
            days = (notice_by.date() - now.date()).days
            if 0 <= days <= notice_window:
                out.append(Finding(
                    "SAAS-002", "high" if days <= 14 else "medium",
                    f"Notice deadline in {days} days (renews {renewal.date().isoformat()})", "saas_app",
                    str(a["id"]), name, {"renewal_date": renewal.date().isoformat(),
                                         "notice_days": a.get("notice_days"), "notice_by": notice_by.date().isoformat(),
                                         "annual_value": round(cost * 12, 2), "owner": a.get("owner") or "—"},
                    "Decide now whether to renew, renegotiate or cancel, and send notice before the deadline.",
                    due_date=notice_by.date().isoformat(), owner_hint=a.get("owner") or None,
                    key=renewal.date().isoformat(),
                ))
        if not (a.get("owner") or "").strip():
            out.append(Finding(
                "SAAS-004", "low", "No business owner recorded", "saas_app", str(a["id"]), name,
                {"category": a.get("category"), "monthly_cost": cost},
                "Assign an owner who is accountable for usage reviews, renewals and offboarding.",
            ))
    for cat, members in by_category.items():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda a: float(a.get("monthly_cost") or 0))
        names = [app_name(a) for a in members]
        cheapest = float(members[0].get("monthly_cost") or 0)
        out.append(Finding(
            "SAAS-003", "low", f"{len(members)} tools for {members[0]['category']}", "saas_category", cat,
            members[0]["category"], {"tools": names, "combined_monthly_cost": sum(float(a.get('monthly_cost') or 0) for a in members)},
            "Pick one standard tool for this purpose and migrate users before the other contracts renew.",
            monthly_cost=cheapest,
        ))
    return out
