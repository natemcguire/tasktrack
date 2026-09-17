"""Hosted authorization adapter for the shared domain service.

Caller identity is supplied only by the authenticated Worker. Project grants are
loaded inside the same tenant SQLite transaction as the protected operation.
"""

import json

from .db import Error
from .policy import (
    ACTIONS,
    AgentScope,
    Membership,
    Project,
    ProjectGrant,
    Resource,
    decide,
)


class Access:
    def __init__(self, identity):
        self.identity = identity
        self.tenant_id = identity["workspace_id"]
        self.member = Membership(
            id=identity["membership_id"],
            user_id=identity["user_id"],
            tenant_id=self.tenant_id,
            kind=identity["kind"],
            role=identity["access_role"],
            status=identity["status"],
            revision=identity["membership_revision"],
            customer_id=identity.get("customer_id"),
            capabilities=frozenset(
                json.loads(identity.get("membership_capabilities", "[]"))
            ),
        )
        self.external = self.member.kind == "external"

    def context(self, c, project_id):
        row = c.execute(
            "SELECT * FROM project_access WHERE project_id=?", (project_id,)
        ).fetchone()
        if not row:
            raise Error(404, "not_found", "This project is unavailable.")
        project = Project(
            str(project_id),
            self.tenant_id,
            row["internal_access"],
            row["customer_id"],
            row["revision"],
        )
        row = c.execute(
            "SELECT * FROM project_grants WHERE project_id=? AND membership_id=?",
            (project_id, self.member.id),
        ).fetchone()
        grant = (
            None
            if row is None
            else ProjectGrant(
                project_id=str(project_id),
                membership_id=self.member.id,
                tenant_id=self.tenant_id,
                access=row["access"],
                customer_id=row["customer_id"],
                can_view_invoices=bool(row["can_view_invoices"]),
                can_approve_scope=bool(row["can_approve_scope"]),
                capabilities=frozenset(json.loads(row["capabilities"])),
            )
        )
        return project, grant

    def allowed(self, c, action, project_id=None, resource=Resource()):
        project, grant = (
            self.context(c, project_id) if project_id is not None else (None, None)
        )
        agent = None
        if self.identity.get("bearer"):
            # Legacy owner-issued tokens are tenant-wide, but still intersect live
            # membership and project policy. Explicit scopes can only narrow this.
            ids = self.identity.get("token_project_ids")
            if ids is None:
                ids = [str(r[0]) for r in c.execute("SELECT id FROM projects")]
            agent = AgentScope(
                self.tenant_id,
                self.member.id,
                frozenset(self.identity.get("token_capabilities", ACTIONS)),
                frozenset(ids),
            )
        return decide(
            action,
            tenant_id=self.tenant_id,
            membership=self.member,
            project=project,
            grant=grant,
            resource=resource,
            agent=agent,
        ).allowed

    def require(self, c, action, project_id=None, resource=Resource(), hidden=False):
        if not self.allowed(c, action, project_id, resource):
            raise Error(
                404 if hidden else 403,
                "not_found" if hidden else "not_allowed",
                "This item is unavailable."
                if hidden
                else "You don’t have permission to do that.",
            )

    def visible_projects(self, c):
        return [
            r[0]
            for r in c.execute("SELECT id FROM projects")
            if self.allowed(c, "project.read", r[0])
        ]

    def task_scope(self, c):
        # Existing task content is internal. Shared task serialization is delivered
        # with the discussions/customer-visibility feature, not inferred from grants.
        if self.external:
            return "0", []
        ids = self.visible_projects(c)
        return (
            ("project_id IN (" + ",".join("?" for _ in ids) + ")", ids)
            if ids
            else ("0", [])
        )

    def write(self, service, c, method, path, body):
        parts = path.strip("/").split("/")[2:]
        if parts == ["projects"] and method == "POST":
            return self.require(c, "project.create")
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "columns":
            project = service.project(c, parts[1])
            return self.require(c, "workflow.manage", project["id"])
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "access":
            self.require(c, "identity.manage")
        if len(parts) >= 2 and parts[0] == "projects":
            project = service.project(c, parts[1])
            return self.require(c, "project.settings", project["id"])
        if parts == ["tasks"] and method == "POST":
            project = service.project(c, body.get("project_id", ""))
            return self.require(c, "work.edit", project["id"])
        if len(parts) >= 2 and parts[0] == "tasks":
            task = service.task(c, parts[1])
            action = parts[2] if len(parts) > 2 else "edit"
            capability = {
                "claim": "work.execute",
                "resume": "work.execute",
                "checkpoint": "work.execute",
                "handoff": "work.execute",
                "submit": "work.execute",
                "complete": "work.accept",
                "reopen": "work.accept",
                "request-changes": "work.accept",
                "comments": "notes.write",
                "attachments": "notes.write",
            }.get(action, "work.edit")
            if action == "move":
                phase = body.get("phase") or body.get("status")
                if phase is None and type(body.get("column_id")) is int:
                    column = c.execute(
                        "SELECT allowed_phases_json FROM board_columns WHERE id=? AND project_id=? AND archived_at IS NULL",
                        (body["column_id"], task["project_id"]),
                    ).fetchone()
                    if column:
                        phases = json.loads(column["allowed_phases_json"])
                        phase = (
                            task["status"]
                            if task["status"] in phases
                            else phases[0]
                            if len(phases) == 1
                            else None
                        )
                capability = (
                    "work.accept"
                    if phase == "done"
                    else "work.execute"
                    if phase in {"review", "in_progress"}
                    else "work.edit"
                )
            return self.require(c, capability, task["project_id"])
        raise Error(404, "not_found", "This action is unavailable.")

    def project_data(self, c, project):
        self.require(c, "project.read", project["id"], hidden=True)
        p, _ = self.context(c, project["id"])
        if self.external:
            return {k: project[k] for k in ("id", "key", "name", "version", "url")} | {
                "brief_markdown": "",
                "document_links": [],
                "aliases": [],
            }
        return project | {
            "internal_access": p.internal_access,
            "customer_id": p.customer_id,
            "visibility_revision": p.revision,
        }

    def project_access(self, service, c, identifier):
        project = service.project(c, identifier)
        self.require(c, "project.settings", project["id"])
        p, _ = self.context(c, project["id"])
        grants = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM project_grants WHERE project_id=? ORDER BY membership_id",
                (project["id"],),
            )
        ]
        for g in grants:
            g["capabilities"] = json.loads(g["capabilities"])
        return {
            "project_id": project["id"],
            "internal_access": p.internal_access,
            "customer_id": p.customer_id,
            "revision": p.revision,
            "grants": grants,
        }

    def update_project_access(self, service, c, identifier, body, context):
        # Grant changes and customer moves are admin-only, not delegated project edits.
        self.require(c, "identity.manage")
        project = service.project(c, identifier)
        previous = self.project_access(service, c, identifier)
        if (
            type(body.get("expected_version")) is not int
            or body["expected_version"] != previous["revision"]
        ):
            raise Error(
                409, "version_conflict", "Project access changed. Reload and try again."
            )
        mode = body.get("internal_access", previous["internal_access"])
        customer = body.get("customer_id", previous["customer_id"])
        if mode not in ("all", "restricted") or (
            customer is not None
            and (
                not isinstance(customer, str)
                or not customer.strip()
                or len(customer) > 100
            )
        ):
            raise Error(422, "invalid_access", "Choose valid project access settings.")
        grants = body.get("grants")
        if not isinstance(grants, list) or len(grants) > 500:
            raise Error(422, "invalid_grants", "Supply the complete participant list.")
        if customer != previous["customer_id"] and any(
            g["customer_id"] is not None for g in previous["grants"]
        ):
            raise Error(
                409,
                "customer_grants_remain",
                "Remove existing customer participants before changing the customer.",
            )
        seen = set()
        for g in grants:
            # Worker resolves membership/customer from D1, never client role assertions.
            if (
                not isinstance(g, dict)
                or not isinstance(g.get("membership_id"), str)
                or g["membership_id"] in seen
            ):
                raise Error(
                    422, "invalid_grants", "Each participant must be listed once."
                )
            seen.add(g["membership_id"])
            if g.get("customer_id") is not None and g["customer_id"] != customer:
                raise Error(
                    422,
                    "customer_mismatch",
                    "Choose participants from this project’s customer.",
                )
        c.execute("DELETE FROM project_grants WHERE project_id=?", (project["id"],))
        for g in grants:
            c.execute(
                "INSERT INTO project_grants VALUES (?,?,?,?,?,?,?)",
                (
                    project["id"],
                    g["membership_id"],
                    g["access"],
                    g.get("customer_id"),
                    json.dumps(g["capabilities"]),
                    int(g["can_view_invoices"]),
                    int(g["can_approve_scope"]),
                ),
            )
        c.execute(
            "UPDATE project_access SET internal_access=?,customer_id=?,revision=revision+1 WHERE project_id=?",
            (mode, customer, project["id"]),
        )
        service.event(
            c,
            "project",
            project["id"],
            project["id"],
            "project.access_changed",
            context,
            {"revision": previous["revision"] + 1},
        )
        return self.project_access(service, c, identifier)
