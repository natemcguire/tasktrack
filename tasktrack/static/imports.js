"use strict";
const csrf = document.querySelector('meta[name="tt-csrf"]').content,
  workspace = document.querySelector('meta[name="tt-workspace"]').content;
const message = document.querySelector("#import-message");
let source,
  job,
  connected = null,
  cancelled = false;
async function api(path, body, raw = false) {
  const r = await fetch("/api/v1" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "X-CSRF-Token": csrf,
      "X-Workspace-ID": workspace,
      "Idempotency-Key": crypto.randomUUID(),
      ...(raw ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : raw ? body : JSON.stringify(body),
  });
  const value = await r.json();
  if (!r.ok) throw new Error(value.error?.message || "Request failed.");
  return value;
}
function node(tag, text, parent) {
  const n = document.createElement(tag);
  n.textContent = text;
  if (parent) parent.append(n);
  return n;
}
function button(text, parent, handler) {
  const b = node("button", text, parent);
  b.className = "button";
  b.onclick = () => run(b, handler);
  return b;
}
async function run(button, fn) {
  button.disabled = true;
  message.textContent = "";
  try {
    await fn();
  } catch (e) {
    message.textContent = e.message;
  } finally {
    button.disabled = false;
  }
}
function mappings(statuses) {
  const box = document.querySelector("#import-mappings");
  box.replaceChildren();
  for (const [name, current] of statuses) {
    const label = node("label", name, box);
    const s = document.createElement("select");
    s.dataset.status = name;
    for (const [value, title] of [
      ["backlog", "Backlog"],
      ["in_progress", "In progress"],
      ["review", "Review"],
      ["done", "Done"],
    ]) {
      const o = node("option", title, s);
      o.value = value;
      o.selected = value === current;
    }
    label.append(s);
  }
}
function selectedMapping() {
  return Object.fromEntries(
    [...document.querySelectorAll("[data-status]")].map((s) => [
      s.dataset.status,
      s.value,
    ]),
  );
}
function details(value) {
  job = value;
  renderPeople(value);
  document.querySelector("#import-review").hidden = false;
  const box = document.querySelector("#import-summary");
  box.replaceChildren();
  node(
    "p",
    `${value.state} · ${value.cursor} of ${value.total} source records processed`,
    box,
  );
  node(
    "p",
    `Created ${value.report.created}, unchanged ${value.report.unchanged}, conflicts ${value.report.conflicts.length}. Imported ${value.report.comments} comments and ${value.report.attachments} files.`,
    box,
  );
  node("p", "Columns: " + value.columns.join(", "), box);
  node(
    "p",
    `${value.attachments.length} source files must be verified before import.`,
    box,
  );
  if (value.warnings.length) {
    node("h3", "Review these gaps", box);
    const ul = node("ul", "", box);
    for (const warning of value.warnings) node("li", warning, ul);
  }
  for (const conflict of value.report.conflicts)
    node("p", `${conflict.source_id}: ${conflict.reason}`, box);
  if (value.source_archive_url) {
    const a = node("a", "Download original source export", box);
    a.href = value.source_archive_url;
  }
  document.querySelector("#import-commit").disabled = [
    "complete",
    "partial",
    "rolled_back",
  ].includes(value.state);
}
document.querySelector("#import-analyze").onclick = (e) =>
  run(e.target, async () => {
    const f = document.querySelector("#import-file").files[0];
    if (!f) throw new Error("Choose an export file.");
    if (f.size > 2 * 1024 * 1024)
      throw new Error("Choose a file under 2 MiB or use a connected service.");
    const text = await f.text();
    source = f.name.toLowerCase().endsWith(".csv") ? text : JSON.parse(text);
    connected = null;
    const analyzed = await api("/import-analyze", {
      provider: document.querySelector("#import-provider").value,
      source,
    });
    mappings(analyzed.statuses);
    message.textContent = `Found ${analyzed.total} records. Review the destination and status mapping.`;
    document.querySelector("#import-preview").disabled = false;
  });
document.querySelector("#import-preview").onclick = (e) =>
  run(e.target, async () => {
    const common = {
      project_id: Number(document.querySelector("#import-project").value),
      mapping: selectedMapping(),
      allow_visibility_change:
        document.querySelector("#import-visibility").checked,
    };
    if (!common.project_id)
      throw new Error("Create or choose a destination project first.");
    message.textContent = connected
      ? "Reading provider records and files…"
      : "Preparing preview…";
    const value = connected
      ? await fetchSnapshot({
          connection_id: connected.id,
          ...common,
          account_id: connected.account,
          source_project: connected.project,
        })
      : await api("/import-previews", {
          ...common,
          provider: document.querySelector("#import-provider").value,
          source,
        });
    details(value);
    message.textContent =
      "Preview ready. Review gaps and files before importing.";
    await jobs();
  });
