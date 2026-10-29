"""Deterministic synthetic tenant in Microsoft Graph's response shape.

Dates are relative to "now" so the demo always looks current. The data runs through the same
normaliser and rules as live data, so the demo exercises the real code path.
"""

from __future__ import annotations

import random
import uuid
from datetime import timedelta

from .collector import normalise
from .core.context import Context
from .core.db import iso, utcnow

DOMAIN = "northwindcare.example"
FIRST = [
    "Amelia", "Oliver", "Isla", "George", "Ava", "Noah", "Mia", "Leo", "Grace", "Arthur", "Freya",
    "Oscar", "Lily", "Harry", "Ella", "Jack", "Sophia", "Theo", "Chloe", "Henry", "Zara", "Kofi",
    "Priya", "Tunde", "Aisha", "Daniel", "Hannah", "Samuel", "Ruth", "Ibrahim", "Sienna", "Ethan",
    "Maya", "Lucas", "Nia", "Adam", "Erin", "Joseph", "Bethany", "Rhys",
]
LAST = [
    "Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies", "Patel", "Robinson",
    "Wright", "Thompson", "Evans", "Walker", "White", "Roberts", "Green", "Hall", "Okafor", "Adeyemi",
    "Khan", "Clarke", "Hughes", "Edwards", "Mensah", "Shah", "Lewis", "Ward", "Morgan", "Baker",
]
DEPTS = [
    ("Clinical Operations", 0.28), ("Customer Service", 0.18), ("Finance", 0.10), ("HR", 0.07),
    ("IT", 0.08), ("Sales", 0.14), ("Executive", 0.04), ("Facilities", 0.06), ("Marketing", 0.05),
]
SKU_IDS = {}


def _sku_id(part: str) -> str:
    if part not in SKU_IDS:
        SKU_IDS[part] = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"sku.{part}"))
    return SKU_IDS[part]


