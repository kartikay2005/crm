"""
Dataset Intelligence Engine — domain knowledge base.

Each domain is defined by:
- `signature_columns`: canonical feature names strongly associated with the
  domain (used for the "feature names" and "schema completeness" score
  components — this doubles as a lightweight stand-in for real per-model
  schemas until Segment 3's model registry exists; once that lands, schema
  completeness should be recomputed against actual registered model
  schemas rather than this generic list).
- `keywords`: a broader vocabulary of substrings that show up in column
  names for this domain, used for the looser "domain keywords" component.
- `known_ranges`: a few well-known fields with plausible numeric ranges,
  used for the "distribution similarity" component. Deliberately small and
  curated — this is a heuristic sanity check, not a statistical model.
- `expected_type_mix`: rough expected ratio of numerical vs categorical
  columns, used for the "data types" component.

This is intentionally data, not code, so adding a 21st domain or tuning an
existing one never requires touching the scoring logic in domain_detection.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DomainSpec:
    name: str
    signature_columns: tuple[str, ...]
    keywords: tuple[str, ...]
    known_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    expected_numerical_ratio: float = 0.6  # fraction of columns expected numerical


DOMAINS: dict[str, DomainSpec] = {
    "Healthcare": DomainSpec(
        name="Healthcare",
        signature_columns=("age", "blood pressure", "heart rate", "cholesterol", "bmi",
                            "diagnosis", "glucose", "weight", "height", "patient"),
        keywords=("patient", "diagnosis", "symptom", "treatment", "medication", "clinical",
                   "hospital", "doctor", "disease", "blood", "heart", "glucose", "insulin",
                   "tumor", "cancer", "medical"),
        known_ranges={"age": (0, 120), "blood pressure": (60, 220), "heart rate": (30, 220),
                      "cholesterol": (100, 400), "bmi": (10, 60)},
        expected_numerical_ratio=0.65,
    ),
    "Finance": DomainSpec(
        name="Finance",
        signature_columns=("income", "credit score", "loan amount", "debt", "interest rate",
                            "balance", "assets", "liabilities"),
        keywords=("loan", "credit", "debt", "income", "interest", "balance", "asset",
                   "liability", "mortgage", "bank", "account", "investment", "portfolio"),
        known_ranges={"credit score": (300, 850), "interest rate": (0, 40)},
        expected_numerical_ratio=0.8,
    ),
    "Insurance": DomainSpec(
        name="Insurance",
        signature_columns=("premium", "policy", "claim", "coverage", "deductible", "risk score"),
        keywords=("premium", "policy", "claim", "coverage", "deductible", "insured",
                   "underwriting", "actuarial", "beneficiary"),
        expected_numerical_ratio=0.6,
    ),
    "Retail": DomainSpec(
        name="Retail",
        signature_columns=("product", "revenue", "quantity", "profit", "price", "category",
                            "discount", "store"),
        keywords=("product", "revenue", "sale", "profit", "price", "discount", "store",
                   "sku", "inventory", "category", "brand"),
        expected_numerical_ratio=0.55,
    ),
    "E-commerce": DomainSpec(
        name="E-commerce",
        signature_columns=("order id", "cart", "checkout", "session", "conversion",
                            "customer id", "product id"),
        keywords=("cart", "checkout", "session", "conversion", "click", "impression",
                   "order", "shipping", "review", "rating", "wishlist"),
        expected_numerical_ratio=0.5,
    ),
    "HR": DomainSpec(
        name="HR",
        signature_columns=("employee id", "salary", "department", "tenure", "performance",
                            "attrition", "satisfaction"),
        keywords=("employee", "salary", "department", "attrition", "tenure", "performance",
                   "satisfaction", "hire", "manager", "promotion", "overtime"),
        expected_numerical_ratio=0.5,
    ),
    "Education": DomainSpec(
        name="Education",
        signature_columns=("student id", "grade", "score", "attendance", "gpa", "course"),
        keywords=("student", "grade", "score", "attendance", "gpa", "course", "exam",
                   "enrollment", "teacher", "school", "semester"),
        expected_numerical_ratio=0.5,
    ),
    "Agriculture": DomainSpec(
        name="Agriculture",
        signature_columns=("yield", "rainfall", "soil", "crop", "temperature", "irrigation"),
        keywords=("crop", "yield", "soil", "rainfall", "irrigation", "fertilizer",
                   "harvest", "farm", "livestock", "pesticide"),
        expected_numerical_ratio=0.7,
    ),
    "Manufacturing": DomainSpec(
        name="Manufacturing",
        signature_columns=("defect rate", "downtime", "cycle time", "throughput", "machine"),
        keywords=("defect", "downtime", "cycle time", "throughput", "machine", "assembly",
                   "production", "quality control", "yield rate", "maintenance"),
        expected_numerical_ratio=0.75,
    ),
    "Marketing": DomainSpec(
        name="Marketing",
        signature_columns=("campaign", "impressions", "clicks", "ctr", "conversion rate",
                            "spend", "roi"),
        keywords=("campaign", "impression", "click", "ctr", "conversion", "spend", "roi",
                   "engagement", "reach", "audience", "channel"),
        expected_numerical_ratio=0.6,
    ),
    "Customer Churn": DomainSpec(
        name="Customer Churn",
        signature_columns=("churn", "tenure", "contract", "monthly charges", "customer id"),
        keywords=("churn", "tenure", "contract", "subscription", "retention", "cancel",
                   "renewal", "loyalty"),
        expected_numerical_ratio=0.55,
    ),
    "Fraud Detection": DomainSpec(
        name="Fraud Detection",
        signature_columns=("transaction amount", "fraud", "merchant", "transaction id",
                            "is fraud"),
        keywords=("fraud", "transaction", "merchant", "chargeback", "suspicious",
                   "anomaly", "risk flag"),
        expected_numerical_ratio=0.65,
    ),
    "Real Estate": DomainSpec(
        name="Real Estate",
        signature_columns=("price", "square feet", "bedrooms", "bathrooms", "location",
                            "property type"),
        keywords=("property", "listing", "bedroom", "bathroom", "square feet", "sqft",
                   "zoning", "lot size", "appraisal", "mortgage"),
        expected_numerical_ratio=0.6,
    ),
    "Sales Forecasting": DomainSpec(
        name="Sales Forecasting",
        signature_columns=("date", "sales", "forecast", "region", "quarter"),
        keywords=("forecast", "sales", "quarter", "seasonality", "trend", "pipeline",
                   "quota", "target"),
        expected_numerical_ratio=0.6,
    ),
    "Inventory": DomainSpec(
        name="Inventory",
        signature_columns=("stock level", "reorder point", "warehouse", "sku", "lead time"),
        keywords=("stock", "reorder", "warehouse", "sku", "lead time", "backorder",
                   "shipment", "supplier"),
        expected_numerical_ratio=0.65,
    ),
    "Transportation": DomainSpec(
        name="Transportation",
        signature_columns=("distance", "route", "vehicle", "fuel", "delivery time"),
        keywords=("route", "vehicle", "fuel", "delivery", "shipment", "fleet", "driver",
                   "mileage", "logistics"),
        expected_numerical_ratio=0.6,
    ),
    "Energy": DomainSpec(
        name="Energy",
        signature_columns=("consumption", "voltage", "power", "load", "meter reading"),
        keywords=("energy", "power", "voltage", "consumption", "grid", "meter", "load",
                   "renewable", "kwh"),
        expected_numerical_ratio=0.75,
    ),
    "IoT": DomainSpec(
        name="IoT",
        signature_columns=("sensor id", "reading", "timestamp", "device id", "signal"),
        keywords=("sensor", "device", "signal", "telemetry", "firmware", "battery",
                   "reading", "actuator"),
        expected_numerical_ratio=0.7,
    ),
    "Climate": DomainSpec(
        name="Climate",
        signature_columns=("temperature", "humidity", "precipitation", "co2", "wind speed"),
        keywords=("temperature", "humidity", "precipitation", "climate", "emission",
                   "co2", "weather", "wind"),
        known_ranges={"temperature": (-90, 60), "humidity": (0, 100)},
        expected_numerical_ratio=0.8,
    ),
    "Cybersecurity": DomainSpec(
        name="Cybersecurity",
        signature_columns=("ip address", "packet", "attack type", "threat level", "login attempts"),
        keywords=("threat", "attack", "malware", "intrusion", "firewall", "packet",
                   "vulnerability", "breach", "phishing"),
        expected_numerical_ratio=0.5,
    ),
}

GENERAL_DOMAIN = "General Tabular Dataset"
