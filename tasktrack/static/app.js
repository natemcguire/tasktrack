"use strict";
const hosted = document.querySelector('meta[name="tt-context"]');
const account = hosted ? JSON.parse(hosted.content) : null;
const $ = (q, root = document) => root.querySelector(q);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const states = {
  backlog: "Backlog",
  in_progress: "In progress",
  review: "Review",
  done: "Done",
};
const state = {
  projects: [],
  project: null,
  task: null,
  view: "board",
  filters: {},
  columns: {},
  mobile: "backlog",
  generation: 0,
};
const actor = $("#actor"),
  session = $("#session");
actor.value = account?.actor || localStorage.getItem("tt_actor") || "";
session.value = account
  ? sessionStorage.getItem("tt_browser_session") || crypto.randomUUID()
  : localStorage.getItem("tt_session") || "";
if (account) {
  sessionStorage.setItem("tt_browser_session", session.value);
  actor.readOnly = true;
  actor.closest("label").hidden = true;
  session.closest("label").hidden = true;
  const link = document.createElement("a");
  link.href = "/account";
  link.className = "button";
  link.textContent = "Account";
  document.querySelector(".identity").append(link);
  document.querySelector(".sidebar-foot").textContent = account.workspace_name;
}
actor.addEventListener("change", () =>
  localStorage.setItem("tt_actor", actor.value.trim()),
);
session.addEventListener("change", () =>
  localStorage.setItem("tt_session", session.value.trim()),
);
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").classList.add("visible");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $("#toast").classList.remove("visible"), 4000);
}
function errorText(error) {
  return error.fields && Object.keys(error.fields).length
    ? Object.values(error.fields).join(" ")
    : error.message;
}
async function api(
  path,
  { method = "GET", body, requestId = crypto.randomUUID() } = {},
) {
  const headers = { "X-Via": "ui" };
  if (account) {
    headers["X-CSRF-Token"] = account.csrf;
    headers["X-Workspace-ID"] = account.workspace_id;
  }
  if (actor.value.trim()) headers["X-Actor"] = actor.value.trim();
  if (session.value.trim()) headers["X-Session"] = session.value.trim();
  if (method !== "GET") {
    if (!headers["X-Actor"])
      throw {
        code: "missing_actor",
        message: "Enter your name in “Acting as” before saving.",
      };
    headers["Idempotency-Key"] = requestId;
    if (!(body instanceof FormData))
      headers["Content-Type"] = "application/json";
  }
  let response;
  try {
    response = await fetch("/api/v1" + path, {
      method,
      headers,
      body:
        body === undefined
          ? undefined
          : body instanceof FormData
            ? body
            : JSON.stringify(body),
    });
  } catch {
    throw {
      code: "connection_error",
      message:
        "Could not reach Tasktrack. Your input is preserved; retry when the service is available.",
    };
  }
  const data = await response.json();
  if (!response.ok) throw { ...data.error, status: response.status };
  return data;
}
async function allPages(path) {
  let items = [],
    cursor;
  do {
    const page = await api(
      path +
        (path.includes("?") ? "&" : "?") +
        new URLSearchParams({ limit: 200, ...(cursor ? { cursor } : {}) }),
    );
    items.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor);
  return items;
}
function safeLink(uri) {
  try {
    const u = new URL(uri);
    return ["http:", "https:", "file:"].includes(u.protocol) ? esc(uri) : "#";
  } catch {
    return "#";
  }
}
function inline(text) {
  return esc(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\(([^\s)]+)\)/g, (_, label, uri) => {
      const decoded = uri
        .replace(/&amp;/g, "&")
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'");
      return `<a href="${safeLink(decoded)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });
}
function markdown(text) {
  let html = "",
    inCode = false,
    inList = false;
  for (const line of String(text || "").split("\n")) {
    if (line.startsWith("```")) {
      if (inList) {
        html += "</ul>";
        inList = false;
      }
      html += inCode ? "</code></pre>" : "<pre><code>";
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      html += esc(line) + "\n";
      continue;
    }
    if (/^[-*] /.test(line)) {
      if (!inList) {
        html += "<ul>";
        inList = true;
      }
      html += `<li>${inline(line.slice(2))}</li>`;
      continue;
    }
    if (inList) {
      html += "</ul>";
      inList = false;
    }
    const heading = line.match(/^(#{1,3}) (.*)$/);
    if (heading) {
      const n = heading[1].length;
      html += `<h${n}>${inline(heading[2])}</h${n}>`;
    } else if (line.trim()) html += `<p>${inline(line)}</p>`;
  }
  return html + (inCode ? "</code></pre>" : "") + (inList ? "</ul>" : "");
}
const date = (value) =>
  value
    ? new Date(value).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "—";
const options = (values, selected = "") =>
  values
    .map(
      ([v, label]) =>
        `<option value="${esc(v)}" ${String(v) === String(selected) ? "selected" : ""}>${esc(label)}</option>`,
    )
    .join("");
let fieldSequence = 0;
function field(name, label, value = "", type = "text", hint = "") {
  const id = "field-" + ++fieldSequence,
    attrs = `id="${id}" name="${name}"${hint ? ` aria-describedby="${id}-hint"` : ""}`;
  return `<div class="field"><label for="${id}">${label}</label>${type === "textarea" ? `<textarea ${attrs} rows="4">${esc(value)}</textarea>` : `<input ${attrs} type="${type}" value="${esc(value)}">`}${hint ? `<small id="${id}-hint">${hint}</small>` : ""}</div>`;
}
function select(name, label, values, selected = "") {
  const id = "field-" + ++fieldSequence;
  return `<div class="field"><label for="${id}">${label}</label><select id="${id}" name="${name}">${options(values, selected)}</select></div>`;
}
function jsonField(name, label, value, hint = "") {
  return field(
    name,
    label,
    JSON.stringify(value ?? [], null, 2),
    "textarea",
    hint,
  );
}
function linkRow(name, item = {}) {
  return `<div class="link-row">${field(name + "_label", "Label", item.label || "")}${field(name + "_uri", "URL", item.uri || "", "text")}<button class="icon-button" type="button" data-remove-link aria-label="Remove link">×</button></div>`;
}
function linksField(name, label, items = []) {
  return `<fieldset class="links-field" data-links="${name}"><legend>${label}</legend><div class="link-rows">${(items.length ? items : [{}]).map((x) => linkRow(name, x)).join("")}</div><button class="button" type="button" data-add-link="${name}">＋ Add link</button><p class="small muted">Use a web URL or a file:/// reference.</p></fieldset>`;
}
function getLinks(form, name) {
  const labels = form.getAll(name + "_label"),
    uris = form.getAll(name + "_uri");
  return labels
    .map((label, i) => ({ label: label.trim(), uri: uris[i].trim() }))
    .filter((x) => x.label || x.uri);
}
document.addEventListener("click", (event) => {
  const add = event.target.closest("[data-add-link]"),
    remove = event.target.closest("[data-remove-link]");
  if (add) {
    const group = add.closest("[data-links]");
    $(".link-rows", group).insertAdjacentHTML(
      "beforeend",
      linkRow(add.dataset.addLink),
    );
    $(".link-rows", group).lastElementChild.querySelector("input").focus();
  }
  if (remove) remove.closest(".link-row").remove();
});
function parseJSON(form, name) {
  try {
    return JSON.parse(form.get(name) || "[]");
  } catch {
    throw {
      message: `${name.replaceAll("_", " ")} must be valid JSON. Your input is preserved.`,
    };
  }
}
function parseLines(value) {
  return String(value || "")
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}
function evidenceHTML(items = []) {
  return items.length
    ? `<ul>${items.map((x) => `<li><a href="${safeLink(x.uri)}" target="_blank" rel="noopener noreferrer">${esc(x.label)}</a></li>`).join("")}</ul>`
    : "";
}
function showForm(
  title,
  html,
  save,
  { record, recordPath, button = "Save", keepOpen = false } = {},
) {
  const dialog = $("#editor"),
    form = $("#editor-form");
  $("#dialog-title").textContent = title;
  $("#dialog-fields").innerHTML = html;
  $("#form-error").innerHTML = "";
  $("#save-dialog").textContent = button;
  $("#save-dialog").hidden = false;
  $("#cancel-dialog").textContent = "Cancel";
  $("#save-dialog").disabled = false;
  let version = record?.version,
    requestId = crypto.randomUUID(),
    lastBody = null;
  form.onsubmit = async (event) => {
    event.preventDefault();
    const buttonEl = $("#save-dialog");
    buttonEl.disabled = true;
    $("#form-error").innerHTML = "";
    const data = new FormData(form);
    const serialized = JSON.stringify(
      [...data].map(([k, v]) => [
        k,
        v instanceof File ? [v.name, v.size, v.lastModified] : v,
      ]),
    );
    if (lastBody !== null && serialized !== lastBody)
      requestId = crypto.randomUUID();
    lastBody = serialized;
    try {
      await save(data, version, requestId);
      if (!keepOpen) {
        dialog.close();
        await route();
      }
    } catch (error) {
      $("#form-error").innerHTML = `<p>${esc(errorText(error))}</p>`;
      if (error.code === "version_conflict" && recordPath) {
        $("#form-error").insertAdjacentHTML(
          "beforeend",
          '<p>Your unsaved input is still here. Review the saved record before applying your changes.</p><button type="button" class="button" id="review-conflict">Review latest version</button>',
        );
        $("#review-conflict").onclick = async () => {
          try {
            const current = await api(recordPath);
            version = current.version;
            requestId = crypto.randomUUID();
            $("#form-error").innerHTML =
              `<p>Latest saved version: ${version}. Compare it with your input, then save when ready.</p><pre>${esc(JSON.stringify(current, null, 2))}</pre>`;
          } catch (e) {
            $("#form-error").textContent = errorText(e);
          }
        };
      } else if (error.code !== "connection_error" && error.status !== 503)
        requestId = crypto.randomUUID();
    } finally {
      buttonEl.disabled = false;
    }
  };
  dialog.showModal();
}
$("#close-dialog").onclick = $("#cancel-dialog").onclick = () =>
  $("#editor").close();
async function navigate(path) {
  history.pushState({}, "", path);
  state.view = "board";
  state.filters = {};
  await route();
}
document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-nav]");
  if (link && !event.metaKey && !event.ctrlKey) {
    event.preventDefault();
    navigate(link.getAttribute("href"));
  }
});
window.addEventListener("popstate", () => {
  state.view = "board";
  state.filters = {};
  route();
});
async function loadProjects() {
  state.projects = await allPages("/projects");
  $("#projects").innerHTML = state.projects
    .map(
      (p) =>
        `<a class="project-link ${state.project?.id === p.id ? "selected" : ""}" href="/projects/${esc(p.key)}" data-nav ${state.project?.id === p.id ? 'aria-current="page"' : ""}><span class="project-initial">${esc(p.key[0])}</span>${esc(p.name)}</a>`,
    )
    .join("");
}
$("#add-project").onclick = () => projectForm();
function projectForm(project = null) {
  const links = project?.document_links || [];
  showForm(
    project ? "Project settings" : "Create a project",
    `<div class="form-grid">${field("key", "Project key", project?.key || "", "text", "1–10 letters or numbers, starting with a letter.")}${field("name", "Project name", project?.name || "")}</div>${field("brief_markdown", "Project brief / PRD", project?.brief_markdown || "", "textarea", "Markdown is supported. Explain why this work matters.")}${linksField("document_links", "External documents", links)}`,
    async (f, version, requestId) => {
      const body = {
        key: f.get("key"),
        name: f.get("name"),
        brief_markdown: f.get("brief_markdown"),
        document_links: getLinks(f, "document_links"),
        ...(project ? { expected_version: version } : {}),
      };
      const saved = await api(
        project ? `/projects/${project.id}` : "/projects",
        { method: project ? "PATCH" : "POST", body, requestId },
      );
      state.project = saved;
      history.replaceState({}, "", `/projects/${saved.key}`);
      toast(
        project
          ? "Project settings saved. Earlier links still work."
          : "Project created.",
      );
    },
    {
      record: project,
      recordPath: project ? `/projects/${project.id}` : null,
      button: project ? "Save settings" : "Create project",
    },
  );
}
async function taskForm(task = null, kind = "task") {
  try {
    const epics = await allPages(
      `/tasks?project_id=${state.project.id}&kind=epic`,
    );
    const links = (task?.thread_links || []).map(
      ({ source, project, thread_id, primary }) => ({
        source,
        project,
        thread_id,
        primary,
      }),
    );
    showForm(
      task
        ? `Edit ${task.reference}`
        : kind === "epic"
          ? "Create an epic"
          : "Create a task",
      `${field("title", "Title", task?.title || "")}${field("description_markdown", kind === "epic" ? "Epic PRD / description" : "Description", task?.description_markdown || "", "textarea", "Markdown is supported. Backlog drafts can be incomplete.")}${field("acceptance_criteria", "Acceptance criteria", task?.acceptance_criteria?.join("\n") || "", "textarea", "One criterion per line. Required before work starts.")}<div class="form-grid">${field("assignee", "Assignee", task?.assignee || "")}${select(
        "priority",
        "Priority",
        ["low", "normal", "high", "urgent"].map((x) => [
          x,
          x[0].toUpperCase() + x.slice(1),
        ]),
        task?.priority || "normal",
      )}</div>${(task?.kind || kind) === "task" ? select("parent_id", "Parent epic", [["", "No epic"], ...epics.map((t) => [t.id, t.title])], task?.parent_id || "") : ""}${field("dependency_ids", "Dependency task IDs", task?.dependency_ids?.join(", ") || "", "text", "Comma-separated IDs of prerequisite tasks in this project.")}<details><summary>Conversation links</summary>${jsonField("thread_links", "Linked inbox threads", links, 'List of {"source": "inbox UUID", "project": "slug", "thread_id": "…", "primary": true}. Configure the inbox browser URL in config.json.')}</details>`,
      async (f, version, requestId) => {
        const body = {
          title: f.get("title"),
          description_markdown: f.get("description_markdown"),
          acceptance_criteria: parseLines(f.get("acceptance_criteria")),
          assignee: f.get("assignee").trim() || null,
          priority: f.get("priority"),
          parent_id: f.get("parent_id") ? Number(f.get("parent_id")) : null,
          dependency_ids: f
            .get("dependency_ids")
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
            .map(Number),
          thread_links: parseJSON(f, "thread_links"),
          ...(task
            ? { expected_version: version }
            : { project_id: state.project.id, kind }),
        };
        await api(task ? `/tasks/${task.id}` : "/tasks", {
          method: task ? "PATCH" : "POST",
          body,
          requestId,
        });
        toast(
          task
            ? "Task saved."
            : kind === "epic"
              ? "Epic created."
              : "Task created.",
        );
      },
      {
        record: task,
        recordPath: task ? `/tasks/${task.id}` : null,
        button: task ? "Save task" : "Create",
      },
    );
  } catch (error) {
    toast(errorText(error));
  }
}
function checkpointFields(task) {
  const cp = task.checkpoint || {};
  return `${field("summary", "Progress summary", cp.summary || "", "textarea")}${field("next_action", "Next action", cp.next_action || "", "textarea")}<div class="form-grid">${field("workspace", "Workspace", cp.workspace || "")}${field("branch", "Branch", cp.branch || "")}</div>${field("commit", "Commit", cp.commit || "")}${field("not_applicable_reason", "If this is not code work", cp.not_applicable_reason || "", "text", "Explain why workspace and branch/commit do not apply.")}${field("acceptance_remaining", "Acceptance criteria still remaining", cp.acceptance_remaining?.join("\n") || "", "textarea")}${linksField("checkpoint_evidence", "Progress evidence", cp.evidence || [])}`;
}
function getCheckpoint(f) {
  return {
    summary: f.get("summary"),
    next_action: f.get("next_action"),
    workspace: f.get("workspace"),
    branch: f.get("branch"),
    commit: f.get("commit"),
    not_applicable_reason: f.get("not_applicable_reason"),
    acceptance_remaining: parseLines(f.get("acceptance_remaining")),
    evidence: getLinks(f, "checkpoint_evidence"),
  };
}
function transitionAction(from, to) {
  return from === to
    ? "reorder"
    : {
        "backlog:in_progress": "start",
        "in_progress:review": "submit",
        "review:done": "complete",
        "review:in_progress": "request-changes",
        "in_progress:backlog": "backlog",
        "review:backlog": "backlog",
        "done:backlog": "reopen",
      }[`${from}:${to}`] || "invalid";
}
function actionFields(task, action) {
  let html = "";
  if (["checkpoint", "handoff", "backlog"].includes(action))
    html += checkpointFields(task);
  if (action === "handoff")
    html += field(
      "to",
      "Hand off to",
      task.assignee || "",
      "text",
      "Leave blank to make this task available for pickup.",
    );
  if (action === "reassign")
    html += field(
      "assignee",
      "New assignee",
      task.assignee || "",
      "text",
      "Leave blank to unassign. This explicitly clears any execution claim.",
    );
  if (
    [
      "backlog",
      "reassign",
      "block",
      "unblock",
      "request-changes",
      "reopen",
      "archive",
      "restore",
    ].includes(action)
  )
    html += field("reason", "Reason", "", "textarea");
  if (action === "submit")
    html +=
      field(
        "result_summary",
        "Result summary",
        task.result?.summary || "",
        "textarea",
      ) +
      linksField(
        "result_evidence",
        "Result evidence",
        task.result?.evidence || [],
      );
  if (action === "complete")
    html +=
      field(
        "acceptance_note",
        "Acceptance note",
        "",
        "textarea",
        "Describe how the acceptance criteria were satisfied.",
      ) + evidenceHTML(task.result?.evidence);
  if (action === "reopen" && task.parent_id)
    html +=
      '<label class="checkbox-field"><input name="reopen_parent" type="checkbox">Also reopen the parent epic if it is complete</label>';
  if (action === "claim" || action === "resume")
    html +=
      field(
        "execution_session",
        "Execution session",
        session.value,
        "text",
        "Choose an explicit identifier for this agent session.",
      ) +
      '<p class="muted">This records your actor and execution session. A claim does not reserve files.</p>';
  if (action === "start")
    html +=
      '<p class="muted">Start this assigned task. A description, acceptance criteria, and resolved blockers are required.</p>';
  if (action === "invalid")
    html +=
      '<p class="error-box">This transition is not permitted. Work goes through In progress and Review before Done.</p>';
  return html;
}
function actionBody(f, action) {
  const body = {};
  if (["checkpoint", "handoff", "backlog"].includes(action))
    body.checkpoint = getCheckpoint(f);
  if (action === "handoff") body.to = f.get("to").trim() || null;
  if (action === "reassign") body.assignee = f.get("assignee").trim() || null;
  if (f.has("reason")) body.reason = f.get("reason");
  if (action === "submit")
    body.result = {
      summary: f.get("result_summary"),
      evidence: getLinks(f, "result_evidence"),
    };
  if (action === "complete") body.acceptance_note = f.get("acceptance_note");
  if (action === "reopen" && f.has("reopen_parent")) body.reopen_parent = true;
  return body;
}
function actionForm(task, action) {
  const titles = {
    claim: "Claim task",
    resume: "Resume in this session",
    checkpoint: "Save checkpoint",
    handoff: "Hand off work",
    reassign: "Reassign task",
    block: "Block task",
    unblock: "Resolve blocker",
    submit: "Submit for review",
    complete: "Complete task",
    "request-changes": "Request changes",
    reopen: "Reopen task",
    archive: "Archive task",
    restore: "Restore task",
  };
  showForm(
    `${titles[action]} · ${task.reference}`,
    actionFields(task, action),
    async (f, version, requestId) => {
      if (f.has("execution_session")) {
        session.value = f.get("execution_session").trim();
        localStorage.setItem("tt_session", session.value);
      }
      await api(`/tasks/${task.id}/${action}`, {
        method: "POST",
        body: { expected_version: version, ...actionBody(f, action) },
        requestId,
      });
      toast("Task updated.");
    },
    { record: task, recordPath: `/tasks/${task.id}`, button: titles[action] },
  );
}
async function moveForm(task, initial = task.status, beforeId = undefined) {
  showForm(
    `Move ${task.reference}`,
    select("status", "Column", Object.entries(states), initial) +
      '<div id="move-requirements"></div>' +
      select("before_id", "Position", [["", "At the end of the column"]]),
    async (f, version, requestId) => {
      const next = f.get("status"),
        act = transitionAction(task.status, next);
      await api(`/tasks/${task.id}/move`, {
        method: "POST",
        body: {
          expected_version: version,
          status: next,
          before_id: f.get("before_id") ? Number(f.get("before_id")) : null,
          ...actionBody(f, act),
        },
        requestId,
      });
      toast("Board updated.");
    },
    { record: task, recordPath: `/tasks/${task.id}`, button: "Move task" },
  );
  let token = 0;
  const refresh = async () => {
    const current = ++token;
    const next = $('[name="status"]', $("#editor")).value;
    const position = $('[name="before_id"]', $("#editor"));
    position.disabled = true;
    $("#save-dialog").disabled = true;
    $("#move-requirements").innerHTML = actionFields(
      task,
      transitionAction(task.status, next),
    );
    try {
      const items = await allPages(
        `/tasks?project_id=${task.project_id}&status=${next}`,
      );
      if (current !== token) return;
      position.innerHTML = options(
        [
          ["", "At the end of the column"],
          ...items
            .filter((t) => t.id !== task.id)
            .map((t) => [t.id, `Before ${t.reference} · ${t.title}`]),
        ],
        beforeId || "",
      );
      position.disabled = false;
      $("#save-dialog").disabled = false;
    } catch (e) {
      if (current === token) {
        $("#form-error").textContent = errorText(e);
        $("#save-dialog").disabled = false;
      }
    }
  };
  $('[name="status"]', $("#editor")).onchange = refresh;
  await refresh();
}
function card(task) {
  return `<article class="card" draggable="true" data-id="${task.id}"><div class="card-top"><span class="reference">${esc(task.reference)}</span><span class="priority ${task.priority}">${task.priority === "urgent" ? "↑↑" : task.priority === "high" ? "↑" : "―"} ${task.priority}</span></div><a href="/tasks/${task.id}" class="card-title" data-nav>${esc(task.title)}</a>${task.kind === "epic" ? `<span class="epic-tag">◇ Epic · ${task.progress.done}/${task.progress.total} complete</span>` : task.epic ? `<span class="epic-tag">◇ ${esc(task.epic.title)}</span>` : ""}${task.blocked ? `<span class="blocked-tag" title="${esc(task.blockers.join("; "))}">⊘ Blocked · ${esc(task.blockers[0])}</span>` : ""}<div class="card-foot"><span class="assignee"><span class="avatar">${esc(task.assignee ? task.assignee.slice(0, 2).toUpperCase() : "—")}</span><span class="assignee-name">${esc(task.assignee || "Unassigned")}</span></span><button class="icon-button card-menu" data-move="${task.id}" aria-label="Move ${esc(task.reference)}">⋯</button></div>${task.pickup_needed ? '<div class="pickup">Ready for pickup</div>' : task.execution ? '<div class="pickup">Session claimed</div>' : ""}</article>`;
}
async function board() {
  const p = state.project;
  const epics = await allPages(`/tasks?project_id=${p.id}&kind=epic`);
  $("#breadcrumb").textContent = `Projects / ${p.key}`;
  $("#main").innerHTML =
    `<div class="page-head"><div><p class="eyebrow">${esc(p.key)} / WORKSPACE</p><h1>${esc(p.name)}</h1><p class="page-description">Keep the next step clear. Give good work a place to land.</p></div><div class="head-actions"><button class="button quiet" id="settings">Settings</button><button class="button" id="new-epic">New epic</button><button class="button primary" id="new-task">＋ New task</button></div></div><div class="view-tabs" role="tablist" aria-label="Project views">${[
      ["board", "Board"],
      ["brief", "Project brief"],
      ["history", "Activity"],
      ["archived", "Archived"],
    ]
      .map(
        ([v, label]) =>
          `<button class="view-tab ${state.view === v ? "active" : ""}" role="tab" aria-selected="${state.view === v}" data-view="${v}">${label}</button>`,
      )
      .join("")}</div><div id="project-view"></div>`;
  $("#settings").onclick = () => projectForm(p);
  $("#new-task").onclick = () => taskForm();
  $("#new-epic").onclick = () => taskForm(null, "epic");
  document.querySelectorAll("[data-view]").forEach(
    (b) =>
      (b.onclick = () => {
        state.view = b.dataset.view;
        board();
      }),
  );
  if (state.view === "brief") {
    $("#project-view").innerHTML =
      `<section class="brief-box"><h2>Project brief</h2><div class="markdown">${markdown(p.brief_markdown) || '<p class="muted">Describe the goal, the constraints, and what success looks like in project settings.</p>'}</div>${evidenceHTML(p.document_links)}</section>`;
    return;
  }
  if (state.view === "history") {
    $("#project-view").innerHTML = '<div id="project-history"></div>';
    await projectHistory();
    return;
  }
  const filters = state.filters;
  $("#project-view").innerHTML =
    `<div class="toolbar" aria-label="Board filters"><label class="search"><span class="filter-label">Search tasks</span><input id="search" placeholder="Search tasks or references…" value="${esc(filters.q || "")}"></label><label class="filter-label" for="filter-assignee">Filter by assignee</label><input id="filter-assignee" placeholder="Assignee" value="${esc(filters.assignee || "")}"><label class="filter-label" for="filter-epic">Filter by epic</label><select id="filter-epic">${options([["", "All epics"], ...epics.map((t) => [t.id, t.title])], filters.parent_id || "")}</select><label class="filter-label" for="filter-priority">Filter by priority</label><select id="filter-priority">${options([["", "All priorities"], ...["low", "normal", "high", "urgent"].map((x) => [x, x[0].toUpperCase() + x.slice(1)])], filters.priority || "")}</select><label class="filter-label" for="filter-blocked">Filter by blocked state</label><select id="filter-blocked">${options(
      [
        ["", "Any blockers"],
        ["true", "Blocked"],
        ["false", "Unblocked"],
      ],
      filters.blocked || "",
    )}</select><button class="button quiet" id="clear-filters">Clear</button></div><label class="filter-label" for="mobile-columns">Board column</label><select id="mobile-columns" class="mobile-columns">${options(Object.entries(states), state.mobile)}</select><div class="board">${Object.entries(
      states,
    )
      .map(
        ([key, title]) =>
          `<section class="column ${key} ${state.mobile === key ? "mobile-active" : ""}" data-status="${key}" aria-label="${title}"><div class="column-head"><h2 class="column-title"><span class="status-dot"></span>${title} <span class="count" id="count-${key}">…</span></h2><button class="icon-button column-add" aria-label="Create backlog draft from ${title}" data-create>+</button></div><div class="cards" id="cards-${key}"></div><div id="more-${key}"></div></section>`,
      )
      .join(
        "",
      )}</div><div class="board-foot"><span id="board-counts">Loading tasks…</span><span>Drag to move · Use a card’s ⋯ menu with a keyboard</span></div>`;
  let timer;
  for (const [id, key] of [
    ["search", "q"],
    ["filter-assignee", "assignee"],
    ["filter-epic", "parent_id"],
    ["filter-priority", "priority"],
    ["filter-blocked", "blocked"],
  ]) {
    $("#" + id).addEventListener(
      id === "search" || id === "filter-assignee" ? "input" : "change",
      (event) => {
        state.filters[key] = event.target.value;
        clearTimeout(timer);
        timer = setTimeout(loadBoard, 200);
      },
    );
  }
  $("#clear-filters").onclick = () => {
    state.filters = {};
    board();
  };
  $("#mobile-columns").onchange = (event) => {
    state.mobile = event.target.value;
    document
      .querySelectorAll(".column")
      .forEach((c) =>
        c.classList.toggle("mobile-active", c.dataset.status === state.mobile),
      );
  };
  document
    .querySelectorAll("[data-create]")
    .forEach((b) => (b.onclick = () => taskForm()));
  await loadBoard();
}
async function loadBoard() {
  const generation = ++state.generation;
  try {
    await Promise.all(
      Object.keys(states).map(async (status) => {
        const query = new URLSearchParams({
          project_id: state.project.id,
          status,
          limit: 20,
          ...Object.fromEntries(
            Object.entries(state.filters).filter(([, v]) => v !== ""),
          ),
          ...(state.view === "archived" ? { archived: "true" } : {}),
        });
        const page = await api("/tasks?" + query);
        if (generation !== state.generation || !$("#cards-" + status)) return;
        state.columns[status] = { ...page, query };
        renderColumn(status);
      }),
    );
    if (generation === state.generation && $("#board-counts")) {
      const cols = Object.values(state.columns);
      $("#board-counts").textContent =
        `${cols.reduce((a, c) => a + c.items.length, 0)} shown · ${cols.reduce((a, c) => a + c.total, 0)} matching · ${cols.reduce((a, c) => a + c.project_total, 0)} total${state.view === "archived" ? " archived" : ""}`;
    }
  } catch (error) {
    toast(errorText(error));
    if ($("#board-counts"))
      $("#board-counts").textContent = "Unable to load. Reload to retry.";
  }
}
function renderColumn(status) {
  const column = state.columns[status];
  $("#count-" + status).textContent = column.total;
  $("#cards-" + status).innerHTML = column.items.length
    ? column.items.map(card).join("")
    : `<div class="empty-column">${state.view === "archived" ? "No archived work" : Object.values(state.filters).some(Boolean) ? "No matching tasks" : "Room for what’s next"}</div>`;
  $("#more-" + status).innerHTML = column.has_more
    ? `<button class="button load-more" id="load-${status}">Load more · ${column.items.length} of ${column.total}</button>`
    : "";
  if (column.has_more)
    $("#load-" + status).onclick = async () => {
      try {
        const query = new URLSearchParams(column.query);
        query.set("cursor", column.next_cursor);
        const more = await api("/tasks?" + query);
        state.columns[status] = {
          ...more,
          query: column.query,
          items: [...column.items, ...more.items],
        };
        renderColumn(status);
        const cols = Object.values(state.columns);
        $("#board-counts").textContent =
          `${cols.reduce((a, c) => a + c.items.length, 0)} shown · ${cols.reduce((a, c) => a + c.total, 0)} matching · ${cols.reduce((a, c) => a + c.project_total, 0)} total`;
      } catch (e) {
        toast(errorText(e));
      }
    };
  const container = $(`[data-status="${status}"]`);
  container.querySelectorAll("[data-move]").forEach(
    (b) =>
      (b.onclick = () => {
        const task = column.items.find((t) => t.id === Number(b.dataset.move));
        if (state.view === "archived") actionForm(task, "restore");
        else moveForm(task);
      }),
  );
  container.querySelectorAll(".card").forEach((el) => {
    el.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("text/plain", el.dataset.id);
      event.dataTransfer.effectAllowed = "move";
    });
    el.addEventListener("dragover", (event) => event.preventDefault());
    el.addEventListener("drop", (event) => {
      event.preventDefault();
      event.stopPropagation();
      drop(event, status, Number(el.dataset.id));
    });
  });
  container.ondragover = (event) => {
    event.preventDefault();
    container.classList.add("drag-over");
  };
  container.ondragleave = () => container.classList.remove("drag-over");
  container.ondrop = (event) => {
    event.preventDefault();
    drop(event, status);
  };
}
function drop(event, status, beforeId) {
  document
    .querySelectorAll(".drag-over")
    .forEach((x) => x.classList.remove("drag-over"));
  const id = Number(event.dataTransfer.getData("text/plain"));
  const task = Object.values(state.columns)
    .flatMap((c) => c.items)
    .find((t) => t.id === id);
  if (task && id !== beforeId) moveForm(task, status, beforeId);
}
function historyRows(items) {
  return items
    .map(
      (e) =>
        `<article class="history-row"><h3>${esc(e.actor)} <span class="muted">· ${esc(e.operation.replaceAll("-", " "))}</span></h3><p>${esc(date(e.created_at))} · ${esc(e.via)}${e.session ? ` · session ${esc(e.session)}` : ""} · #${e.sequence}</p>${e.detail.reason ? `<p>${esc(e.detail.reason)}</p>` : ""}${e.detail.after?.status && e.detail.before?.status !== e.detail.after.status ? `<p>${esc(e.detail.before?.status ? states[e.detail.before.status] + " → " : "")}${esc(states[e.detail.after.status])}</p>` : ""}<details><summary>Recorded details</summary><pre>${esc(JSON.stringify(e.detail, null, 2))}</pre></details></article>`,
    )
    .join("");
}
async function projectHistory(after = 0, append = false) {
  try {
    const health = await api("/health");
    const page = await api(
      "/events?" +
        new URLSearchParams({
          source: health.instance_id,
          after,
          project_id: state.project.id,
          limit: 50,
        }),
    );
    const target = $("#project-history");
    if (!target) return;
    target.querySelector("button")?.remove();
    if (!append) target.innerHTML = "";
    target.insertAdjacentHTML(
      "beforeend",
      historyRows(page.items) ||
        (!append
          ? '<p class="section-empty">No matching events in this page of history.</p>'
          : ""),
    );
    if (page.has_more) {
      target.insertAdjacentHTML(
        "beforeend",
        '<button class="button" id="more-events">Continue through history</button>',
      );
      $("#more-events").onclick = () => projectHistory(page.after, true);
    }
  } catch (e) {
    toast(errorText(e));
  }
}
function property(label, value) {
  return `<div class="property"><span class="property-label">${label}</span><div class="property-value">${value}</div></div>`;
}
async function detail(identifier) {
  const task = await api(`/tasks/${identifier}`);
  state.task = task;
  state.project = await api(`/projects/${task.project_id}`);
  await loadProjects();
  document.title = `${task.reference} · ${task.title} · Tasktrack`;
  $("#breadcrumb").innerHTML =
    `<a href="/projects/${esc(state.project.key)}" data-nav>${esc(state.project.name)}</a> / ${esc(task.reference)}`;
  let actions = task.archived_at
    ? ["restore"]
    : [
        "reassign",
        ...(task.kind === "task" &&
        ["backlog", "in_progress"].includes(task.status)
          ? [task.execution ? "resume" : "claim"]
          : []),
        ...(task.status === "in_progress"
          ? ["checkpoint", "handoff", "submit"]
          : []),
        ...(task.status === "review" ? ["complete", "request-changes"] : []),
        ...(task.status === "done" ? ["reopen"] : []),
        task.blocker_reason ? "unblock" : "block",
        "archive",
      ];
  const labels = {
    reassign: "Reassign",
    claim: "Claim",
    resume: "Resume",
    checkpoint: "Checkpoint",
    handoff: "Handoff",
    submit: "Submit for review",
    complete: "Complete",
    "request-changes": "Request changes",
    reopen: "Reopen",
    block: "Block",
    unblock: "Resolve blocker",
    archive: "Archive",
    restore: "Restore",
  };
  $("#main").innerHTML =
    `<div class="page-head"><div class="task-heading"><p class="eyebrow">${esc(task.reference)} / ${task.kind.toUpperCase()}${task.archived_at ? " / ARCHIVED" : ""}</p><h1>${esc(task.title)}</h1><p class="page-description">${esc(states[task.status])} · Updated ${esc(date(task.updated_at))}</p></div><div class="head-actions">${!task.archived_at ? '<button class="button" id="edit-task">Edit task</button><button class="button primary" id="move-task">Move</button>' : ""}</div></div>${task.blocked ? `<div class="callout warning"><strong>Blocked</strong><br>${task.blockers.map(esc).join("<br>")}</div>` : ""}<div class="inline-actions history-controls">${actions.map((a) => `<button class="button ${a === "archive" ? "quiet" : ""}" data-action="${a}">${labels[a]}</button>`).join("")}</div><div class="task-layout"><div class="task-content"><section class="task-section"><div class="section-head"><h2>${task.kind === "epic" ? "Epic PRD" : "Description"}</h2></div><div class="markdown">${markdown(task.description_markdown) || '<p class="section-empty">This is a draft. Add a description before starting work.</p>'}</div></section><section class="task-section"><div class="section-head"><h2>Acceptance criteria</h2></div>${task.acceptance_criteria.length ? `<ul class="criteria">${task.acceptance_criteria.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : '<p class="section-empty">What will make this task done? Add criteria in Edit task.</p>'}</section>${task.kind === "epic" ? `<section class="task-section"><div class="section-head"><h2>Child tasks · ${task.progress.done}/${task.progress.total} complete</h2></div><div id="children"></div></section>` : ""}<section class="task-section"><div class="section-head"><h2>Latest checkpoint</h2></div>${task.checkpoint ? `<div class="markdown">${markdown(task.checkpoint.summary)}</div><div class="callout"><strong>Next action</strong><br>${esc(task.checkpoint.next_action)}</div><p class="mono">${esc([task.checkpoint.workspace, task.checkpoint.branch, task.checkpoint.commit].filter(Boolean).join(" · ") || task.checkpoint.not_applicable_reason)}</p>${task.checkpoint.acceptance_remaining.length ? `<h3>Still remaining</h3><ul>${task.checkpoint.acceptance_remaining.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}${evidenceHTML(task.checkpoint.evidence)}` : '<p class="section-empty">Save a checkpoint so the next session knows where to begin.</p>'}</section>${task.result ? `<section class="task-section"><div class="section-head"><h2>Result & evidence</h2></div><div class="markdown">${markdown(task.result.summary)}</div>${evidenceHTML(task.result.evidence)}${task.completion ? `<div class="callout"><strong>Accepted by ${esc(task.completion.actor)}</strong><p>${esc(task.completion.acceptance_note)}</p><small>${esc(date(task.completion.completed_at))} · Retained completion record</small></div>` : ""}</section>` : ""}<section class="task-section"><div class="section-head"><h2>Comments</h2><button class="button" id="add-comment">Add comment</button></div><div id="comments"></div></section><section class="task-section"><div class="section-head"><h2>Attachments</h2><button class="button" id="add-attachment">Attach file</button></div><div id="attachments"></div></section><section class="task-section" id="history-section"><div class="section-head"><h2>History</h2><a href="#history-section" class="small">Permanent record</a></div><div id="task-history"></div></section></div><aside class="task-aside" aria-label="Task properties">${property("Status", esc(states[task.status]) + (task.pickup_needed ? '<div class="pickup">Ready for pickup</div>' : ""))}${property("Assignee", esc(task.assignee || "Unassigned"))}${property("Priority", esc(task.priority))}${property("Execution", task.execution ? `${esc(task.execution.actor)}<br><span class="mono">${esc(task.execution.session)}</span><br><small>Claimed ${esc(date(task.execution.claimed_at))}${task.execution.resumed_at ? `<br>Resumed ${esc(date(task.execution.resumed_at))}` : ""}</small>` : "No execution claim")}${property("Project", `<a href="/projects/${esc(state.project.key)}" data-nav>${esc(state.project.name)}</a><br><button class="button quiet" id="show-prd">Read project brief</button>`)}${task.epic ? property("Epic", `<a href="/tasks/${task.epic.id}" data-nav>${esc(task.epic.title)}</a>`) : ""}${property("Dependencies", task.dependencies.length ? task.dependencies.map((d) => `<a href="/tasks/${d.id}" data-nav>${esc(d.title)}</a><br><small>${esc(states[d.status])}</small>`).join("<br>") : "None")}${property("Conversations", task.thread_links.length ? task.thread_links.map((l) => (l.url ? `<a href="${safeLink(l.url)}" target="_blank" rel="noopener noreferrer">Open conversation${l.primary ? " · primary" : ""}</a>` : `<span>${esc(l.thread_id)}<br><small>URL not configured · ${esc(l.project)}</small></span>`)).join("<br>") : "No linked threads")}${property("Record", `Version ${task.version}<br><small>Created by ${esc(task.created_by)} via ${esc(task.created_via)}<br>${esc(date(task.created_at))}</small>`)}</aside></div>`;
  if ($("#edit-task"))
    $("#edit-task").onclick = () => taskForm(task, task.kind);
  if (account) {
    const share = document.createElement("button");
    share.className = "button";
    share.textContent = "Share with customer";
    share.onclick = () => shareTask(task);
    $(".head-actions").append(share);
  }
  if ($("#move-task")) $("#move-task").onclick = () => moveForm(task);
  document
    .querySelectorAll("[data-action]")
    .forEach((b) => (b.onclick = () => actionForm(task, b.dataset.action)));
  $("#show-prd").onclick = async () => {
    history.pushState({}, "", `/projects/${state.project.key}`);
    state.view = "brief";
    await board();
  };
  $("#add-comment").onclick = () =>
    showForm(
      "Add a durable note",
      field(
        "body",
        "Comment",
        "",
        "textarea",
        "Save a decision, context, or next action. Comments are append-only.",
      ),
      async (f, _, requestId) => {
        await api(`/tasks/${task.id}/comments`, {
          method: "POST",
          body: { body: f.get("body") },
          requestId,
        });
        toast("Comment saved.");
      },
      { button: "Add comment" },
    );
  $("#add-attachment").onclick = () =>
    showForm(
      "Attach a file",
      '<div class="field"><label for="upload-file">File</label><input id="upload-file" type="file" name="file" required aria-describedby="upload-hint"><small id="upload-hint">Up to 10 MiB. Attachments download as files.</small></div>' +
        field("comment_id", "Optional comment ID", "", "number"),
      async (f, _, requestId) => {
        if (!f.get("comment_id")) f.delete("comment_id");
        await api(`/tasks/${task.id}/attachments`, {
          method: "POST",
          body: f,
          requestId,
        });
        toast("Attachment saved.");
      },
      { button: "Upload file" },
    );
  await Promise.all([
    loadTaskList("comments"),
    loadTaskList("attachments"),
    loadTaskList("history"),
    ...(task.kind === "epic" ? [loadChildren()] : []),
  ]);
}
async function loadTaskList(type, cursor = null, append = false) {
  const id = state.task.id,
    target = $(type === "history" ? "#task-history" : "#" + type);
  const page = await api(
    `/tasks/${id}/${type}?` +
      new URLSearchParams({ limit: 25, ...(cursor ? { cursor } : {}) }),
  );
  if (state.task.id !== id || !target.isConnected) return;
  target.querySelector(".load-more")?.remove();
  if (!append) target.innerHTML = "";
  let html =
    type === "history"
      ? historyRows(page.items)
      : type === "comments"
        ? page.items
            .map(
              (c) =>
                `<article class="comment"><div class="comment-meta">${esc(c.actor)} · ${esc(date(c.created_at))} · ${esc(c.via)} · Comment #${c.id}</div><div class="markdown">${markdown(c.body)}</div></article>`,
            )
            .join("")
        : page.items
            .map(
              (a) =>
                `<div class="file-row"><a href="${esc(a.download_url)}" download>${esc(a.filename)}</a><small>${(a.size / 1024).toFixed(1)} KiB · ${esc(a.actor)}${a.comment_id ? ` · Comment #${a.comment_id}` : ""}</small></div>`,
            )
            .join("");
  target.insertAdjacentHTML(
    "beforeend",
    html || (!append ? `<p class="section-empty">No ${type} yet.</p>` : ""),
  );
  if (page.has_more) {
    target.insertAdjacentHTML(
      "beforeend",
      `<button class="button load-more">Load more ${type}</button>`,
    );
    target.querySelector(".load-more").onclick = () =>
      loadTaskList(type, page.next_cursor, true).catch((e) =>
        toast(errorText(e)),
      );
  }
}
async function loadChildren(cursor = null, append = false) {
  const id = state.task.id;
  const page = await api(
    "/tasks?" +
      new URLSearchParams({
        project_id: state.task.project_id,
        parent_id: id,
        limit: 25,
        ...(cursor ? { cursor } : {}),
      }),
  );
  const target = $("#children");
  if (!target) return;
  target.querySelector("button")?.remove();
  if (!append) target.innerHTML = "";
  target.insertAdjacentHTML(
    "beforeend",
    page.items
      .map(
        (t) =>
          `<div class="child-row"><a href="/tasks/${t.id}" data-nav>${esc(t.reference)} · ${esc(t.title)}</a><span class="small muted">${esc(states[t.status])}</span></div>`,
      )
      .join("") ||
      '<p class="section-empty">Create tasks and select this epic as their parent.</p>',
  );
  if (page.has_more) {
    target.insertAdjacentHTML(
      "beforeend",
      '<button class="button">Load more children</button>',
    );
    target.querySelector("button").onclick = () =>
      loadChildren(page.next_cursor, true);
  }
}
function taskPreview(task, summary) {
  const canvas = document.createElement("canvas");
  canvas.width = 1200;
  canvas.height = 630;
  const c = canvas.getContext("2d");
  c.fillStyle = "#edf3ef";
  c.fillRect(0, 0, 1200, 630);
  c.fillStyle = "#ffffff";
  c.beginPath();
  c.roundRect(20, 20, 1160, 590, 20);
  c.fill();
  c.fillStyle = "#176b52";
  c.font = "600 22px system-ui";
  c.fillText(task.reference + "  /  TASK UPDATE", 64, 82);
  c.fillStyle = "#edf5f1";
  c.beginPath();
  c.roundRect(64, 110, 220, 44, 22);
  c.fill();
  c.fillStyle = "#176b52";
  c.font = "600 22px system-ui";
  c.fillText(states[task.status], 85, 140);
  function lines(text, y, font, color, height, max) {
    c.font = font;
    c.fillStyle = color;
    const output = [];
    let line = "";
    for (const word of text.trim().split(/\s+/)) {
      const candidate = line ? line + " " + word : word;
      if (line && c.measureText(candidate).width > 1060) {
        output.push(line);
        line = "";
      }
      if (line) line += " ";
      for (const character of word) {
        if (c.measureText(line + character).width > 1060) {
          output.push(line);
          line = "";
        }
        line += character;
      }
    }
    if (line) output.push(line);
    output
      .slice(0, max)
      .forEach((value, index) =>
        c.fillText(
          index === max - 1 && output.length > max
            ? value.slice(0, -2) + "…"
            : value.trim(),
          64,
          y + index * height,
        ),
      );
  }
  lines(task.title, 222, "700 48px system-ui", "#242a2c", 61, 3);
  lines(summary, 422, "400 28px system-ui", "#596761", 39, 3);
  c.fillStyle = "#176b52";
  c.font = "600 23px system-ui";
  c.fillText("tasktrack", 64, 568);
  c.fillStyle = "#687175";
  c.font = "400 20px system-ui";
  c.fillText("Open the link for the latest status", 785, 568);
  return canvas.toDataURL("image/png");
}
async function shareTask(task) {
  try {
    const shares = await api(`/tasks/${task.id}/shares`);
    const existing = shares.items
      .map(
        (s) =>
          `<div class="share-link-row"><a href="${esc(s.url)}" target="_blank" rel="noopener">Open shared task</a><button type="button" class="button quiet" data-revoke-share="${esc(s.id)}">Revoke</button></div>`,
      )
      .join("");
    showForm(
      "Share with customer",
      `<p class="muted">Share this task’s title, current status, and your update. Anyone with the link can view it.</p>${field("summary", "Customer update", "", "textarea", "Write a short update for the customer.")}<img class="share-image" id="share-preview" alt="Task preview"><div id="existing-shares">${existing}</div>`,
      async (form, version, requestId) => {
        const summary = form.get("summary").trim();
        if (!summary)
          throw { message: "Write a short update for the customer." };
        const current = await api(`/tasks/${task.id}`);
        const preview = taskPreview(current, summary);
        const saved = await api(`/tasks/${task.id}/shares`, {
          method: "POST",
          requestId,
          body: {
            summary,
            image: preview.split(",")[1],
            expected_version: current.version,
          },
        });
        $("#dialog-fields").innerHTML =
          `<div class="share-result"><p>Your customer link is ready.</p><img class="share-image" src="${preview}" alt="Shared task snapshot"><label for="shared-url">Customer link</label><input id="shared-url" readonly value="${esc(saved.url)}"><div class="share-buttons"><button type="button" class="button primary" id="copy-share">Copy link</button><a class="button" href="${esc(saved.url)}" target="_blank" rel="noopener">Open customer view</a>${navigator.share ? '<button type="button" class="button" id="native-share">Share…</button>' : ""}</div></div>`;
        $("#save-dialog").hidden = true;
        $("#cancel-dialog").textContent = "Done";
        $("#copy-share").onclick = async () => {
          try {
            await navigator.clipboard.writeText(saved.url);
            $("#copy-share").textContent = "Copied";
          } catch {
            $("#shared-url").select();
            toast("Select and copy the customer link.");
          }
        };
        if ($("#native-share"))
          $("#native-share").onclick = async () => {
            try {
              await navigator.share({ title: current.title, url: saved.url });
            } catch (error) {
              if (error.name !== "AbortError")
                toast("Copy the link to share it.");
            }
          };
      },
      { button: "Create share link", keepOpen: true },
    );
    const summary = $('[name="summary"]', $("#editor"));
    summary.maxLength = 4000;
    const update = () => {
      $("#share-preview").src = taskPreview(
        task,
        summary.value || "Your customer update will appear here.",
      );
    };
    summary.addEventListener("input", update);
    update();
    document.querySelectorAll("[data-revoke-share]").forEach((button) => {
      button.onclick = async () => {
        button.disabled = true;
        try {
          await api(`/tasks/${task.id}/shares/${button.dataset.revokeShare}`, {
            method: "DELETE",
            body: {},
          });
          button.closest(".share-link-row").remove();
          toast("Customer link revoked.");
        } catch (error) {
          toast(errorText(error));
          button.disabled = false;
        }
      };
    });
  } catch (error) {
    toast(errorText(error));
  }
}
async function route() {
  try {
    const path = decodeURIComponent(location.pathname);
    state.generation++;
    if (path.startsWith("/tasks/")) {
      await detail(path.split("/")[2]);
      return;
    }
    if (path.startsWith("/projects/"))
      state.project = await api("/projects/by-key/" + path.split("/")[2]);
    await loadProjects();
    if (!state.projects.length) {
      $("#main").innerHTML =
        '<section class="empty-workspace"><span class="brand-mark" aria-hidden="true">t.</span><p class="eyebrow">A CLEAR PLACE TO START</p><h1>Good work starts with context.</h1><p>Bring projects, requirements, and the next action together. Create your first project to make room for the work.</p><button class="button primary" id="first-project">Create your first project</button></section>';
      $("#first-project").onclick = () => projectForm();
      return;
    }
    if (!state.project) state.project = state.projects[0];
    history.replaceState({}, "", `/projects/${state.project.key}`);
    document.title = `${state.project.name} · Tasktrack`;
    await loadProjects();
    await board();
  } catch (error) {
    $("#main").innerHTML =
      `<section class="empty-workspace"><h1>We couldn’t open this view.</h1><p class="error-box">${esc(errorText(error))}</p><a class="button" href="/">Back to projects</a> <button class="button primary" id="retry">Try again</button></section>`;
    $("#retry").onclick = route;
  }
}
route();
