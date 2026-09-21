"""Pure authorization rules. Callers must load current, trusted tenant context.

This module is a foundation, not middleware: existing routes do not use it yet.
"""

from dataclasses import dataclass

POLICY_VERSION = 1

TENANT_ACTIONS = frozenset(
    {
        "project.create",
        "identity.manage",
        "integrations.manage",
        "notifications.deliver",
        "surcharge.manage",
        "tenant.export",
        "tenant.delete",
        "runner.configure",
        "finance.report",
        "accounting.export",
    }
)
PROJECT_ACTIONS = frozenset(
    {
        "project.read",
        "project.settings",
        "project.invite_external",
        "work.read",
        "work.edit",
        "work.execute",
        "work.accept",
        "workflow.manage",
        "shared.read",
        "shared.comment",
        "comms.create",
        "notes.read",
        "notes.write",
        "cost.read",
        "rates.read",
        "time.read",
        "time.record",
        "time.correct",
        "expense.submit",
        "expense.approve",
        "invoice.issue",
        "invoice.read",
        "invoice.pay",
        "invoice.adjust",
        "cash.record",
        "scope.approve",
        "run.execute",
        "reply.publish",
    }
)
ACTIONS = TENANT_ACTIONS | PROJECT_ACTIONS
ADMIN_ONLY = frozenset(
    {
        "identity.manage",
        "integrations.manage",
        "notifications.deliver",
        "surcharge.manage",
        "tenant.export",
        "tenant.delete",
        "runner.configure",
    }
)
# Membership grants are tenant-wide; project grants apply only within one project.
MEMBERSHIP_GRANTABLE = frozenset({"project.create"})
PROJECT_GRANTABLE = frozenset(
    {
        "project.manage",
        "project.settings",
        "project.invite_external",
        "invoice.adjust",
        "scope.approve",
        "run.execute",
    }
)
MANAGER_ACTIONS = frozenset(
    {
        "project.settings",
        "work.accept",
        "workflow.manage",
        "scope.approve",
    }
)
INTERNAL_DEFAULTS = frozenset(
    {
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
    }
)
BILLING_DEFAULTS = frozenset(
    {
        "finance.report",
        "accounting.export",
        "cost.read",
        "rates.read",
        "time.read",
        "time.correct",
        "expense.approve",
        "invoice.issue",
        "invoice.read",
        "invoice.pay",
        "cash.record",
    }
)
EXTERNAL_DEFAULTS = frozenset(
    {
        "project.read",
        "work.read",
        "shared.read",
        "shared.comment",
        "comms.create",
    }
)


@dataclass(frozen=True)
class Membership:
    id: str
    user_id: str
    tenant_id: str
    kind: str
    role: str
    status: str = "active"
    revision: int = 1
    customer_id: str | None = None
    capabilities: frozenset[str] = frozenset()
    regular_cost_access: bool = True


@dataclass(frozen=True)
class Project:
    id: str
    tenant_id: str
    internal_access: str = "all"
    customer_id: str | None = None
    revision: int = 1


@dataclass(frozen=True)
class ProjectGrant:
    project_id: str
    membership_id: str
    tenant_id: str
    access: str = "participant"
    revoked: bool = False
    customer_id: str | None = None
    can_view_invoices: bool = False
    can_approve_scope: bool = False
    capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Resource:
    visibility: str = "internal"
    owner_user_id: str | None = None
    # Domain code must derive these from the actual invoice/approval request.
    issued_invoice: bool = False
    named_approver: bool = False


@dataclass(frozen=True)
class AgentScope:
    tenant_id: str
    owner_membership_id: str
    capabilities: frozenset[str]
    project_ids: frozenset[str]
    active: bool = True


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str
    message: str


def deny(code="not_allowed"):
    # Safe for clients: do not disclose whether another tenant's resource exists.
    return Decision(False, code, "You don’t have access to this action.")


def _id(value):
    return isinstance(value, str) and bool(value.strip())


def _caps(value, allowed):
    return isinstance(value, frozenset) and value <= allowed


def _revision(value):
    return type(value) is int and value > 0


