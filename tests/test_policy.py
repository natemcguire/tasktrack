"""Authorization boundary tests, independent of browser presentation."""

import unittest
from dataclasses import replace

from tasktrack.policy import (
    ACTIONS,
    ADMIN_ONLY,
    PROJECT_ACTIONS,
    AgentScope,
    Membership,
    Project,
    ProjectGrant,
    Resource,
    decide,
)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.member = Membership("m1", "u1", "a", "internal", "regular")
        self.project = Project("7", "a", customer_id="customer-a")
        self.grant = ProjectGrant("7", "m1", "a", customer_id="customer-a")
        self.shared = Resource(
            "shared", owner_user_id="u1", issued_invoice=True, named_approver=True
        )

    def check(self, action, member=None, project=None, **kwargs):
        if action in PROJECT_ACTIONS:
            kwargs["project"] = project or self.project
        return decide(
            action, tenant_id="a", membership=member or self.member, **kwargs
        ).allowed

    def test_default_role_matrix(self):
        # Explicit expected sets are intentional: changes require reviewing each role.
        internal = {
            "project.read",
            "work.read",
            "work.edit",
            "work.execute",
            "shared.read",
            "shared.comment",
            "comms.create",
            "notes.read",
            "notes.write",
            "time.record",
            "expense.submit",
            "reply.publish",
            "cost.read",
            "time.read",
        }
        billing = internal | {
            "finance.report",
            "accounting.export",
            "rates.read",
            "time.correct",
            "expense.approve",
            "invoice.issue",
            "invoice.read",
            "invoice.pay",
            "cash.record",
        }
        external = {
            "project.read",
            "work.read",
            "shared.read",
            "shared.comment",
            "comms.create",
        }
        roles = [
            ("internal", "admin", set(ACTIONS)),
            ("internal", "billing", billing),
            ("internal", "regular", internal),
            ("external", "regular", external),
        ]
        for kind, role, expected in roles:
            m = replace(
                self.member,
                kind=kind,
                role=role,
                customer_id="customer-a" if kind == "external" else None,
            )
            for action in ACTIONS:
                with self.subTest(kind=kind, role=role, action=action):
                    args = {"resource": self.shared}
                    if kind == "external" and action in PROJECT_ACTIONS:
                        args["grant"] = self.grant
                    self.assertEqual(self.check(action, m, **args), action in expected)

    def test_cross_tenant_denies_every_action_even_admin(self):
        for action in ACTIONS:
            with self.subTest(action=action):
                self.assertFalse(
                    self.check(
                        action,
                        replace(self.member, role="admin", tenant_id="b"),
                        resource=self.shared,
                    )
                )
                if action in PROJECT_ACTIONS:
                    self.assertFalse(
                        self.check(
                            action,
                            replace(self.member, role="admin"),
                            project=replace(self.project, tenant_id="b"),
                            resource=self.shared,
                        )
                    )

    def test_inactive_member_denies_every_action_and_agent(self):
        agent = AgentScope("a", "m1", ACTIONS, frozenset({"7"}))
        for status in ["suspended", "removed", "pending", "", None]:
            for action in ACTIONS:
                with self.subTest(status=status, action=action):
                    self.assertFalse(
                        self.check(
                            action,
                            replace(self.member, role="admin", status=status),
                            agent=agent,
                            resource=self.shared,
                        )
                    )

    def test_restricted_internal_requires_live_matching_grant(self):
        p = replace(self.project, internal_access="restricted")
        self.assertFalse(self.check("project.read", project=p))
        self.assertTrue(self.check("project.read", project=p, grant=self.grant))
        for g in [
            replace(self.grant, revoked=True),
            replace(self.grant, membership_id="m2"),
            replace(self.grant, tenant_id="b"),
            replace(self.grant, project_id="8"),
        ]:
            self.assertFalse(self.check("project.read", project=p, grant=g))
        self.assertTrue(
            self.check("project.read", replace(self.member, role="admin"), project=p)
        )

    def test_external_requires_individual_customer_matched_grant(self):
        m = replace(self.member, kind="external", customer_id="customer-a")
        self.assertFalse(self.check("project.read", m))
        self.assertTrue(self.check("project.read", m, grant=self.grant))
        for g in [
            replace(self.grant, revoked=True),
            replace(self.grant, customer_id="customer-b"),
            replace(self.grant, access="manager"),
            replace(self.grant, membership_id="m2"),
        ]:
            self.assertFalse(self.check("project.read", m, grant=g))
        for customer in [None, "customer-b"]:
            self.assertFalse(
                self.check(
                    "project.read",
                    m,
                    project=replace(self.project, customer_id=customer),
                    grant=self.grant,
                )
            )

    def test_external_content_is_internal_by_default(self):
        m = replace(self.member, kind="external", customer_id="customer-a")
        for action in ["work.read", "shared.read", "shared.comment", "comms.create"]:
            self.assertFalse(self.check(action, m, grant=self.grant))
            self.assertTrue(
                self.check(action, m, grant=self.grant, resource=self.shared)
            )
        for action in [
            "notes.read",
            "notes.write",
            "cost.read",
            "rates.read",
            "time.read",
            "work.edit",
            "work.execute",
        ]:
            self.assertFalse(
                self.check(action, m, grant=self.grant, resource=self.shared)
            )

    def test_external_billing_and_approval_flags_are_narrow(self):
        m = replace(self.member, kind="external", customer_id="customer-a")
        g = replace(self.grant, can_view_invoices=True, can_approve_scope=True)
        for action in ["invoice.read", "invoice.pay", "scope.approve"]:
            self.assertTrue(self.check(action, m, grant=g, resource=self.shared))
            self.assertFalse(
                self.check(action, m, grant=self.grant, resource=self.shared)
            )
        self.assertFalse(
            self.check(
                "invoice.read",
                m,
                grant=g,
                resource=replace(self.shared, issued_invoice=False),
            )
        )
        self.assertFalse(
            self.check(
                "scope.approve",
                m,
                grant=g,
                resource=replace(self.shared, named_approver=False),
            )
        )
        for action in [
            "invoice.issue",
            "invoice.adjust",
            "cash.record",
            "finance.report",
        ]:
            kwargs = {"grant": g} if action in PROJECT_ACTIONS else {}
            self.assertFalse(self.check(action, m, resource=self.shared, **kwargs))

    def test_internal_manager_and_explicit_grants(self):
        g = replace(self.grant, access="manager")
        for action in [
            "project.settings",
            "work.accept",
            "workflow.manage",
            "scope.approve",
        ]:
            self.assertTrue(self.check(action, grant=g))
        self.assertFalse(self.check("project.invite_external", grant=g))
        grants = replace(
            self.grant,
            capabilities=frozenset(
                {"project.invite_external", "run.execute", "invoice.adjust"}
            ),
        )
        for action in ["project.invite_external", "run.execute"]:
            self.assertTrue(self.check(action, grant=grants))
        self.assertFalse(self.check("invoice.adjust", grant=grants))
        self.assertTrue(
            self.check(
                "invoice.adjust", replace(self.member, role="billing"), grant=grants
            )
        )
        self.assertTrue(
            self.check(
                "project.create",
                replace(self.member, capabilities=frozenset({"project.create"})),
            )
        )

    def test_admin_permissions_cannot_be_granted(self):
        for action in ADMIN_ONLY:
            for role in ["billing", "regular"]:
                self.assertFalse(
                    self.check(
                        action,
                        replace(
                            self.member, role=role, capabilities=frozenset({action})
                        ),
                    )
                )
        external = replace(self.member, kind="external", customer_id="customer-a")
        for cap in ["project.manage", "run.execute", "cost.read"]:
            self.assertFalse(
                self.check(
                    "project.read",
                    external,
                    grant=replace(self.grant, capabilities=frozenset({cap})),
                )
            )

    def test_time_expense_and_cost_privacy(self):
        for action in ["time.read", "time.record", "expense.submit"]:
            self.assertTrue(self.check(action, resource=self.shared))
            self.assertFalse(
                self.check(action, resource=replace(self.shared, owner_user_id="u2"))
            )
        self.assertFalse(
            self.check("cost.read", replace(self.member, regular_cost_access=False))
        )
        self.assertTrue(
            self.check(
                "cost.read",
                replace(self.member, role="billing", regular_cost_access=False),
            )
        )
        self.assertFalse(self.check("rates.read"))
        self.assertTrue(
            self.check(
                "time.read",
                replace(self.member, role="billing"),
                resource=Resource(owner_user_id="u2"),
            )
        )

    def test_agent_intersects_owner_and_project_permissions(self):
        agent = AgentScope("a", "m1", ACTIONS, frozenset({"7"}))
        self.assertTrue(self.check("work.edit", agent=agent))
        self.assertFalse(self.check("invoice.issue", agent=agent))
        self.assertFalse(self.check("identity.manage", agent=agent))
        for a in [
            replace(agent, active=False),
            replace(agent, owner_membership_id="m2"),
            replace(agent, tenant_id="b"),
            replace(agent, capabilities=frozenset()),
            replace(agent, project_ids=frozenset()),
            replace(agent, capabilities=frozenset({"*"})),
        ]:
            self.assertFalse(self.check("work.edit", agent=a))
        self.assertFalse(
            self.check(
                "work.edit",
                agent=agent,
                project=replace(self.project, internal_access="restricted"),
            )
        )

    def test_unknown_and_malformed_context_fails_closed(self):
        for action in ["anything", "", None, [], {}]:
            self.assertFalse(
                self.check(action)
                if isinstance(action, str)
                else decide(action, tenant_id="a", membership=self.member).allowed
            )
        for member in [
            None,
            {},
            replace(self.member, kind=[]),
            replace(self.member, role="owner"),
            replace(self.member, revision=True),
            replace(self.member, revision=0),
            replace(self.member, capabilities={"project.create"}),
            replace(self.member, id=""),
        ]:
            self.assertFalse(
                decide(
                    "project.read",
                    tenant_id="a",
                    membership=member,
                    project=self.project,
                ).allowed
            )
        for project in [
            None,
            {},
            replace(self.project, internal_access=[]),
            replace(self.project, revision=0),
        ]:
            self.assertFalse(
                decide(
                    "project.read",
                    tenant_id="a",
                    membership=self.member,
                    project=project,
                ).allowed
            )
        self.assertFalse(self.check("work.read", resource=Resource(visibility=[])))
        self.assertFalse(self.check("work.read", grant=replace(self.grant, access=[])))
        self.assertFalse(
            self.check("work.read", resource=replace(self.shared, issued_invoice="yes"))
        )

    def test_refreshed_revocation_denies_without_cached_permission(self):
        m = replace(self.member, kind="external", customer_id="customer-a")
        self.assertTrue(
            self.check("shared.read", m, grant=self.grant, resource=self.shared)
        )
        self.assertFalse(
            self.check(
                "shared.read",
                m,
                grant=replace(self.grant, revoked=True),
                resource=self.shared,
            )
        )
        self.assertFalse(
            self.check(
                "shared.read",
                replace(m, status="suspended", revision=2),
                grant=self.grant,
                resource=self.shared,
            )
        )

    def test_tenant_action_cannot_consume_project_grants(self):
        admin = replace(self.member, role="admin")
        self.assertFalse(
            decide(
                "identity.manage", tenant_id="a", membership=admin, project=self.project
            ).allowed
        )
        self.assertFalse(
            decide(
                "project.create",
                tenant_id="a",
                membership=self.member,
                grant=self.grant,
            ).allowed
        )


if __name__ == "__main__":
    unittest.main()
