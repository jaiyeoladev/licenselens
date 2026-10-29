"""LicenseLens application spec and ASGI entry point (`uvicorn licenselens.app:app`)."""

from __future__ import annotations

from pathlib import Path

from . import views
from .catalog import DEFAULT_PRICES, DEFAULT_SUPERSEDES
from .collector import GRAPH_PERMISSIONS, collect_live
from .core import AppSpec, Config, NavItem, SettingDef, create_app
from .core.context import Context
from .demo import collect_demo, seed_saas
from .rules import RULES, analyse

SCHEMA = """
CREATE TABLE IF NOT EXISTS saas_apps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor TEXT NOT NULL,
    product TEXT NOT NULL,
    category TEXT,
    owner TEXT,
    seats_purchased INTEGER NOT NULL DEFAULT 0,
    seats_used INTEGER NOT NULL DEFAULT 0,
    monthly_cost REAL NOT NULL DEFAULT 0,
    billing_cycle TEXT NOT NULL DEFAULT 'annual',
    renewal_date TEXT,
    notice_days INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    updated_at TEXT,
    updated_by TEXT
);
"""

SETTINGS = [
    SettingDef("inactive_days", "Inactive after (days)", "int", 90,
               "Enabled users with no interactive or non-interactive sign-in for longer than this are flagged.",
               "Microsoft 365 users", min=14, max=730),
    SettingDef("never_signed_in_grace_days", "Never-signed-in grace period (days)", "int", 30,
               "New accounts are not flagged until they are at least this old.", "Microsoft 365 users", min=0, max=365),
    SettingDef("excluded_upns", "Excluded accounts", "textarea", "breakglass*@*\nsvc-*@*",
               "One pattern per line (wildcards allowed). Break-glass and service accounts are typical exclusions.",
               "Microsoft 365 users"),
    SettingDef("min_unused_seats", "Flag unassigned seats from", "int", 1,
               "Minimum number of unassigned seats on a SKU before it is flagged.", "Seats & renewals", min=1, max=10000),
    SettingDef("unused_seat_medium_cost", "Unused-seat cost for medium severity", "float", 100,
               "Monthly cost of unassigned seats above which the finding is raised as medium.", "Seats & renewals",
               min=0),
    SettingDef("renewal_window_days", "Renewal window (days)", "int", 60,
               "Microsoft subscriptions renewing within this window are flagged for right-sizing.",
               "Seats & renewals", min=7, max=365),
    SettingDef("saas_min_utilisation", "SaaS utilisation target", "float", 0.8,
               "Register entries using fewer than this share of seats (0–1) are flagged.", "SaaS register",
               min=0.1, max=1.0),
    SettingDef("saas_notice_window_days", "Notice deadline warning (days)", "int", 45,
               "Warn this many days before the last date to give notice on a SaaS contract.", "SaaS register",
               min=1, max=365),
    SettingDef("supersedes", "Licence overlap map", "json", DEFAULT_SUPERSEDES,
               "Advanced: if a user holds the key SKU, each SKU in its list is redundant.", "Advanced"),
    SettingDef("sku_prices", "SKU prices", "json", DEFAULT_PRICES, "Edited on the Pricing page.", "Advanced",
               hidden=True),
]


def collect_demo_with_register(ctx: Context) -> dict:
    data = collect_demo(ctx)
    data["saas"] = ctx.db.query("SELECT * FROM saas_apps")
    data["counts"]["saas_apps"] = len(data["saas"])
    return data


def collect_live_with_register(ctx: Context) -> dict:
    data = collect_live(ctx)
    data["saas"] = ctx.db.query("SELECT * FROM saas_apps")
    data["counts"]["saas_apps"] = len(data["saas"])
    return data


def live_requirements(ctx: Context) -> list[str]:
    c = ctx.config
    return [n for n, v in (("AZURE_TENANT_ID", c.tenant_id), ("AZURE_CLIENT_ID", c.client_id),
                           ("AZURE_CLIENT_SECRET", c.client_secret)) if not v]


def on_startup(ctx: Context) -> None:
    if ctx.config.demo_mode:
        seed_saas(ctx)


SPEC = AppSpec(
    slug="licenselens",
    name="LicenseLens",
    tagline="Microsoft 365 licence and SaaS spend optimisation",
    description="Finds unused, overlapping and orphaned licences and tracks every SaaS renewal.",
    package_dir=Path(__file__).parent,
    nav=[NavItem("Licences", "/licences"), NavItem("Users", "/users"), NavItem("SaaS register", "/saas"),
         NavItem("Pricing", "/pricing")],
    rules=RULES,
    setting_defs=SETTINGS,
    collect_demo=collect_demo_with_register,
    collect_live=collect_live_with_register,
    analyse=analyse,
    dashboard=views.dashboard,
    register_routes=views.register_routes,
    live_requirements=live_requirements,
    schema_sql=SCHEMA,
    entity_link=views.entity_link,
    report_context=views.report_context,
    on_startup=on_startup,
    graph_permissions=GRAPH_PERMISSIONS,
)


def build(config: Config | None = None, run_initial_scan: bool = True):
    return create_app(SPEC, config or Config.from_env(SPEC.slug), run_initial_scan=run_initial_scan)


def __getattr__(name: str):
    # Lazily create the ASGI app so importing the package never needs configuration.
    if name == "app":
        global app
        app = build()
        return app
    raise AttributeError(name)
