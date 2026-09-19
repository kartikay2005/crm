"""
The most important test file in this suite, per IMPLEMENTATION_PLAN.md
Segment 8's own instruction: "write an explicit cross-tenant test... this
is the single most important test in this whole segment given the
tenancy bugs found in earlier segments."

Also formalizes the Segment 8 regression where GET /datasets/{id}/analysis
silently lost its @router.get decorator during an earlier edit and went
unreachable for several segments without anyone noticing — a routing-level
smoke test belongs in the permanent suite specifically so that class of
mistake fails CI immediately instead of sitting undetected again.
"""
import io

import pytest


ALL_DATASET_ENDPOINTS = [
    ("GET", "profile"), ("GET", "analysis"), ("GET", "predictions"),
    ("GET", "insights"), ("GET", "recommendations"), ("GET", "charts"),
    ("GET", "report"), ("POST", "predict"),
]


def _upload(api_client, headers, filename="patients.csv"):
    content = (
        b"age,blood_pressure,heart_rate,cholesterol,diagnosis\n"
        b"45,120,72,190,0\n52,135,80,210,1\n61,145,90,240,1\n39,118,68,180,0\n70,150,95,260,1\n"
    )
    resp = api_client.post(
        "/api/v1/datasets/upload", headers=headers,
        files={"file": (filename, io.BytesIO(content), "text/csv")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["dataset_id"]


def test_every_dataset_route_is_actually_registered(api_client):
    """Routing-level regression test for the missing-decorator bug: walks
    the live OpenAPI schema (not source code) to confirm every expected
    dataset-intelligence endpoint is genuinely reachable. This is exactly
    the check that would have caught the /analysis regression the moment
    it was introduced, rather than several segments later."""
    schema = api_client.get("/openapi.json").json()
    paths = schema["paths"]

    expected = [
        ("post", "/api/v1/datasets/upload"),
        ("get", "/api/v1/datasets"),
        ("get", "/api/v1/datasets/{dataset_id}/profile"),
        ("get", "/api/v1/datasets/{dataset_id}/analysis"),
        ("get", "/api/v1/datasets/{dataset_id}/predictions"),
        ("get", "/api/v1/datasets/{dataset_id}/insights"),
        ("get", "/api/v1/datasets/{dataset_id}/recommendations"),
        ("get", "/api/v1/datasets/{dataset_id}/charts"),
        ("get", "/api/v1/datasets/{dataset_id}/report"),
        ("post", "/api/v1/datasets/{dataset_id}/predict"),
        ("delete", "/api/v1/datasets/{dataset_id}"),
        ("get", "/api/v1/models"),
        ("get", "/api/v1/models/domains"),
    ]
    for method, path in expected:
        assert path in paths, f"{path} is not registered at all"
        assert method in paths[path], f"{method.upper()} {path} is not registered"


@pytest.mark.parametrize("method,suffix", ALL_DATASET_ENDPOINTS)
def test_cross_tenant_access_returns_404(api_client, auth_headers, second_auth_headers, method, suffix):
    """Tenant B must NEVER be able to access tenant A's dataset via any
    endpoint, regardless of HTTP method. 404, not 403 — a 403 would leak
    that the resource exists at all."""
    dataset_id = _upload(api_client, auth_headers)

    resp = api_client.request(method, f"/api/v1/datasets/{dataset_id}/{suffix}", headers=second_auth_headers)
    assert resp.status_code == 404, (
        f"{method} /{suffix} leaked cross-tenant data or existence: got {resp.status_code}"
    )


def test_owner_retains_access_after_cross_tenant_check(api_client, auth_headers, second_auth_headers):
    """Guards against an overly-broad fix that blocks everyone, not just
    other tenants — the owner must still see their own data."""
    dataset_id = _upload(api_client, auth_headers)
    api_client.get(f"/api/v1/datasets/{dataset_id}/profile", headers=second_auth_headers)  # cross-tenant attempt
    resp = api_client.get(f"/api/v1/datasets/{dataset_id}/profile", headers=auth_headers)  # owner
    assert resp.status_code == 200


def test_dataset_list_is_tenant_scoped(api_client, auth_headers, second_auth_headers):
    _upload(api_client, auth_headers)
    resp_a = api_client.get("/api/v1/datasets", headers=auth_headers)
    resp_b = api_client.get("/api/v1/datasets", headers=second_auth_headers)
    assert resp_a.json()["total"] == 1
    assert resp_b.json()["total"] == 0


def test_soft_delete_propagates_to_every_endpoint(api_client, auth_headers):
    """A soft-deleted dataset must become immediately unreachable
    everywhere, not just hidden from the list endpoint — this is what
    _get_active_dataset's consistent application across all routes is
    supposed to guarantee."""
    dataset_id = _upload(api_client, auth_headers)
    del_resp = api_client.delete(f"/api/v1/datasets/{dataset_id}", headers=auth_headers)
    assert del_resp.status_code == 204

    resp = api_client.get(f"/api/v1/datasets/{dataset_id}/profile", headers=auth_headers)
    assert resp.status_code == 404

    list_resp = api_client.get("/api/v1/datasets", headers=auth_headers)
    assert list_resp.json()["total"] == 0