document.querySelector("#import-upload").onclick = (e) =>
  run(e.target, async () => {
    if (!job) throw new Error("Prepare a preview first.");
    for (const file of document.querySelector("#import-files").files) {
      const raw = await file.arrayBuffer();
      const sha = [
        ...new Uint8Array(await crypto.subtle.digest("SHA-256", raw)),
      ]
        .map((x) => x.toString(16).padStart(2, "0"))
        .join("");
      await api("/import-jobs/" + job.id + "/files/" + sha, raw, true);
    }
    message.textContent = "Selected files verified.";
  });
document.querySelector("#import-commit").onclick = (e) =>
  run(e.target, async () => {
    cancelled = false;
    do {
      details(
        await api(
          "/import-jobs/" +
            job.id +
            (job.state === "cancelled" ? "/resume" : "/commit"),
          { digest: job.digest },
        ),
      );
      message.textContent = `Processed ${job.cursor} of ${job.total} records.`;
      await new Promise((r) => setTimeout(r, 100));
    } while (job.state === "running" && !cancelled);
    await jobs();
  });
document.querySelector("#import-cancel").onclick = (e) =>
  run(e.target, async () => {
    cancelled = true;
    details(
      await api("/import-jobs/" + job.id + "/cancel", { digest: job.digest }),
    );
    message.textContent =
      "Pending work cancelled. Already imported records remain available.";
    await jobs();
  });
document.querySelector("#import-report").onclick = () => {
  if (!job) return;
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(job, null, 2)], { type: "application/json" }),
  );
  const a = node("a", "");
  a.href = url;
  a.download = "tasktrack-import-" + job.id + ".json";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
async function jobs() {
  const result = await api("/import-jobs"),
    box = document.querySelector("#import-jobs");
  box.replaceChildren();
  for (const item of result.items)
    button(
      `${item.provider} · ${item.state} · ${item.cursor}/${item.total}`,
      box,
      async () => details(await api("/import-jobs/" + item.id)),
    );
}
async function connections() {
  const box = document.querySelector("#connection-list");
  box.replaceChildren();
  for (const conn of (await api("/import-connections")).items) {
    const section = node("section", "", box);
    node("h3", conn.provider, section);
    for (const account of conn.accounts) {
      button("Choose project from " + account.name, section, async () => {
        const result = await api(
          "/import-connections/" +
            conn.id +
            "/projects?account_id=" +
            encodeURIComponent(account.id),
        );
        const choices = node("div", "", section);
        for (const p of result.items)
          button(p.name, choices, async () => {
            connected = {
              id: conn.id,
              account: account.id,
              project: String(p.id),
            };
            source = null;
            const statusResult = await api(
              "/import-connections/" +
                conn.id +
                "/statuses?account_id=" +
                encodeURIComponent(account.id) +
                "&source_project=" +
                encodeURIComponent(p.id),
            );
            mappings(statusResult.statuses);
            document.querySelector("#import-preview").disabled = false;
            message.textContent =
              "Selected " +
              p.name +
              ". Review the destination and column phases, then prepare a preview.";
          });
      });
    }
    button("Disconnect", section, async () => {
      await api("/import-connections/" + conn.id + "/disconnect", {});
      await connections();
    });
  }
}
async function init() {
  let cursor;
  do {
    const result = await api(
      "/projects?limit=200" +
        (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""),
    );
    for (const project of result.items) {
      const o = node(
        "option",
        project.name,
        document.querySelector("#import-project"),
      );
      o.value = project.id;
    }
    cursor = result.has_more ? result.next_cursor : null;
  } while (cursor);
  const box = document.querySelector("#provider-buttons");
  for (const p of (await api("/import-providers")).items) {
    const b = button("Connect " + p.provider, box, async () => {
      const result = await api(
        "/import-connections/" + p.provider + "/authorize",
        {},
      );
      location.assign(result.url);
    });
    b.disabled = !p.configured;
    if (!p.configured)
      node(
        "p",
        p.provider +
          " connection is not enabled yet. Export-file import is available.",
        box,
      );
  }
  await Promise.all([connections(), jobs()]);
}
init().catch((e) => (message.textContent = e.message));

const decode = (s) =>
  Uint8Array.from(atob(s.replaceAll("-", "+").replaceAll("_", "/")), (c) =>
    c.charCodeAt(0),
  );
