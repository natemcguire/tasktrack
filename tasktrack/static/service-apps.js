"use strict";
const csrf = document.querySelector('meta[name="tt-csrf"]').content;
const workspace = document.querySelector('meta[name="tt-workspace"]').content;
const message = document.querySelector("#app-message");
async function api(path, body) {
  const r = await fetch("/api/v1" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
      "X-Workspace-ID": workspace,
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error?.message || "Request failed.");
  return data;
}
const decode = (s) =>
  Uint8Array.from(atob(s.replaceAll("-", "+").replaceAll("_", "/")), (c) =>
    c.charCodeAt(0),
  );
const encode = (b) =>
  btoa(String.fromCharCode(...new Uint8Array(b)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
async function confirmPasskey(challenge) {
  const options = challenge.options;
  const key = await navigator.credentials.get({
    publicKey: {
      ...options,
      challenge: decode(options.challenge),
      allowCredentials: options.allowCredentials.map((c) => ({
        ...c,
        id: decode(c.id),
      })),
    },
  });
  return {
    challenge_id: challenge.challenge_id,
    credential: {
      id: key.id,
      rawId: encode(key.rawId),
      type: key.type,
      response: {
        authenticatorData: encode(key.response.authenticatorData),
        clientDataJSON: encode(key.response.clientDataJSON),
        signature: encode(key.response.signature),
        userHandle: key.response.userHandle
          ? encode(key.response.userHandle)
          : null,
      },
      clientExtensionResults: key.getClientExtensionResults(),
    },
  };
}
const el = (tag, text, parent) => {
  const n = document.createElement(tag);
  n.textContent = text;
  parent?.append(n);
  return n;
};
async function perform(fn) {
  message.textContent = "";
  try {
    await fn();
  } catch (e) {
    message.textContent = e.message;
  }
}
function credential(data) {
  document.querySelector("#app-secret").hidden = false;
  document.querySelector("#app-credential").textContent = JSON.stringify(
    {
      client_id: data.client_id,
      client_secret: data.client_secret,
      token_endpoint: data.token_endpoint,
    },
    null,
    2,
  );
}
document.querySelector("#app-hide").onclick = () => {
  document.querySelector("#app-credential").textContent = "";
  document.querySelector("#app-secret").hidden = true;
};
async function list() {
  const target = document.querySelector("#app-list");
  target.replaceChildren();
  for (const app of (await api("/service-apps")).items) {
    const row = el("div", "", target);
    el("h3", app.name, row);
    el(
      "p",
      `Projects: ${app.project_ids.join(", ")} · ${app.capabilities.join(", ")} · ${app.revoked_at ? "Revoked" : "Active"}`,
      row,
    );
    if (app.revoked_at) continue;
    for (const action of ["rotate", "revoke"]) {
      const b = el(
        "button",
        action === "rotate" ? "Rotate secret" : "Revoke access",
        row,
      );
      b.className = "button";
      b.onclick = () =>
        perform(async () => {
          b.disabled = true;
          try {
            const path = "/service-apps/" + app.id + "/" + action;
            const proof = await confirmPasskey(
              await api(path + "/challenge", {}),
            );
            const value = await api(path, proof);
            if (value.client_secret) credential(value);
            await list();
          } finally {
            b.disabled = false;
          }
        });
    }
  }
}
document.querySelector("#app-form").onsubmit = (e) => {
  e.preventDefault();
  perform(async () => {
    const b = e.target.querySelector("button");
    b.disabled = true;
    try {
      const data = {
        name: document.querySelector("#app-name").value,
        project_ids: [
          ...document.querySelectorAll("[data-project]:checked"),
        ].map((n) => Number(n.value)),
        capabilities: [
          "project.read",
          "work.read",
          ...(document.querySelector("#app-notes").checked
            ? ["notes.read"]
            : []),
        ],
      };
      const proof = await confirmPasskey(
        await api("/service-apps/challenge", data),
      );
      credential(await api("/service-apps", { ...data, ...proof }));
      await list();
    } finally {
      b.disabled = false;
    }
  });
};
perform(async () => {
  let cursor;
  do {
    const result = await api(
      "/projects?limit=200" +
        (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""),
    );
    for (const p of result.items) {
      const label = el("label", "", document.querySelector("#app-projects"));
      const check = document.createElement("input");
      check.type = "checkbox";
      check.value = p.id;
      check.dataset.project = "";
      label.append(check, document.createTextNode(p.name));
    }
    cursor = result.has_more ? result.next_cursor : null;
  } while (cursor);
  await list();
});