def build_raw(seed: int = 42) -> tuple[dict, list, list, list]:
    rng = random.Random(seed)
    now = utcnow()
    users: list[dict] = []
    used_names: set[str] = set()

    def person(dept: str) -> tuple[str, str]:
        while True:
            fn, ln = rng.choice(FIRST), rng.choice(LAST)
            upn = f"{fn}.{ln}".lower()
            if upn not in used_names:
                used_names.add(upn)
                return f"{fn} {ln}", f"{upn}@{DOMAIN}"

    def add(name, upn, dept, skus, enabled=True, last_days=None, created_days=None,
            user_type="Member", title=""):
        created = now - timedelta(days=created_days if created_days is not None else rng.randint(120, 2400))
        activity = None
        if last_days is not None:
            interactive = now - timedelta(days=last_days, hours=rng.randint(0, 20))
            activity = {
                "lastSignInDateTime": iso(interactive),
                "lastNonInteractiveSignInDateTime": iso(interactive + timedelta(hours=rng.randint(0, 6))),
            }
        users.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, upn)),
            "displayName": name,
            "userPrincipalName": upn,
            "accountEnabled": enabled,
            "userType": user_type,
            "createdDateTime": iso(created),
            "department": dept,
            "jobTitle": title,
            "assignedLicenses": [{"skuId": _sku_id(s), "disabledPlans": []} for s in skus],
            "signInActivity": activity,
        })

    # Core workforce
    for _ in range(205):
        dept = rng.choices([d for d, _ in DEPTS], weights=[w for _, w in DEPTS])[0]
        name, upn = person(dept)
        skus = ["SPE_F1"] if dept in ("Customer Service", "Facilities") and rng.random() < 0.7 else ["SPE_E3"]
        if dept in ("IT", "Executive") and rng.random() < 0.55:
            skus = ["SPE_E5"]
        if dept == "Finance" and rng.random() < 0.6:
            skus.append("POWER_BI_PRO")
        if dept in ("Clinical Operations", "IT") and rng.random() < 0.08:
            skus.append("VISIOCLIENT")
        if dept in ("IT", "Executive", "Sales") and rng.random() < 0.18:
            skus.append("Microsoft_365_Copilot")
        if dept == "IT" and rng.random() < 0.25:
            skus.append("PROJECTPROFESSIONAL")
        if rng.random() < 0.3:
            skus.append("POWER_BI_STANDARD")
        add(name, upn, dept, skus, last_days=rng.choice([0, 0, 0, 1, 1, 2, 3, 5, 8, 13, 21]))

    # Leavers whose accounts were disabled but kept their licences
    for _ in range(9):
        dept = rng.choice(["Sales", "Clinical Operations", "Customer Service", "Finance"])
        name, upn = person(dept)
        skus = ["SPE_E3"] + (["Microsoft_365_Copilot"] if rng.random() < 0.25 else [])
        add(name, upn, dept, skus, enabled=False, last_days=rng.randint(40, 300))

    # Enabled but inactive for months
    for _ in range(14):
        dept = rng.choice([d for d, _ in DEPTS])
        name, upn = person(dept)
        skus = rng.choice([["SPE_E3"], ["SPE_E3", "POWER_BI_PRO"], ["SPE_F1"], ["SPE_E3", "VISIOCLIENT"]])
        add(name, upn, dept, skus, last_days=rng.randint(95, 420))

    # Provisioned but never signed in
    for _ in range(5):
        dept = rng.choice(["Clinical Operations", "Sales", "Marketing"])
        name, upn = person(dept)
        add(name, upn, dept, ["SPE_E3"], last_days=None, created_days=rng.randint(40, 200))

    # Overlapping licences
    for combo in (["SPE_E5", "SPE_E3"], ["SPE_E5", "POWER_BI_PRO"], ["SPE_E5", "POWER_BI_PRO"],
                  ["SPE_E3", "EXCHANGESTANDARD"], ["SPE_E3", "EXCHANGESTANDARD"], ["SPE_E3", "EMS"],
                  ["SPE_E3", "AAD_PREMIUM"], ["SPE_E5", "SPE_E3", "AAD_PREMIUM_P2"]):
        dept = rng.choice(["IT", "Executive", "Finance"])
        name, upn = person(dept)
        add(name, upn, dept, combo, last_days=rng.randint(0, 6))

    # Guests holding paid licences
    add("Dr Marcus Bell (Locum)", f"marcus.bell_partnerhealth.example#EXT#@{DOMAIN}", "Clinical Operations",
        ["SPE_E3"], user_type="Guest", last_days=33)
    add("Kemi Ade (Contractor)", f"kemi.ade_agency.example#EXT#@{DOMAIN}", "IT",
        ["SPE_E3", "VISIOCLIENT"], user_type="Guest", last_days=140)

    # Break-glass / service accounts (excluded by default settings)
    add("Break Glass 01", f"breakglass01@{DOMAIN}", "IT", ["AAD_PREMIUM_P2"], last_days=200)
    add("Scanner Service", f"svc-scanner@{DOMAIN}", "IT", ["EXCHANGESTANDARD"], last_days=1)

    # Purchased seats (some deliberately over-bought)
    consumed: dict[str, int] = {}
    for u in users:
        for lic in u["assignedLicenses"]:
            consumed[lic["skuId"]] = consumed.get(lic["skuId"], 0) + 1
    extra = {"SPE_E3": 22, "SPE_E5": 3, "SPE_F1": 6, "POWER_BI_PRO": 7, "VISIOCLIENT": 4,
             "Microsoft_365_Copilot": 10, "PROJECTPROFESSIONAL": 1, "EXCHANGESTANDARD": 5,
             "EMS": 0, "AAD_PREMIUM": 0, "AAD_PREMIUM_P2": 0, "POWER_BI_STANDARD": 0,
             "PROJECTPREMIUM": 2}
    skus = []
    for part, spare in extra.items():
        sid = _sku_id(part)
        used = consumed.get(sid, 0)
        enabled = used + spare if part != "POWER_BI_STANDARD" else 1000000
        skus.append({
            "skuId": sid,
            "skuPartNumber": part,
            "consumedUnits": used,
            "capabilityStatus": "Enabled",
            "appliesTo": "User",
            "prepaidUnits": {"enabled": enabled, "suspended": 0, "warning": 0},
        })

    renewals = {
        "SPE_E3": (41, False), "SPE_E5": (41, False), "SPE_F1": (41, False),
        "Microsoft_365_Copilot": (24, True), "POWER_BI_PRO": (182, False), "VISIOCLIENT": (73, False),
        "PROJECTPROFESSIONAL": (120, False), "EXCHANGESTANDARD": (230, False), "PROJECTPREMIUM": (19, False),
    }
    subs = []
    for part, (days, trial) in renewals.items():
        sku = next(s for s in skus if s["skuPartNumber"] == part)
        subs.append({
            "skuId": sku["skuId"],
            "skuPartNumber": part,
            "totalLicenses": sku["prepaidUnits"]["enabled"],
            "status": "Enabled",
            "isTrial": trial,
            "nextLifecycleDateTime": iso(now + timedelta(days=days)),
            "createdDateTime": iso(now - timedelta(days=365 - days)),
        })
    org = {"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, DOMAIN)), "displayName": "Northwind Care Group (demo)"}
    return org, skus, subs, users


def collect_demo(ctx: Context) -> dict:
    org, skus, subs, users = build_raw()
    data = normalise(org, skus, subs, users, signin_known=True, warnings=[])
    data["counts"]["graph_calls"] = 0
    return data


DEMO_SAAS = [
    # vendor, product, category, owner, seats, used, monthly cost (total), cycle, renewal days, notice days
    ("Zoom", "Zoom Workplace Business", "Video conferencing", "IT", 60, 23, 690.0, "annual", 64, 30),
    ("Cisco", "Webex Meetings", "Video conferencing", "Clinical Operations", 25, 9, 300.0, "annual", 150, 60),
    ("Slack", "Slack Pro", "Team chat", "Sales", 40, 31, 230.0, "monthly", 12, 0),
    ("Dropbox", "Dropbox Business", "File storage", "Marketing", 30, 12, 405.0, "annual", 38, 30),
    ("Box", "Box Business", "File storage", "", 15, 14, 270.0, "annual", 260, 30),
    ("Adobe", "Acrobat Pro", "PDF editing", "Finance", 35, 33, 595.0, "annual", 200, 30),
    ("DocuSign", "DocuSign Business Pro", "E-signature", "HR", 10, 4, 400.0, "annual", 28, 30),
    ("Adobe", "Acrobat Sign", "E-signature", "Finance", 5, 5, 175.0, "annual", 310, 30),
    ("Atlassian", "Jira Software Standard", "Project tracking", "IT", 50, 44, 410.0, "monthly", 9, 0),
    ("monday.com", "monday work management", "Project tracking", "Marketing", 20, 7, 320.0, "annual", 55, 30),
    ("Salesforce", "Sales Cloud Enterprise", "CRM", "Sales", 30, 29, 4200.0, "annual", 95, 90),
    ("Mimecast", "Email Security", "Email security", "IT", 240, 236, 980.0, "annual", 47, 90),
    ("Miro", "Miro Business", "Whiteboarding", "", 20, 6, 300.0, "annual", 170, 30),
]


def seed_saas(ctx: Context) -> None:
    if ctx.db.scalar("SELECT COUNT(*) FROM saas_apps", (), 0):
        return
    now = utcnow()
    with ctx.db.conn() as con:
        for vendor, product, cat, owner, seats, used, cost, cycle, renew, notice in DEMO_SAAS:
            con.execute(
                """INSERT INTO saas_apps (vendor, product, category, owner, seats_purchased, seats_used,
                monthly_cost, billing_cycle, renewal_date, notice_days, notes, updated_at, updated_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (vendor, product, cat, owner or None, seats, used, cost, cycle,
                 (now + timedelta(days=renew)).date().isoformat(), notice, "Demo record", iso(now), "demo"),
            )
