"use strict";
const csrf = document.querySelector('meta[name="tt-csrf"]').content;
const workspace = document.querySelector('meta[name="tt-workspace"]').content;
const message = document.querySelector("#agent-message");
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
  let data;
  try {
    data = await r.json();
  } catch {
    throw new Error(
      "The server response was interrupted. Refresh to check the result before trying again.",
    );
  }
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
function line(parent, text) {
  const p = document.createElement("p");
  p.textContent = text;
  parent.append(p);
}
async function attempt(button, fn) {
  button.disabled = true;
  message.textContent = "";
  try {
    await fn();
  } catch (e) {
    message.textContent =
      e.name === "NotAllowedError"
        ? "Passkey confirmation cancelled."
        : e.message;
  } finally {
    button.disabled = false;
  }
}
const capabilityLabels = {
  "project.read": "View projects",
  "work.read": "View tasks",
  "work.edit": "Edit tasks",
  "work.execute": "Work on tasks",
  "work.accept": "Review completed work",
  "notes.read": "Read comments and files",
  "notes.write": "Add comments and files",
  "notifications.deliver": "Deliver your notifications",
  "integrations.manage": "Manage imports",
};
const friendlyCapabilities = (caps) =>
  caps.map((c) => capabilityLabels[c] || c).join(", ");
const projectNames = new Map();
const projectNamesReady = (async () => {
  let cursor;
  do {
    const page = await api(
      "/projects?limit=200" +
        (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""),
    );
    for (const p of page.items) projectNames.set(String(p.id), p.name);
    cursor = page.has_more ? page.next_cursor : null;
  } while (cursor);
})().catch(() => {});
const friendlyProjects = (ids) =>
  ids.map((id) => projectNames.get(String(id)) || "Project " + id).join(", ");