def decide(
    action,
    *,
    tenant_id,
    membership,
    project=None,
    grant=None,
    resource=Resource(),
    agent=None,
):
    """Evaluate one declared action from trusted, current records; deny by default.

    Load membership/grants again after revocation and before queued deliveries.
    Do not construct these records from request JSON, actor headers or token claims.
    """
    if not isinstance(action, str) or action not in ACTIONS:
        return deny("unknown_action")
    m = membership
    if not isinstance(m, Membership) or not _id(tenant_id):
        return deny("invalid_context")
    if not all(_id(x) for x in (m.id, m.user_id, m.tenant_id)):
        return deny("invalid_context")
    if m.tenant_id != tenant_id or m.status != "active":
        return deny()
    if not all(isinstance(x, str) for x in (m.kind, m.role)):
        return deny("invalid_context")
    if (m.kind, m.role) not in {
        ("internal", "admin"),
        ("internal", "billing"),
        ("internal", "regular"),
        ("external", "regular"),
    } or not _revision(m.revision):
        return deny("invalid_context")
    if (
        not _caps(m.capabilities, MEMBERSHIP_GRANTABLE)
        or type(m.regular_cost_access) is not bool
    ):
        return deny("invalid_context")
    if m.kind == "external" and (not _id(m.customer_id) or m.capabilities):
        return deny("invalid_context")
    if (
        not isinstance(resource, Resource)
        or not isinstance(resource.visibility, str)
        or resource.visibility not in {"internal", "shared"}
    ):
        return deny("invalid_context")
    if (
        type(resource.issued_invoice) is not bool
        or type(resource.named_approver) is not bool
    ):
        return deny("invalid_context")
    if agent is not None:
        if not isinstance(agent, AgentScope) or agent.active is not True:
            return deny()
        if agent.tenant_id != tenant_id or agent.owner_membership_id != m.id:
            return deny()
        if not _caps(agent.capabilities, ACTIONS) or not isinstance(
            agent.project_ids, frozenset
        ):
            return deny("invalid_context")
        if (
            not all(_id(x) for x in agent.project_ids)
            or action not in agent.capabilities
        ):
            return deny()
    if action in PROJECT_ACTIONS:
        if not isinstance(project, Project) or not _id(project.id):
            return deny("invalid_context")
        if project.tenant_id != tenant_id:
            return deny()
        if (
            not isinstance(project.internal_access, str)
            or project.internal_access not in {"all", "restricted"}
            or not _revision(project.revision)
        ):
            return deny("invalid_context")
        if agent is not None and project.id not in agent.project_ids:
            return deny()
        if grant is not None:
            if not isinstance(grant, ProjectGrant):
                return deny("invalid_context")
            if (grant.tenant_id, grant.project_id, grant.membership_id) != (
                tenant_id,
                project.id,
                m.id,
            ):
                return deny()
            if (
                not _caps(grant.capabilities, PROJECT_GRANTABLE)
                or not isinstance(grant.access, str)
                or grant.access not in {"participant", "manager"}
            ):
                return deny("invalid_context")
            if any(
                type(x) is not bool
                for x in (
                    grant.revoked,
                    grant.can_view_invoices,
                    grant.can_approve_scope,
                )
            ):
                return deny("invalid_context")
            if grant.revoked:
                grant = None
        if m.kind == "external":
            if (
                grant is None
                or grant.access != "participant"
                or grant.capabilities
                or not _id(project.customer_id)
                or project.customer_id != m.customer_id
                or grant.customer_id != m.customer_id
            ):
                return deny()
            if action != "project.read" and resource.visibility != "shared":
                return deny()
        elif (
            m.role != "admin"
            and project.internal_access == "restricted"
            and grant is None
        ):
            return deny()
    elif project is not None or grant is not None:
        # Tenant operations have their own scope; never use a project grant for them.
        return deny("invalid_context")
    if (
        action in {"time.record", "expense.submit"}
        and resource.owner_user_id != m.user_id
    ):
        return deny()
    if m.kind == "external":
        allowed = action in EXTERNAL_DEFAULTS
        if action in {"invoice.read", "invoice.pay"}:
            allowed = bool(
                grant and grant.can_view_invoices and resource.issued_invoice
            )
        if action == "scope.approve":
            allowed = bool(
                grant and grant.can_approve_scope and resource.named_approver
            )
    elif m.role == "admin":
        allowed = True
    else:
        allowed = action in INTERNAL_DEFAULTS
        if m.role == "billing" and action in BILLING_DEFAULTS:
            allowed = True
        if action == "cost.read" and m.regular_cost_access:
            allowed = True
        if action == "time.read" and resource.owner_user_id == m.user_id:
            allowed = True
        if action in m.capabilities:
            allowed = True
        if grant:
            manager = (
                grant.access == "manager" or "project.manage" in grant.capabilities
            )
            if manager and action in MANAGER_ACTIONS:
                allowed = True
            if action in grant.capabilities and (
                action != "invoice.adjust" or m.role == "billing"
            ):
                allowed = True
        if action in ADMIN_ONLY:
            allowed = False
    return Decision(True, "allowed", "") if allowed else deny()
