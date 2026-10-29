import io
from datetime import timedelta

import httpx

from licenselens.collector import collect_live
from licenselens.core.db import iso, utcnow
from licenselens.core.graph import GraphClient
from licenselens.demo import collect_demo
from licenselens.rules import analyse, analyse_saas
from licenselens.views import deduplicated_waste

from .conftest import csrf


def test_demo_triggers_every_licence_rule(ctx):
    data = collect_demo(ctx)
    rules = {f.rule_id for f in analyse(data, ctx.settings.all(), ctx)}
    assert {"LIC-001", "LIC-002", "LIC-003", "LIC-004", "LIC-005", "LIC-006", "LIC-007"} <= rules


def test_excluded_accounts_are_skipped(ctx):
    data = collect_demo(ctx)
    flagged = {f.details.get("upn") for f in analyse(data, ctx.settings.all(), ctx)}
    assert not any(u and u.startswith(("breakglass", "svc-")) for u in flagged)


def test_overlap_names_the_redundant_sku(ctx):
    data = collect_demo(ctx)
    overlaps = [f for f in analyse(data, ctx.settings.all(), ctx) if f.rule_id == "LIC-004"]
    assert any(f.key == "SPE_E3" and "Microsoft 365 E5" in f.title for f in overlaps)
    assert all(f.monthly_cost > 0 for f in overlaps)


def test_saas_rules():
    today = utcnow().date()
    apps = [
        {"id": 1, "vendor": "Zoom", "product": "Zoom Workplace", "category": "Video", "owner": "IT",
         "seats_purchased": 10, "seats_used": 2, "monthly_cost": 100, "renewal_date": (today + timedelta(days=40)).isoformat(),
         "notice_days": 30},
        {"id": 2, "vendor": "Cisco", "product": "Webex", "category": "video", "owner": "",
         "seats_purchased": 5, "seats_used": 5, "monthly_cost": 50, "renewal_date": None, "notice_days": 0},
    ]
    settings = {"saas_min_utilisation": 0.8, "saas_notice_window_days": 45}
    out = analyse_saas(apps, settings)
    rules = {f.rule_id: f for f in out}
    assert rules["SAAS-001"].monthly_cost == 80
    assert rules["SAAS-002"].severity == "high"  # notice due in 10 days
    assert rules["SAAS-003"].monthly_cost == 50
    assert rules["SAAS-004"].entity_id == "2"
    assert rules["SAAS-001"].entity_name == "Zoom Workplace"


def test_waste_is_not_double_counted(ctx):
    w = deduplicated_waste(ctx)
    gross = ctx.findings.summary()["monthly_cost"]
    assert 0 < w["total"] < gross


def _graph(handler):
    return GraphClient("tenant", "client", "secret", transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_live_collector_normalises_graph(ctx):
    sku = "6fd2c87f-b296-42f0-b197-1e91e994b900"
    now = utcnow()

    def handler(req: httpx.Request):
        path = req.url.path
        if path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if path.endswith("/organization"):
            return httpx.Response(200, json={"value": [{"id": "org", "displayName": "Contoso"}]})
        if path.endswith("/subscribedSkus"):
            return httpx.Response(200, json={"value": [{"skuId": sku, "skuPartNumber": "ENTERPRISEPACK", "consumedUnits": 1,
                                                        "capabilityStatus": "Enabled", "prepaidUnits": {"enabled": 5}}]})
        if path.endswith("/directory/subscriptions"):
            return httpx.Response(403, json={"error": {"code": "Forbidden", "message": "no"}})
        if path.endswith("/users"):
            assert "signInActivity" in req.url.params["$select"]
            return httpx.Response(200, json={"value": [
                {"id": "u1", "displayName": "Ann", "userPrincipalName": "ann@contoso.example", "accountEnabled": False,
                 "userType": "Member", "createdDateTime": iso(now - timedelta(days=400)),
                 "assignedLicenses": [{"skuId": sku}],
                 "signInActivity": {"lastSignInDateTime": iso(now - timedelta(days=200)),
                                    "lastNonInteractiveSignInDateTime": iso(now - timedelta(days=150))}},
                {"id": "u2", "displayName": "Unlicensed", "userPrincipalName": "x@contoso.example", "assignedLicenses": []},
            ]})
        return httpx.Response(404, json={"error": {"code": "NotFound", "message": path}})

    data = collect_live(ctx, client=_graph(handler))
    assert data["tenant"]["name"] == "Contoso"
    assert len(data["users"]) == 1
    u = data["users"][0]
    assert u["skus"] == ["ENTERPRISEPACK"] and not u["enabled"]
    assert u["lastActivity"].startswith((now - timedelta(days=150)).date().isoformat())
    assert any("renewal" in w.lower() for w in data["warnings"])
    data["saas"] = []
    rules = {f.rule_id for f in analyse(data, ctx.settings.all(), ctx)}
    assert {"LIC-001", "LIC-005"} <= rules


def test_live_collector_falls_back_without_signin_activity(ctx):
    def handler(req: httpx.Request):
        path = req.url.path
        if path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if path.endswith("/users"):
            if "signInActivity" in req.url.params["$select"]:
                return httpx.Response(403, json={"error": {"code": "Authentication_RequestFromNonPremiumTenant", "message": "P1"}})
            return httpx.Response(200, json={"value": []})
        return httpx.Response(200, json={"value": []})

    data = collect_live(ctx, client=_graph(handler))
    assert any("sign-in" in w.lower() for w in data["warnings"])


def test_saas_crud_and_import(admin, ctx):
    token = csrf(admin, "/saas/new")
    r = admin.post("/saas/save", data={"csrf_token": token, "vendor": "Acme", "product": "Acme CRM", "category": "CRM",
                                       "owner": "Sales", "seats_purchased": "10", "seats_used": "3",
                                       "monthly_cost": "250", "billing_cycle": "annual", "renewal_date": "2030-01-01",
                                       "notice_days": "30", "notes": ""}, follow_redirects=False)
    assert r.status_code == 303
    assert ctx.db.one("SELECT * FROM saas_apps WHERE product = 'Acme CRM'")["seats_used"] == 3
    bad = admin.post("/saas/save", data={"csrf_token": token, "vendor": "", "product": "", "seats_purchased": "1",
                                         "seats_used": "5", "monthly_cost": "x"})
    assert bad.status_code == 400
    csv_bytes = b"vendor,product,category,seats_purchased,seats_used,monthly_cost,renewal_date\nFoo,Foo Chat,Chat,5,1,40,2030-02-01\n"
    r = admin.post("/saas/import", data={"csrf_token": token},
                   files={"file": ("r.csv", io.BytesIO(csv_bytes), "text/csv")}, follow_redirects=False)
    assert r.status_code == 303
    assert ctx.db.one("SELECT * FROM saas_apps WHERE product = 'Foo Chat'")
    assert admin.get("/saas.csv").text.startswith("vendor,product")


def test_pricing_page_updates_prices(admin, ctx):
    token = csrf(admin, "/pricing")
    admin.post("/pricing", data={"csrf_token": token, "price__SPE_E3": "40.5"})
    assert ctx.settings.get("sku_prices") == {"SPE_E3": 40.5}
