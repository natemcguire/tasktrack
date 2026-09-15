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
