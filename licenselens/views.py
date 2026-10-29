"""LicenseLens pages: dashboard, licences, users, SaaS register and pricing."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from .catalog import DEFAULT_PRICES, FREE_SKUS, sku_name, sku_price
from .core.auth import current_user, require_admin_form, require_user
from .core.context import Context
from .core.db import iso, parse_dt, utcnow
from .core.web import _csv_safe, page_render, parse_date_field, redirect
from .rules import RULES, paid_skus, user_cost

SAAS_FIELDS = ["vendor", "product", "category", "owner", "seats_purchased", "seats_used", "monthly_cost",
               "billing_cycle", "renewal_date", "notice_days", "notes"]
# Rules whose saving is "remove every licence from this user"; overlap savings are then moot.
FULL_COST_RULES = ("LIC-001", "LIC-002", "LIC-003", "LIC-006")


def deduplicated_waste(ctx: Context) -> dict:
    """Waste without double counting. Consolidation opportunities (SAAS-003) are excluded."""
    rows = ctx.db.query(
        "SELECT rule_id, entity_type, entity_id, monthly_cost FROM findings "
        "WHERE status IN ('open','acknowledged') AND monthly_cost > 0"
    )
    full: dict[str, float] = defaultdict(float)
    partial: dict[str, float] = defaultdict(float)
    by_rule: dict[str, float] = defaultdict(float)
    other = 0.0
    for r in rows:
        by_rule[r["rule_id"]] += r["monthly_cost"]
        if r["entity_type"] == "user":
            if r["rule_id"] in FULL_COST_RULES:
                full[r["entity_id"]] = max(full[r["entity_id"]], r["monthly_cost"])
            else:
                partial[r["entity_id"]] += r["monthly_cost"]
        elif r["rule_id"] != "SAAS-003":
            other += r["monthly_cost"]
    users_total = sum(full.get(u) or partial.get(u, 0.0) for u in set(full) | set(partial))
    total = users_total + other
    return {"total": total, "by_rule": dict(by_rule), "users": users_total, "other": other}


def dashboard(ctx: Context) -> dict:
    _, data = ctx.latest()
    settings = ctx.settings.all()
    prices = settings["sku_prices"]
    waste = deduplicated_waste(ctx)
    skus = [s for s in data.get("skus", []) if sku_price(s["partNumber"], prices) > 0]
    purchased = sum(s["enabled"] for s in skus)
    assigned = sum(s["consumed"] for s in skus)
    spend = sum(s["enabled"] * sku_price(s["partNumber"], prices) for s in skus)
    spend += sum(float(a.get("monthly_cost") or 0) for a in data.get("saas", []))
    by_rule = sorted(
        ((f"{rid} · {RULES[rid].title}", v) for rid, v in waste["by_rule"].items() if rid in RULES and rid != "SAAS-003"),
        key=lambda x: -x[1],
    )
    util = sorted(
        ((s["name"], s["enabled"] - s["consumed"], "bar-warn") for s in skus if s["enabled"] > s["consumed"]),
        key=lambda x: -x[1],
    )[:8]
    # waste by department (user findings, de-duplicated per user)
    dept_cost: dict[str, float] = defaultdict(float)
    users = {u["id"]: u for u in data.get("users", [])}
    full: dict[str, float] = defaultdict(float)
    partial: dict[str, float] = defaultdict(float)
    for r in ctx.db.query(
        "SELECT entity_id, rule_id, monthly_cost FROM findings WHERE status IN ('open','acknowledged') "
        "AND entity_type='user'"
    ):
        if r["rule_id"] in FULL_COST_RULES:
            full[r["entity_id"]] = max(full[r["entity_id"]], r["monthly_cost"])
        else:
            partial[r["entity_id"]] += r["monthly_cost"]
    for uid in set(full) | set(partial):
        dept_cost[users.get(uid, {}).get("department", "Unknown")] += full.get(uid) or partial.get(uid, 0.0)
    by_dept = sorted(dept_cost.items(), key=lambda x: -x[1])[:8]

    # renewals in the next six months by month (Microsoft + SaaS register)
    now = utcnow()
    months: dict[str, float] = {}
    for i in range(6):
        m = (now.replace(day=1) + timedelta(days=32 * i)).replace(day=1)
        months[m.strftime("%b %y")] = 0.0
    for sub in data.get("subscriptions", []):
        when = parse_dt(sub.get("nextLifecycleDateTime"))
        key = when.strftime("%b %y") if when else None
        if key in months:
            months[key] += sku_price(sub["partNumber"], prices) * sub["totalLicenses"] * 12
    for a in data.get("saas", []):
        when = parse_dt(a.get("renewal_date"))
        key = when.strftime("%b %y") if when else None
        if key in months:
            months[key] += float(a.get("monthly_cost") or 0) * (12 if a.get("billing_cycle") == "annual" else 1)
    top, _ = ctx.findings.search(status="active", sort="cost", limit=20)
    top = [f for f in top if f["rule_id"] != "SAAS-003" and f["monthly_cost"] > 0][:10]
    renewals, _ = ctx.findings.search(status="active", rule_prefix="", sort="due", limit=200)
    renewals = [r for r in renewals if r["rule_id"] in ("LIC-007", "SAAS-002")][:6]
    inactive = ctx.db.scalar(
        "SELECT COUNT(DISTINCT entity_id) FROM findings WHERE status IN ('open','acknowledged') "
        "AND rule_id IN ('LIC-001','LIC-002','LIC-003')", (), 0,
    )
    return {
        "waste": waste,
        "purchased": purchased,
        "assigned": assigned,
        "spend": spend,
        "waste_by_rule": by_rule,
        "unused_by_sku": util,
        "waste_by_dept": by_dept,
        "renewal_months": list(months.items()),
        "top": top,
        "renewals": renewals,
        "inactive_users": inactive,
        "tenant": data.get("tenant", {}),
        "saas_count": len(data.get("saas", [])),
    }


def entity_link(f: dict) -> str | None:
    if f["entity_type"] == "user":
        return f"/users?q={f['details'].get('upn', '')}"
    if f["entity_type"] == "saas_app":
        return f"/saas/{f['entity_id']}"
    if f["entity_type"] in ("sku", "subscription"):
        return "/licences"
    return None


def report_context(ctx: Context) -> dict:
    return {"waste": deduplicated_waste(ctx)}


def _saas_from_form(form) -> tuple[dict, list[str]]:
    errors: list[str] = []
    rec = {k: str(form.get(k, "")).strip() for k in SAAS_FIELDS}
    for k in ("vendor", "product"):
        if not rec[k]:
            errors.append(f"{k.capitalize()} is required.")
    for k in ("seats_purchased", "seats_used", "notice_days"):
        try:
            rec[k] = max(0, int(float(rec[k] or 0)))
        except ValueError:
            errors.append(f"{k.replace('_', ' ').capitalize()} must be a whole number.")
            rec[k] = 0
    try:
        rec["monthly_cost"] = max(0.0, float(str(rec["monthly_cost"]).replace(",", "").lstrip("£$€") or 0))
    except ValueError:
        errors.append("Monthly cost must be a number.")
        rec["monthly_cost"] = 0.0
    if rec["billing_cycle"] not in ("monthly", "annual"):
        rec["billing_cycle"] = "annual"
    raw_date = rec["renewal_date"]
    rec["renewal_date"] = parse_date_field(raw_date)
    if raw_date and not rec["renewal_date"]:
        errors.append("Renewal date must be YYYY-MM-DD.")
    if rec["seats_used"] > rec["seats_purchased"] and rec["seats_purchased"]:
        errors.append("Seats used cannot exceed seats purchased.")
    for k in ("vendor", "product", "category", "owner"):
        rec[k] = rec[k][:120]
    rec["notes"] = rec["notes"][:1000]
    rec["owner"] = rec["owner"] or None
    return rec, errors


def register_routes(app: FastAPI, ctx: Context) -> None:
    render = page_render(ctx)
    db = ctx.db

    @app.get("/licences", response_class=HTMLResponse)
    async def licences(request: Request, user=Depends(require_user)):
        _, data = ctx.latest()
        prices = ctx.settings.get("sku_prices")
        subs = defaultdict(list)
        for s in data.get("subscriptions", []):
            subs[s["partNumber"]].append(s)
        rows = []
        for s in data.get("skus", []):
            price = sku_price(s["partNumber"], prices)
            renew = sorted(filter(None, (x.get("nextLifecycleDateTime") for x in subs[s["partNumber"]])))
            rows.append({
                **s,
                "price": price,
                "free": s["partNumber"] in FREE_SKUS or s["enabled"] >= 100000,
                "unused": max(0, s["enabled"] - s["consumed"]),
                "monthly": price * s["enabled"],
                "unused_cost": price * max(0, s["enabled"] - s["consumed"]),
                "renewal": renew[0] if renew else None,
                "trial": any(x.get("isTrial") for x in subs[s["partNumber"]]),
            })
        rows.sort(key=lambda r: (r["free"], -r["monthly"]))
        return render(request, "licences.html", rows=rows, title="Licences")

    @app.get("/users", response_class=HTMLResponse)
    async def users(request: Request, filter: str = "all", q: str = "", page: int = 1, user=Depends(require_user)):
        _, data = ctx.latest()
        prices = ctx.settings.get("sku_prices")
        flagged: dict[str, list[str]] = defaultdict(list)
        for r in db.query(
            "SELECT entity_id, rule_id FROM findings WHERE status IN ('open','acknowledged') AND entity_type='user'"
        ):
            flagged[r["entity_id"]].append(r["rule_id"])
        rows = []
        q_l = q.strip().lower()
        for u in data.get("users", []):
            rules = flagged.get(u["id"], [])
            if filter == "flagged" and not rules:
                continue
            if filter == "disabled" and u["enabled"]:
                continue
            if filter == "inactive" and not {"LIC-002", "LIC-003"} & set(rules):
                continue
            if filter == "guests" and u["userType"] != "Guest":
                continue
            if filter == "overlap" and "LIC-004" not in rules:
                continue
            if q_l and q_l not in u["upn"].lower() and q_l not in (u["displayName"] or "").lower() \
                    and q_l not in u["department"].lower():
                continue
            rows.append({**u, "rules": rules, "cost": user_cost(u, prices),
                         "paid": [sku_name(s) for s in paid_skus(u, prices)]})
        rows.sort(key=lambda r: (not r["rules"], -r["cost"], r["displayName"] or ""))
        total_cost = sum(r["cost"] for r in rows)
        per_page = 100
        pages = max(1, -(-len(rows) // per_page))
        page = min(max(page, 1), pages)
        qs = urlencode({k: v for k, v in (("filter", filter), ("q", q)) if v})
        return render(request, "users.html", rows=rows[(page - 1) * per_page: page * per_page], total=len(rows),
                      filter=filter, q=q, total_cost=total_cost, page=page, pages=pages, qs=qs, title="Users")

    # ------------------------------------------------------------ SaaS register
    @app.get("/saas", response_class=HTMLResponse)
    async def saas_list(request: Request, user=Depends(require_user)):
        apps = db.query("SELECT * FROM saas_apps ORDER BY monthly_cost DESC")
        flagged = defaultdict(list)
        for r in db.query(
            "SELECT entity_id, rule_id FROM findings WHERE status IN ('open','acknowledged') AND entity_type='saas_app'"
        ):
            flagged[r["entity_id"]].append(r["rule_id"])
        for a in apps:
            a["rules"] = flagged.get(str(a["id"]), [])
        total = sum(a["monthly_cost"] or 0 for a in apps)
        return render(request, "saas.html", apps=apps, total=total, title="SaaS register")

    @app.get("/saas/new", response_class=HTMLResponse)
    async def saas_new(request: Request, user=Depends(require_user)):
        return render(request, "saas_form.html", app_rec={"billing_cycle": "annual", "notice_days": 30},
                      errors=[], title="Add SaaS subscription")

    @app.get("/saas/{app_id}", response_class=HTMLResponse)
    async def saas_edit(request: Request, app_id: int, user=Depends(require_user)):
        rec = db.one("SELECT * FROM saas_apps WHERE id = ?", (app_id,))
        if not rec:
            return render(request, "error.html", status_code=404, title="Not found", message="No such subscription.")
        findings, _ = ctx.findings.search(status="active", entity_type="saas_app", entity_id=str(app_id))
        return render(request, "saas_form.html", app_rec=rec, errors=[], findings=findings, title=rec["product"])

    @app.post("/saas/save")
    async def saas_save(request: Request, form=Depends(require_admin_form)):
        actor = current_user(request)["name"]
        rec, errors = _saas_from_form(form)
        app_id = str(form.get("id", "")).strip()
        if errors:
            rec["id"] = int(app_id) if app_id.isdigit() else None
            return render(request, "saas_form.html", status_code=400, app_rec=rec, errors=errors, title="Fix errors")
        values = [rec[k] for k in SAAS_FIELDS] + [iso(utcnow()), actor]
        if app_id.isdigit():
            db.execute(
                "UPDATE saas_apps SET " + ", ".join(f"{k}=?" for k in SAAS_FIELDS)
                + ", updated_at=?, updated_by=? WHERE id=?", [*values, int(app_id)],
            )
            db.audit(actor, "saas.update", f"saas:{app_id}", f"{rec['vendor']} {rec['product']}")
        else:
            app_id = str(db.execute(
                "INSERT INTO saas_apps (" + ", ".join(SAAS_FIELDS) + ", updated_at, updated_by) VALUES ("
                + ", ".join("?" * (len(SAAS_FIELDS) + 2)) + ")", values,
            ))
            db.audit(actor, "saas.create", f"saas:{app_id}", f"{rec['vendor']} {rec['product']}")
        request.session.setdefault("flash", []).append(["success", "Saved. Run a scan to refresh findings."])
        return redirect("/saas")

    @app.post("/saas/{app_id}/delete")
    async def saas_delete(request: Request, app_id: int, form=Depends(require_admin_form)):
        actor = current_user(request)["name"]
        rec = db.one("SELECT vendor, product FROM saas_apps WHERE id = ?", (app_id,))
        if rec:
            db.execute("DELETE FROM saas_apps WHERE id = ?", (app_id,))
            db.audit(actor, "saas.delete", f"saas:{app_id}", f"{rec['vendor']} {rec['product']}")
            request.session.setdefault("flash", []).append(["success", "Deleted. Its findings resolve on the next scan."])
        return redirect("/saas")

    @app.get("/saas.csv")
    async def saas_export(request: Request, user=Depends(require_user)):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(SAAS_FIELDS)
        for a in db.query("SELECT * FROM saas_apps ORDER BY vendor, product"):
            w.writerow([_csv_safe(a[k]) if a[k] is not None else "" for k in SAAS_FIELDS])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="saas-register.csv"'})

    @app.post("/saas/import")
    async def saas_import(request: Request, file: UploadFile, form=Depends(require_admin_form)):
        actor = current_user(request)["name"]
        raw = (await file.read(2_000_001))
        if len(raw) > 2_000_000:
            request.session.setdefault("flash", []).append(["error", "File too large (2 MB max)."])
            return redirect("/saas")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        reader = csv.DictReader(io.StringIO(text))
        added, problems = 0, []
        for i, row in enumerate(reader, start=2):
            norm = {(k or "").strip().lower().replace(" ", "_"): (v or "") for k, v in row.items()}
            rec, errors = _saas_from_form(norm)
            if errors:
                problems.append(f"row {i}: {errors[0]}")
                continue
            db.execute(
                "INSERT INTO saas_apps (" + ", ".join(SAAS_FIELDS) + ", updated_at, updated_by) VALUES ("
                + ", ".join("?" * (len(SAAS_FIELDS) + 2)) + ")",
                [rec[k] for k in SAAS_FIELDS] + [iso(utcnow()), actor],
            )
            added += 1
            if added >= 2000:
                break
        db.audit(actor, "saas.import", "saas", f"{added} rows added, {len(problems)} skipped")
        flash = request.session.setdefault("flash", [])
        flash.append(["success" if added else "error", f"Imported {added} subscription(s)."])
        if problems:
            flash.append(["error", "Skipped " + "; ".join(problems[:5]) + (" …" if len(problems) > 5 else "")])
        return redirect("/saas")

    # ------------------------------------------------------------------ pricing
    @app.get("/pricing", response_class=HTMLResponse)
    async def pricing(request: Request, user=Depends(require_user)):
        _, data = ctx.latest()
        prices = ctx.settings.get("sku_prices")
        parts = sorted({s["partNumber"] for s in data.get("skus", [])} | set(prices) | set(DEFAULT_PRICES))
        tenant_parts = {s["partNumber"] for s in data.get("skus", [])}
        rows = [{"part": p, "name": sku_name(p), "price": prices.get(p, 0.0), "in_tenant": p in tenant_parts,
                 "free": p in FREE_SKUS, "default": DEFAULT_PRICES.get(p)} for p in parts]
        rows.sort(key=lambda r: (not r["in_tenant"], r["free"], r["name"]))
        return render(request, "pricing.html", rows=rows, title="Pricing")

    @app.post("/pricing")
    async def pricing_save(request: Request, form=Depends(require_admin_form)):
        actor = current_user(request)["name"]
        prices: dict[str, float] = {}
        for key, value in form.multi_items():
            if not key.startswith("price__"):
                continue
            part = key.removeprefix("price__")[:80]
            try:
                v = float(str(value).replace(",", "").lstrip("£$€") or 0)
            except ValueError:
                continue
            if v > 0:
                prices[part] = round(min(v, 100000.0), 2)
        ctx.settings.set_many({"sku_prices": prices}, actor)
        request.session.setdefault("flash", []).append(["success", "Prices saved. Run a scan to recalculate savings."])
        return redirect("/pricing")
