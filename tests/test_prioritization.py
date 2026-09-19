"""
Tests for services/prioritization_v2.py — threshold resolution precedence
and the "no thresholds configured yet" fallback path.

The fallback-path test is a regression test for a real bug found during
manual end-to-end verification: `_resolve_thresholds`'s ultimate fallback
constructed `CategoryThreshold(tenant_id=tenant_id)` and relied on
SQLAlchemy's `mapped_column(default=...)` to populate every other field.
That default is only applied at flush/INSERT time — never to a bare,
unflushed instance — so every weight/threshold field was actually `None`,
and `prioritize()` crashed with a TypeError the moment it tried to
multiply a None weight by a float. This is the code path every brand-new
tenant hits (any tenant that hasn't called PUT /admin/thresholds yet), so
it was a guaranteed, always-reproducible 500 on GET /prioritization for
new tenants specifically — exactly the case with no fixture/seed data,
which is why it had gone uncaught.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.tenancy import tenant_scope
from models.config import CategoryThreshold
from services.data_source import SellerRecord
from services.prioritization_v2 import _resolve_thresholds, prioritize


def _make_seller(**overrides) -> SellerRecord:
    defaults = dict(
        seller_id="SLR-1", tenant_id="t1", company_name="Acme Co", category="Electronics",
        country="US", region="CA", monthly_revenue=10_000.0, orders=100, avg_order_value=100.0,
        return_rate=0.05, customer_rating=4.5, late_shipment_pct=0.02, cancellation_pct=0.01,
        ad_spend=500.0, conversion_rate=0.03, seller_age_months=12, growth_rate=0.02,
        repeat_customer_pct=0.4, account_manager_id=None, last_contact_date=None,
        support_tickets=1, seller_tier="standard",
    )
    defaults.update(overrides)
    return SellerRecord(**defaults)


@dataclass
class _FakeSellerSource:
    """Minimal in-memory SellerDataSource stub — avoids any file I/O or DB
    dependency for a fast, focused unit test."""
    sellers: list

    def list_sellers(self, tenant_id, *, limit=None, since=None):
        sellers = self.sellers
        if limit is not None:
            sellers = sellers[:limit]
        return iter(sellers)

    def get_seller(self, tenant_id, seller_id):
        return next((s for s in self.sellers if s.seller_id == seller_id), None)

    def health_check(self) -> bool:
        return True


def test_resolve_thresholds_fallback_has_real_values_not_none(db, tenant_and_user):
    """Regression test: a tenant with zero CategoryThreshold rows must still
    get a fully-populated fallback, not an object full of Nones."""
    tenant_id, _, _, _ = tenant_and_user
    with tenant_scope(tenant_id, "system", ("system",)):
        t = _resolve_thresholds(db, tenant_id, category="Electronics", region="CA")
    assert isinstance(t, CategoryThreshold)
    assert t.weight_revenue is not None
    assert t.weight_growth is not None
    assert t.weight_returns is not None
    assert t.weight_shipping is not None
    assert t.weight_rating is not None
    assert t.weight_conversion is not None
    assert t.weight_support is not None
    assert t.high_return_rate is not None
    assert t.declining_growth_rate is not None
    assert t.late_shipment_threshold is not None
    assert t.low_rating_threshold is not None
    # Weights should sum to ~1.0 so scores land on a 0-100 scale.
    total_weight = (
        t.weight_revenue + t.weight_growth + t.weight_returns
        + t.weight_shipping + t.weight_rating + t.weight_conversion + t.weight_support
    )
    assert total_weight == pytest.approx(1.0, abs=0.01)


def test_prioritize_works_for_tenant_with_no_configured_thresholds(db, tenant_and_user):
    """End-to-end regression test for the actual crash: prioritize() must
    not raise for a brand-new tenant that has never configured thresholds —
    this is the default state of every tenant immediately after signup."""
    tenant_id, user_id, _, _ = tenant_and_user
    sellers = [_make_seller(seller_id=f"SLR-{i}", tenant_id=tenant_id) for i in range(3)]
    source = _FakeSellerSource(sellers=sellers)

    with tenant_scope(tenant_id, user_id, ("account_manager",)):
        results = prioritize(db, source, tenant_id, user_id, persist_recommendations=False)
        db.commit()

    assert len(results) == 3
    for r in results:
        assert 0.0 <= r.score <= 100.0
        assert r.tier in ("excellent", "healthy", "at_risk", "critical")


def test_resolve_thresholds_precedence(db, tenant_and_user):
    """(category, region) beats (category, None) beats (None, None)."""
    tenant_id, _, _, _ = tenant_and_user
    with tenant_scope(tenant_id, "system", ("system",)):
        db.add(CategoryThreshold(tenant_id=tenant_id, category=None, region=None, weight_revenue=0.99))
        db.add(CategoryThreshold(tenant_id=tenant_id, category="Electronics", region=None, weight_revenue=0.50))
        db.add(CategoryThreshold(tenant_id=tenant_id, category="Electronics", region="CA", weight_revenue=0.11))
        db.commit()

        most_specific = _resolve_thresholds(db, tenant_id, "Electronics", "CA")
        category_only = _resolve_thresholds(db, tenant_id, "Electronics", "TX")
        tenant_default = _resolve_thresholds(db, tenant_id, "Books", "TX")

    assert most_specific.weight_revenue == pytest.approx(0.11)
    assert category_only.weight_revenue == pytest.approx(0.50)
    assert tenant_default.weight_revenue == pytest.approx(0.99)
