"use strict";
const csrf = document.querySelector('meta[name="tt-csrf"]').content;
const notice = document.querySelector("#account-message");
async function action(path, body = {}) {
  const response = await fetch("/api/account/" + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error.message);
  return data;
}
function result(target, value, caption, command) {
  const box = document.querySelector(target);
  box.replaceChildren();
  box.className = "account-result";
  const label = document.createElement("p");
  label.textContent = caption;
  const input = document.createElement("input");
  input.readOnly = true;
  input.value = value;
  input.setAttribute("aria-label", caption);
  const copy = document.createElement("button");
  copy.className = "button";
  copy.textContent = "Copy";
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(value);
      copy.textContent = "Copied";
    } catch {
      input.select();
      notice.textContent = "Select and copy the value above.";
    }
  };
  box.append(label, input, copy);
  if (command) {
    const pre = document.createElement("pre");
    pre.textContent = command;
    box.append(pre);
  }
}
function bindForm(selector, handler) {
  const form = document.querySelector(selector);
  if (!form) return;
  form.onsubmit = async (event) => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    notice.textContent = "";
    try {
      await handler(new FormData(form));
    } catch (error) {
      notice.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };
}
bindForm("#create-token", async (data) => {
  const { token } = await action("token", { name: data.get("name") });
  result(
    "#token-result",
    token,
    "Copy this token now. It is shown once.",
    `export TT_URL=${location.origin}\nexport TT_TOKEN='${token}'\nexport TT_SESSION=work-1\ntt brief --json`,
  );
});
bindForm("#rename-workspace", async (data) => {
  await action("workspace-name", { name: data.get("name") });
  notice.textContent = "Workspace name saved.";
});
const invite = document.querySelector("#create-invite");
if (invite)
  invite.onclick = async () => {
    invite.disabled = true;
    try {
      const { url } = await action("invite");
      result("#invite-result", url, "Share this invitation with one teammate.");
    } catch (error) {
      notice.textContent = error.message;
    } finally {
      invite.disabled = false;
    }
  };
async function v1(path, { method = "GET", body, idempotencyKey } = {}) {
  const headers = { "X-CSRF-Token": csrf, "X-Via": "ui" };
  if (body) headers["Content-Type"] = "application/json";
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  const response = await fetch("/api/v1" + path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(
      data.error && data.error.fields && Object.keys(data.error.fields).length
        ? Object.values(data.error.fields).join(" ")
        : data.error
          ? data.error.message
          : "Something went wrong.",
    );
    error.code = data.error && data.error.code;
    throw error;
  }
  return data;
}
bindForm("#create-workspace", async (data) => {
  const message = document.querySelector("#create-workspace-message");
  await v1("/tenants", {
    method: "POST",
    body: { name: data.get("name"), timezone: data.get("timezone") },
    idempotencyKey: crypto.randomUUID(),
  });
  message.textContent = "Workspace created. Opening it…";
  // Tenant creation switches the session and rotates CSRF; reload to pick it up.
  location.assign("/");
});
const memberList = document.querySelector("#member-list");
const memberNotice = document.querySelector("#member-message");
if (memberList) {
  async function patchMember(id, changes, extra = {}) {
    const holder = memberList.querySelector(`[data-member-revision="${id}"]`);
    const revision = Number(holder.dataset.revision);
    memberNotice.textContent = "";
    memberNotice.className = "";
    try {
      const result = await v1("/memberships/" + id, {
        method: "PATCH",
        body: { expected_version: revision, ...changes, ...extra },
      });
      holder.dataset.revision = result.revision;
      memberNotice.textContent = "Saved. Affected sessions were signed out.";
      return result;
    } catch (error) {
      memberNotice.className = "form-error";
      if (error.code === "version_conflict") {
        memberNotice.textContent =
          "This person’s access changed elsewhere. Reload to see the current state, then try again.";
      } else if (error.code === "last_admin") {
        memberNotice.textContent =
          "This is the last admin. Make someone else an admin first.";
      } else {
        memberNotice.textContent = error.message;
      }
      throw error;
    }
  }
  memberList.querySelectorAll("[data-member-role]").forEach((sel) => {
    let previous = sel.value;
    sel.onchange = async () => {
      sel.disabled = true;
      try {
        await patchMember(sel.dataset.memberRole, {
          role: sel.value,
          reason: "Role changed from workspace settings",
        });
        previous = sel.value;
      } catch {
        sel.value = previous; // preserve the real state on failure
      } finally {
        sel.disabled = false;
      }
    };
  });
  memberList.querySelectorAll("[data-member-status]").forEach((btn) => {
    btn.onclick = async () => {
      const suspend = btn.dataset.status !== "suspended";
      if (
        suspend &&
        !confirm(
          "Suspend this person? They’ll be signed out and can’t access the workspace until reactivated.",
        )
      )
        return;
      btn.disabled = true;
      try {
        await patchMember(btn.dataset.memberStatus, {
          status: suspend ? "suspended" : "active",
          reason: "Status changed from workspace settings",
        });
        btn.dataset.status = suspend ? "suspended" : "active";
        btn.textContent = suspend ? "Reactivate" : "Suspend";
      } catch {
        /* notice already shown; leave control as-is */
      } finally {
        btn.disabled = false;
      }
    };
  });
}
for (const button of document.querySelectorAll(
  "[data-revoke-token], [data-revoke-invite], [data-remove-member]",
)) {
  button.onclick = async () => {
    button.disabled = true;
    try {
      await action(
        button.dataset.removeMember
          ? "remove-member"
          : button.dataset.revokeToken
            ? "revoke-token"
            : "revoke-invite",
        {
          id:
            button.dataset.removeMember ||
            button.dataset.revokeToken ||
            button.dataset.revokeInvite,
        },
      );
      button.closest("li").remove();
      notice.textContent = "Access revoked.";
    } catch (error) {
      notice.textContent = error.message;
      button.disabled = false;
    }
  };
}