const encode = (b) =>
  btoa(String.fromCharCode(...new Uint8Array(b)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
document.querySelector("#import-rollback").onclick = (e) =>
  run(e.target, async () => {
    if (!job) throw new Error("Choose an import first.");
    const path = "/import-jobs/" + job.id + "/rollback";
    const preview = await api(path);
    if (preview.conflicts.length)
      throw new Error(
        "These records changed after import and cannot be rolled back: " +
          preview.conflicts.join(", "),
      );
    if (
      !confirm(
        "Archive " +
          preview.task_ids.length +
          " unchanged imported records? Files and source history are retained. Task IDs: " +
          preview.task_ids.join(", "),
      )
    )
      return;
    const challenge = await api(path + "/challenge", {});
    const o = challenge.options;
    const k = await navigator.credentials.get({
      publicKey: {
        ...o,
        challenge: decode(o.challenge),
        allowCredentials: o.allowCredentials.map((c) => ({
          ...c,
          id: decode(c.id),
        })),
      },
    });
    const credential = {
      id: k.id,
      rawId: encode(k.rawId),
      type: k.type,
      response: {
        clientDataJSON: encode(k.response.clientDataJSON),
        authenticatorData: encode(k.response.authenticatorData),
        signature: encode(k.response.signature),
        userHandle: k.response.userHandle
          ? encode(k.response.userHandle)
          : null,
      },
      clientExtensionResults: k.getClientExtensionResults(),
    };
    details(
      await api(path + "/decision", {
        digest: job.digest,
        challenge_id: challenge.challenge_id,
        credential,
      }),
    );
    await jobs();
    message.textContent =
      "Unchanged imported work archived. Source mappings and history are retained.";
  });

let peopleMembers;
async function renderPeople(value) {
  const box = document.querySelector("#import-people");
  box.replaceChildren();
  const save = document.querySelector("#import-people-save");
  save.hidden = value.state !== "preview" || !value.people?.length;
  if (save.hidden) return;
  try {
    peopleMembers ||= (await api("/import-people")).items;
    if (job.id !== value.id) return;
    node("h3", "Map source assignees", box);
    for (const person of value.people) {
      const label = node("label", person.name, box);
      const select = document.createElement("select");
      select.dataset.person = person.id;
      const none = node("option", "Leave unassigned", select);
      none.value = "";
      for (const member of peopleMembers) {
        const option = node(
          "option",
          member.name + " (" + member.email + ")",
          select,
        );
        option.value = member.id;
        option.selected = value.people_mapping?.[person.id] === member.email;
      }
      label.append(select);
    }
  } catch (e) {
    message.textContent = e.message;
  }
}
document.querySelector("#import-people-save").onclick = (e) =>
  run(e.target, async () => {
    const mapping = Object.fromEntries(
      [...document.querySelectorAll("[data-person]")].map((s) => [
        s.dataset.person,
        s.value || null,
      ]),
    );
    details(
      await api("/import-jobs/" + job.id + "/people", {
        digest: job.digest,
        mapping,
      }),
    );
    message.textContent = "People mapping saved. Review and import.";
  });

async function fetchSnapshot(data) {
  const pending = await api("/import-fetches", data);
  localStorage.setItem("tasktrack-fetch:" + workspace, pending.id);
  return resumeFetch(pending.id);
}
async function resumeFetch(id) {
  let state;
  do {
    state = await api("/import-fetches/" + id + "/resume", {});
    message.textContent = `Reading source: ${state.requests_done} requests saved. You can close this page and resume later.`;
  } while (state.state !== "complete");
  localStorage.removeItem("tasktrack-fetch:" + workspace);
  return state.result;
}
async function showSavedFetches() {
  const box = document.querySelector("#import-fetches");
  try {
    const saved = await api("/import-fetches");
    box.replaceChildren();
    for (const fetch of saved.items.filter((f) =>
      ["pending", "fetching"].includes(f.state),
    )) {
      const row = document.createElement("div");
      box.append(row);
      node(
        "p",
        `Saved source fetch · ${fetch.requests_done} requests · ${new Date(fetch.created_at * 1000).toLocaleString()}`,
        row,
      );
      button("Resume saved source fetch", row, async () => {
        details(await resumeFetch(fetch.id));
        await jobs();
        row.remove();
      });
      button("Cancel saved source fetch", row, async () => {
        await api("/import-fetches/" + fetch.id + "/cancel", {});
        if (localStorage.getItem("tasktrack-fetch:" + workspace) === fetch.id)
          localStorage.removeItem("tasktrack-fetch:" + workspace);
        message.textContent = "Source fetch cancelled.";
        row.remove();
      });
    }
  } catch (e) {
    message.textContent = e.message;
  }
}
showSavedFetches();