const progress = document.querySelector("#agent-progress");
const progressTitle = document.querySelector("#agent-progress-title");
const progressDetail = document.querySelector("#agent-progress-detail");
const progressKey = "tasktrack-enrollment-status:" + workspace;
let progressTimer;
let progressGeneration = 0;
function showProgress(title, detail) {
  progress.hidden = false;
  progressTitle.textContent = title;
  progressDetail.textContent = detail;
}
function monitorConnection(id) {
  clearTimeout(progressTimer);
  const generation = ++progressGeneration;
  const check = async () => {
    try {
      const state = await api("/agent-enrollments/" + id + "/status");
      if (generation !== progressGeneration) return;
      if (state.status === "connected") {
        showProgress(
          "Agent connected",
          state.name + " collected its credential. Connection is complete.",
        );
        await loadAgents();
        return;
      }
      if (state.status === "approved") {
        showProgress(
          "Approval saved — waiting for agent",
          "Your approval is recorded. Keep the agent running so it can collect its credential.",
        );
      } else if (state.status === "pending") {
        showProgress(
          "Approval not completed",
          "The server has not recorded approval. Review the code and complete the passkey prompt.",
        );
        return;
      } else {
        const expired = state.status === "expired";
        showProgress(
          expired ? "Connection request expired" : "Agent is not connected",
          expired
            ? "No credential was issued in time. Ask the agent for a new connection request."
            : "This request was denied, cancelled, or its access is no longer active.",
        );
        return;
      }
      progressTimer = setTimeout(check, 3000);
    } catch (e) {
      if (generation !== progressGeneration) return;
      showProgress(
        "Connection not confirmed",
        "Could not verify the connection: " +
          e.message +
          " Refresh this page to check again.",
      );
    }
  };
  check();
}
window.addEventListener("pagehide", () => clearTimeout(progressTimer));
let current;
document.querySelector("#agent-code-form").onsubmit = (e) => {
  e.preventDefault();
  attempt(e.target.querySelector("button"), async () => {
    current = await api("/agent-enrollments/lookup", {
      code: document.querySelector("#agent-code").value,
    });
    if (current.workspace_switched) {
      sessionStorage.setItem(
        "tasktrack-enrollment-code",
        document.querySelector("#agent-code").value,
      );
      location.reload();
      return;
    }
    clearTimeout(progressTimer);
    progressGeneration++;
    progress.hidden = true;
    sessionStorage.removeItem(progressKey);
    const box = document.querySelector("#agent-details");
    box.replaceChildren();
    line(box, "Agent: " + current.name);
    await projectNamesReady;
    line(box, "Projects: " + friendlyProjects(current.project_ids));
    line(box, "Access: " + friendlyCapabilities(current.capabilities));
    line(
      box,
      "Request expires: " +
        new Date(current.expires_at * 1000).toLocaleString(),
    );
    line(box, "Access lasts up to 30 days. You can revoke it at any time.");
    document.querySelector("#agent-approve").disabled = false;
    document.querySelector("#agent-review").hidden = false;
    document
      .querySelector("#agent-code-form")
      .closest(".setup-primary").hidden = true;
    document
      .querySelector("#agent-review")
      .scrollIntoView({ behavior: "smooth", block: "start" });
  });
};
for (const decision of ["approve", "deny"]) {
  const button = document.querySelector("#agent-" + decision);
  button.onclick = () =>
    attempt(button, async () => {
      const path = "/agent-enrollments/" + current.id;
      sessionStorage.setItem(progressKey, current.id);
      try {
        let proof = {};
        if (decision === "approve") {
          showProgress(
            "Confirm with your passkey",
            "Complete the system prompt, then return here to confirm the agent connected.",
          );
          proof = await confirmPasskey(await api(path + "/challenge", {}));
          showProgress(
            "Saving your approval",
            "Passkey response received. Waiting for the server to record approval.",
          );
        }
        const result = await api(path + "/decision", { decision, ...proof });
        if (result.status !== (decision === "approve" ? "approved" : "denied"))
          throw new Error("The server did not confirm the decision.");
        document.querySelector("#agent-review").hidden = true;
        message.textContent =
          decision === "approve" ? "Approval saved." : "Request denied.";
        monitorConnection(current.id);
      } catch (e) {
        showProgress(
          "Approval not confirmed",
          e.name === "NotAllowedError"
            ? "The passkey prompt was cancelled or timed out. Review the request and try again."
            : e.message +
                " Refresh this page to check whether approval was recorded.",
        );
        throw e;
      }
    });
}
async function loadAgents() {
  const box = document.querySelector("#agent-list");
  const result = await api("/agents");
  await projectNamesReady;
  box.replaceChildren();
  if (!result.items.length) line(box, "No registered agents yet.");
  for (const item of result.items) {
    const section = document.createElement("article");
    line(
      section,
      item.name +
        " · " +
        (item.revoked_at
          ? "Revoked"
          : "Expires " + new Date(item.expires_at * 1000).toLocaleDateString()),
    );
    line(section, "Projects: " + friendlyProjects(JSON.parse(item.projects)));
    line(section, friendlyCapabilities(JSON.parse(item.capabilities)));
    if (!item.revoked_at) {
      const b = document.createElement("button");
      b.className = "button";
      b.textContent = "Revoke access";
      b.onclick = () =>
        attempt(b, async () => {
          await api("/agents/" + item.id + "/revoke", {});
          await loadAgents();
        });
      section.append(b);
    }
    box.append(section);
  }
}
const preference = document.querySelector("#agent-email");
preference.onchange = () =>
  attempt(preference, async () => {
    await api("/agent-notifications", { email_enabled: preference.checked });
    message.textContent = "Notification preference saved.";
  });
