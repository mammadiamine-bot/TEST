"""Subscriptions, licenses, usage and invoice drafts. Plan changes, licenses and invoices are owner-only (tenancy.OWNER_ONLY_WRITE)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .billing import PLANS, Billing
from .security import require_token


class PlanIn(BaseModel):
    plan: str
    days: int = Field(default=30, ge=1, le=3650)


class LicenseIn(BaseModel):
    plan: str
    days: int = Field(default=30, ge=1, le=3650)


class ActivateIn(BaseModel):
    license: str = Field(min_length=20, max_length=2000)


class InvoiceIn(BaseModel):
    month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


def build_router(core, billing: Billing) -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])
    db = core.db

    def need_org(oid: str) -> None:
        if not db.one("SELECT 1 AS x FROM orgs WHERE id=?", (oid,)):
            raise HTTPException(404, "unknown organization")

    @r.get("/plans")
    def plans():
        return PLANS

    @r.get("/orgs/{oid}/subscription")
    def get(oid: str):
        need_org(oid)
        return billing.current(oid)

    @r.put("/orgs/{oid}/subscription")
    def put(oid: str, b: PlanIn, request: Request):
        need_org(oid)
        return billing.set_plan(oid, b.plan, request.state.principal.id, b.days)

    @r.get("/orgs/{oid}/subscription/history")
    def history(oid: str):
        need_org(oid)
        return db.all("SELECT ts,actor,change FROM subscription_history WHERE org_id=? ORDER BY id DESC", (oid,))

    @r.post("/orgs/{oid}/subscription/license", status_code=201)
    def license_(oid: str, b: LicenseIn, request: Request):
        need_org(oid)
        return {"license": billing.issue_license(oid, b.plan, b.days, request.state.principal.id), "note": "bound to this client; single use"}

    @r.post("/orgs/{oid}/subscription")
    def activate(oid: str, b: ActivateIn, request: Request):
        need_org(oid)
        return billing.activate(oid, b.license, request.state.principal.id)

    @r.get("/orgs/{oid}/invoice-data")
    def invoice_data(oid: str, month: str | None = None):
        need_org(oid)
        return billing.invoice_data(oid, month)

    @r.post("/orgs/{oid}/invoices", status_code=201)
    def invoice(oid: str, b: InvoiceIn, request: Request):
        need_org(oid)
        return billing.save_invoice(oid, b.month, request.state.principal.id)

    @r.get("/orgs/{oid}/invoices")
    def invoices(oid: str):
        need_org(oid)
        return billing.invoices(oid)

    return r
