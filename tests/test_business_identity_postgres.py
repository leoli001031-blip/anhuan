"""Organization-aware session projection through HTTP and real tenant RLS.

OIDC verification is covered separately; only its verified claims are injected.
This gate does not certify the later professional permissions/portal write slice.
"""
from __future__ import annotations
import os
import unittest
import uuid
import asyncio
from unittest.mock import patch

os.environ.setdefault("F1_KEYCLOAK_ISSUER_URL", "http://material-rag.invalid/realms/anhuan")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from infra.f1 import local_seed
from infra.f1.analysis_report_postgres_integration import PostgresIntegrationStack
from platform_foundation.f1.auth import current_user
from platform_foundation.f1.database import session_scope
from platform_foundation.f1.api.routers.analysis_reports import router, session_router
from platform_foundation.f1.api.routers import service_cases, material_qa

STACK = WORLD = None


def setUpModule():
    global STACK, WORLD
    STACK = PostgresIntegrationStack()
    print("BUSINESS_IDENTITY_PROJECT=" + STACK.project_name, flush=True)
    try:
        STACK.start()
        WORLD = STACK.seed_world()
    except BaseException:
        STACK.dispose_runtime(); STACK.stop(); raise


def tearDownModule():
    if STACK:
        STACK.dispose_runtime(); STACK.stop()
        if STACK.cleanup_status != "CLEAN" or STACK.dedicated_after != (0,0,0) or STACK.shared_match != 1:
            raise AssertionError("BUSINESS_IDENTITY_CLEANUP_FAILED")
        print("BUSINESS_IDENTITY_CLEANUP=CLEAN;SHARED_UNCHANGED=1", flush=True)