async function loadApprovals() {
  const box = document.querySelector("#approval-list");
  const result = await api("/approval-requests");
  box.replaceChildren();
  if (!result.items.length) line(box, "No pending action requests.");
  for (const item of result.items) {
    const section = document.createElement("article");
    line(section, item.summary);
    line(
      section,
      "Agent: " +
        item.actor +
        " · expires " +
        new Date(item.expires_at * 1000).toLocaleString(),
    );
    line(
      section,
      "Reason: " + (item.manifest.body.reason || "No reason supplied."),
    );
    if (item.manifest.operation === "task.reassign")
      line(
        section,
        "New assignee: " + (item.manifest.body.assignee || "Unassigned"),
      );
    if (item.manifest.parent)
      line(section, "Also reopens: " + item.manifest.parent.title);
    const target = document.createElement("a");
    target.href = "/tasks/" + encodeURIComponent(item.manifest.task_id);
    target.textContent = "View task";
    section.append(target);
    const technical = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Exact action details";
    const manifest = document.createElement("pre");
    manifest.textContent = JSON.stringify(item.manifest, null, 2);
    technical.append(summary, manifest);
    section.append(technical);
    for (const decision of ["approve", "deny"]) {
      const b = document.createElement("button");
      b.className = "button";
      b.textContent = decision === "approve" ? "Approve with passkey" : "Deny";
      b.onclick = () =>
        attempt(b, async () => {
          const path = "/approval-requests/" + item.id;
          const proof =
            decision === "approve"
              ? await confirmPasskey(await api(path + "/challenge", {}))
              : {};
          await api(path + "/decision", { decision, ...proof });
          await loadApprovals();
        });
      section.append(b);
    }
    box.append(section);
  }
}
let pendingChannel;
async function loadChannels() {
  const box = document.querySelector("#channel-list");
  box.replaceChildren();
  for (const channel of (await api("/agent-channels")).items) {
    const p = document.createElement("p");
    p.textContent =
      channel.channel +
      " · " +
      channel.recipient +
      " · " +
      (channel.verified_at ? "Verified" : "Awaiting verification");
    const b = document.createElement("button");
    b.className = "button";
    b.textContent = "Remove";
    b.onclick = () =>
      attempt(b, async () => {
        await api("/agent-channels/" + channel.id + "/remove", {});
        await loadChannels();
      });
    p.append(b);
    box.append(p);
    if (!channel.verified_at) {
      pendingChannel = channel;
      document.querySelector("#channel-verify").hidden = false;
    }
  }
}
document.querySelector("#channel-form").onsubmit = (e) => {
  e.preventDefault();
  attempt(e.target.querySelector("button"), async () => {
    pendingChannel = await api("/agent-channels", {
      channel: document.querySelector("#channel-kind").value,
      recipient: document.querySelector("#channel-phone").value,
    });
    document.querySelector("#channel-verify").hidden = false;
    message.textContent = pendingChannel.bridge_required
      ? "Verification queued. Keep your personal Mac message bridge running to receive it."
      : "Verification code sent to the configured delivery service.";
    await loadChannels();
  });
};
document.querySelector("#channel-verify").onsubmit = (e) => {
  e.preventDefault();
  attempt(e.target.querySelector("button"), async () => {
    const path = "/agent-channels/" + pendingChannel.id;
    const proof = await confirmPasskey(await api(path + "/challenge", {}));
    await api(path + "/verify", {
      ...proof,
      code: document.querySelector("#channel-code").value,
    });
    document.querySelector("#channel-verify").hidden = true;
    message.textContent =
      "Phone verified. Agent requests can now send review links here.";
    await loadChannels();
  });
};
Promise.all([
  loadAgents(),
  api("/agent-notifications").then((p) => {
    preference.checked = p.email_enabled;
  }),
  loadApprovals(),
  loadChannels(),
]).catch((e) => {
  message.textContent = e.message;
});

const linkCode = new URLSearchParams(location.hash.slice(1)).get("code");
const savedEnrollmentCode = /^[A-Z2-9]{4}-[A-Z2-9]{4}$/.test(linkCode || "")
  ? linkCode
  : sessionStorage.getItem("tasktrack-enrollment-code");
if (linkCode)
  history.replaceState(null, "", location.pathname + location.search);
if (savedEnrollmentCode) {
  sessionStorage.removeItem("tasktrack-enrollment-code");
  document.querySelector("#agent-code").value = savedEnrollmentCode;
  document.querySelector("#agent-code-form").requestSubmit();
}

const savedEnrollmentStatus = sessionStorage.getItem(progressKey);
if (savedEnrollmentStatus && !savedEnrollmentCode)
  monitorConnection(savedEnrollmentStatus);

setInterval(() => {
  if (!current?.expires_at || document.querySelector("#agent-review").hidden)
    return;
  const seconds = Math.max(
    0,
    Math.ceil(current.expires_at - Date.now() / 1000),
  );
  const expiry = document.querySelector("#agent-expiry");
  expiry.textContent = seconds
    ? `Complete approval within ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}.`
    : "This request expired. Ask your agent for a new connection link.";
  if (!seconds) document.querySelector("#agent-approve").disabled = true;
}, 1000);
window.addEventListener("hashchange", () => {
  const code = new URLSearchParams(location.hash.slice(1)).get("code");
  if (/^[A-Z2-9]{4}-[A-Z2-9]{4}$/.test(code || "")) location.reload();
});

document.querySelector("#agent-change").onclick = () => {
  current = null;
  document.querySelector("#agent-review").hidden = true;
  document.querySelector("#agent-code-form").closest(".setup-primary").hidden =
    false;
  document.querySelector("#agent-code").focus();
};
