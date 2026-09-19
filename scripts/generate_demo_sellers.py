"""
Generates a demo `data/sellers.csv` for local development.

Why this exists: `core/config.py`'s `data_source_kind` defaults to "sql",
which reads sellers from a marketplace system-of-record view
(`vw_sellers_canonical`) that only exists in a real deployment — no
migration creates it, and none should, since it's owned by an external
system in production. On a fresh local clone (SQLite, no marketplace DB
to point at), that means GET /api/v1/sellers and GET /api/v1/prioritization
fail immediately with "no such table: vw_sellers_canonical" — there was
previously no way to exercise either endpoint locally at all.

`services/data_source.py`'s CSVSellerDataSource exists specifically for
this dev/demo case, but nothing ever generated the CSV it reads from
(`data/sellers.csv`, per `seller_csv_path`'s default) or pointed
`DATA_SOURCE_KIND=csv` at it from `.env.example`. This script closes that
gap: run it once, set `DATA_SOURCE_KIND=csv` (see .env.example), and the
seller/prioritization endpoints work against realistic synthetic data with
no external database required.

Run: python3 scripts/generate_demo_sellers.py
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "sellers.csv"
N_SELLERS = 250
RNG = np.random.default_rng(7)

CATEGORIES = ["Electronics", "Home & Kitchen", "Apparel", "Toys", "Books",
              "Beauty", "Sports & Outdoors", "Grocery", "Automotive", "Office Supplies"]
COUNTRIES_STATES = [
    ("US", "CA"), ("US", "NY"), ("US", "TX"), ("US", "FL"), ("US", "WA"),
    ("US", "IL"), ("CA", "ON"), ("CA", "BC"), ("GB", "ENG"), ("DE", "BY"),
]
TIERS = ["standard", "preferred", "premium"]
FIRST_WORDS = ["Summit", "Blue", "River", "North", "Cedar", "Golden", "Silver",
               "Urban", "Coastal", "Prime", "Bright", "Metro", "Union", "Apex"]
SECOND_WORDS = ["Trading", "Goods", "Supply", "Works", "Market", "Collective",
                "Outfitters", "Depot", "Traders", "Mercantile", "Group", "Co"]


def _company_name(rng: np.random.Generator, idx: int) -> str:
    return f"{rng.choice(FIRST_WORDS)} {rng.choice(SECOND_WORDS)} #{idx}"


def generate_rows(n: int) -> list[dict]:
    rows = []
    for i in range(1, n + 1):
        country, state = COUNTRIES_STATES[RNG.integers(0, len(COUNTRIES_STATES))]
        category = CATEGORIES[RNG.integers(0, len(CATEGORIES))]
        seller_age_months = int(RNG.integers(1, 96))
        monthly_revenue = float(round(max(500.0, RNG.lognormal(mean=9.0, sigma=1.0)), 2))
        orders = int(max(1, RNG.poisson(lam=max(monthly_revenue / 80, 1))))
        avg_order_value = round(monthly_revenue / orders, 2) if orders else 0.0
        return_rate = float(round(np.clip(RNG.beta(2, 20), 0, 0.6), 4))
        customer_rating = float(round(np.clip(RNG.normal(4.3, 0.5), 1.0, 5.0), 2))
        late_shipment_pct = float(round(np.clip(RNG.beta(2, 25), 0, 0.5), 4))
        cancellation_pct = float(round(np.clip(RNG.beta(1.5, 30), 0, 0.4), 4))
        ad_spend = float(round(max(0.0, RNG.lognormal(mean=6.0, sigma=1.2)), 2))
        conversion_rate = float(round(np.clip(RNG.beta(2, 40), 0.001, 0.15), 4))
        growth_rate = float(round(RNG.normal(0.02, 0.08), 4))
        repeat_customer_pct = float(round(np.clip(RNG.beta(3, 5), 0, 1), 4))
        support_tickets = int(max(0, RNG.poisson(lam=1.5)))
        tier = TIERS[RNG.integers(0, len(TIERS))]

        rows.append({
            "seller_id": f"SLR-{i:05d}",
            "company_name": _company_name(RNG, i),
            "category": category,
            "country": country,
            "state": state,
            "monthly_revenue": monthly_revenue,
            "orders": orders,
            "avg_order_value": avg_order_value,
            "return_rate": return_rate,
            "customer_rating": customer_rating,
            "late_shipment_pct": late_shipment_pct,
            "cancellation_pct": cancellation_pct,
            "ad_spend": ad_spend,
            "conversion_rate": conversion_rate,
            "seller_age_months": seller_age_months,
            "growth_rate": growth_rate,
            "repeat_customer_pct": repeat_customer_pct,
            "account_manager": "",
            "support_tickets": support_tickets,
            "seller_tier": tier,
        })
    return rows


def main() -> None:
    rows = generate_rows(N_SELLERS)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} demo sellers to {OUT_PATH}")
    print("Set DATA_SOURCE_KIND=csv in .env to use this locally "
          "(see .env.example) — sql (the default) requires a real "
          "marketplace database view and will not work against SQLite.")


if __name__ == "__main__":
    main()