class BusinessIdentityPostgresTests(unittest.TestCase):
    def setUp(self):
        self.sub = "business-role-" + uuid.uuid4().hex
        self.claims = {"sub": self.sub, "roles": ["super_admin"]}
        self.app = FastAPI()
        self.app.include_router(session_router, prefix="/v1")
        self.app.include_router(service_cases.router, prefix="/v1/service-cases")
        self.app.include_router(material_qa.router, prefix="/v1/material-qa")
        self.app.include_router(router, prefix="/v1/analysis-reports")
        self.app.dependency_overrides[current_user] = lambda: self.claims
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()

    def member(self, enterprise, role):
        with STACK._bootstrap() as connection:
            local_seed._ensure_binding(connection, local_seed.Binding(self.sub, self.sub,
                self.sub + "@example.invalid", enterprise, role))

    def access(self, enterprise):
        return self.client.get("/v1/session/access", headers={"X-Enterprise-Id": str(enterprise)})

    def test_provider_roles_come_from_membership_not_realm_claims(self):
        for membership, product in (("enterprise_admin", "provider_admin"), ("plant_admin", "provider_consultant"), ("auditor", "provider_reviewer")):
            with self.subTest(role=membership):
                self.sub = "business-role-" + uuid.uuid4().hex
                self.claims["sub"] = self.sub
                self.member(WORLD.enterprise_a, membership)
                response = self.access(WORLD.enterprise_a)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["schema"], "anhuan-analysis-report-session-v2")
                self.assertEqual(response.json()["product_role"], product)
                if membership != "enterprise_admin":
                    self.assertEqual(response.json()["capabilities"], [])
                    denied = self.client.get("/v1/analysis-reports/published", headers={"X-Enterprise-Id": str(WORLD.enterprise_a)})
                    self.assertEqual(denied.status_code, 404)

    def test_client_administrator_is_a_client_and_cannot_call_provider_report_api(self):
        self.member(WORLD.enterprise_b, "enterprise_admin")
        response = self.access(WORLD.enterprise_b)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["product_role"], "client_user")
        self.assertEqual(response.json()["capabilities"], ["list_published", "read_published"])
        response = self.client.get(f"/v1/analysis-reports/clients/{WORLD.foreign_client_id}/reports", headers={"X-Enterprise-Id": str(WORLD.enterprise_b)})
        self.assertEqual(response.status_code, 404)

    def test_technical_administrator_is_not_a_professional_report_actor(self):
        self.member(WORLD.enterprise_a, "super_admin")
        self.claims["roles"] = []
        response = self.access(WORLD.enterprise_a)
        self.assertEqual(response.json()["product_role"], "technical_admin")
        self.assertEqual(response.json()["capabilities"], [])
        response = self.client.get(f"/v1/analysis-reports/clients/{WORLD.bound_client_id}/reports", headers={"X-Enterprise-Id": str(WORLD.enterprise_a)})
        self.assertEqual(response.status_code, 404)

    def test_unknown_organization_does_not_guess_client_or_provider(self):
        self.member(WORLD.enterprise_c, "enterprise_admin")
        response = self.access(WORLD.enterprise_c)
        self.assertEqual(response.json()["product_role"], "unconfigured")
        self.assertEqual(response.json()["capabilities"], [])

    def test_same_person_has_independent_roles_in_two_organizations(self):
        self.member(WORLD.enterprise_a, "auditor")
        self.member(WORLD.enterprise_b, "enterprise_admin")
        self.assertEqual(self.client.get("/v1/session/access").status_code, 400)
        self.assertEqual(self.access(WORLD.enterprise_a).json()["product_role"], "provider_reviewer")
        self.assertEqual(self.access(WORLD.enterprise_b).json()["product_role"], "client_user")

    def test_nonmembership_header_remains_not_found(self):
        self.member(WORLD.enterprise_a, "enterprise_admin")
        self.assertEqual(self.access(WORLD.enterprise_b).status_code, 404)

    def test_runtime_cannot_change_organization_kind_to_promote_itself(self):
        self.member(WORLD.enterprise_b, "enterprise_admin")
        async def exercise():
            async with session_scope(role="f1_api", enterprise_id=WORLD.enterprise_b, sub=self.sub) as session:
                try:
                    result = await session.execute(text("UPDATE f1.enterprise SET business_kind='service_provider' WHERE id=:id"), {"id":WORLD.enterprise_b})
                except DBAPIError as error:
                    self.assertIn(error.orig.sqlstate, {"42501", "P0001"})
                    if error.orig.sqlstate == "P0001":
                        self.assertIn("BUSINESS_ORGANIZATION_KIND_IMMUTABLE", str(error.orig))
                else:
                    # A denied UPDATE may affect zero rows under RLS.
                    self.assertEqual(result.rowcount, 0)
                await session.rollback()
        asyncio.run(exercise())
        self.assertEqual(self.access(WORLD.enterprise_b).json()["product_role"], "client_user")

    def test_client_admin_service_summary_uses_audience_and_hides_other_customer(self):
        self.member(WORLD.enterprise_b, "enterprise_admin")
        own, other = uuid.uuid4(), uuid.uuid4()
        with STACK._bootstrap() as connection:
            for case_id, client_id in ((own, WORLD.bound_client_id), (other, WORLD.unbound_client_id)):
                connection.execute("INSERT INTO f1.service_case (id,enterprise_id,client_account_id,title,description,service_type,created_by_user_id) VALUES (%s,%s,%s,'Synthetic service','Internal secret','check',%s)", (case_id,WORLD.enterprise_a,client_id,WORLD.actor_a))
        response = self.client.get("/v1/service-cases/portal", headers={"X-Enterprise-Id":str(WORLD.enterprise_b)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(str(own), [row["id"] for row in response.json()["items"]])
        self.assertNotIn(str(other), response.text)
        self.assertNotIn("Internal secret", response.text)
        self.assertEqual(response.json()["allowed_actions"], [])

    def test_client_admin_real_extractive_qa_uses_bound_material_and_rechecks_revocation(self):
        self.member(WORLD.enterprise_b, "enterprise_admin")
        headers = {"X-Enterprise-Id":str(WORLD.enterprise_b)}
        body = {"request_id":str(uuid.uuid4()), "question":"合成材料"}
        with patch.dict(os.environ, {"F1_MATERIAL_QA_LOCAL_EXTRACTIVE":"1"}):
            response = self.client.post("/v1/material-qa", headers=headers, json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertGreater(len(response.json()["citations"]), 0, response.text)
            self.assertNotIn("race", response.text)
            self.assertEqual(self.client.post("/v1/material-qa", headers=headers, json=body).json(), response.json())
            self.assertEqual(self.client.post("/v1/material-qa", headers=headers, json={**body,"client_account_id":str(WORLD.unbound_client_id)}).status_code,404)
            STACK.set_binding_status(WORLD.bound_client_id, "revoked")
            try:
                self.assertEqual(self.client.post("/v1/material-qa", headers=headers, json=body).status_code,404)
                self.assertEqual(self.client.get("/v1/service-cases/portal", headers=headers).json()["items"],[])
            finally:
                STACK.set_binding_status(WORLD.bound_client_id, "active")

    def test_provider_technical_and_unconfigured_roles_cannot_enter_customer_routes(self):
        for enterprise, role in ((WORLD.enterprise_a,"enterprise_admin"), (WORLD.enterprise_a,"super_admin"), (WORLD.enterprise_a,"auditor"), (WORLD.enterprise_a,"plant_admin"), (WORLD.enterprise_c,"enterprise_admin")):
            with self.subTest(enterprise=enterprise,role=role):
                self.sub = "business-role-" + uuid.uuid4().hex
                self.claims["sub"] = self.sub
                self.member(enterprise,role)
                headers = {"X-Enterprise-Id":str(enterprise)}
                self.assertEqual(self.client.get("/v1/service-cases/portal",headers=headers).status_code,404)
                if role != "enterprise_admin" or enterprise == WORLD.enterprise_c:
                    response = self.client.post("/v1/material-qa",headers=headers,json={"question":"合成材料","request_id":str(uuid.uuid4())})
                    self.assertEqual(response.status_code,403,response.text)
