// Real Worker + D1 + Durable Object SQLite + R2, with a local-only email outbox.
import { chromium, expect } from "@playwright/test";
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import {
  mkdtemp,
  mkdir,
  readdir,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";
import net from "node:net";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temp = await mkdtemp(path.join(tmpdir(), "tasktrack-hosted-"));
const state = path.join(temp, "state");
const artifacts = path.join(root, "docs/screenshots");
await mkdir(artifacts, { recursive: true });
const port = await new Promise((resolve) => {
  const listener = net.createServer();
  listener.listen(0, "127.0.0.1", () => {
    const port = listener.address().port;
    listener.close(() => resolve(port));
  });
});
const url = `http://127.0.0.1:${port}`;
// Wrangler rewrites redirect hosts locally; production host isolation is checked after deploy.
const previewUrl = url;
const env = {
  ...process.env,
  PATH: `${path.join(root, "node_modules/.bin")}${path.delimiter}${process.env.PATH}`,
  WRANGLER_SEND_METRICS: "false",
  CI: "true",
};
for (const key of Object.keys(env)) {
  if (/^(CLOUDFLARE_|CF_|TT_)/.test(key)) delete env[key];
}
let server,
  browser,
  db,
  output = "";
const errors = [],
  checks = [];
const pass = (message) => {
  checks.push(message);
  console.log(`✓ ${message}`);
};
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function start() {
  execFileSync(
    "npx",
    [
      "wrangler",
      "d1",
      "migrations",
      "apply",
      "tasktrack-accounts",
      "--local",
      "--persist-to",
      state,
    ],
    { cwd: root, env, stdio: "pipe" },
  );
  server = spawn(
    "uv",
    [
      "run",
      "--group",
      "cloudflare",
      "pywrangler",
      "dev",
      "--local",
      "--local-upstream",
      `127.0.0.1:${port}`,
      "--port",
      String(port),
      "--persist-to",
      state,
      "--var",
      `PUBLIC_URL:${url}`,
      "--var",
      "LOCAL_EMAIL:1",
      "--var",
      `PREVIEW_URL:${previewUrl}`,
    ],
    { cwd: root, env, detached: true, stdio: ["ignore", "pipe", "pipe"] },
  );
  server.stdout.on("data", (chunk) => {
    output += chunk;
  });
  server.stderr.on("data", (chunk) => {
    output += chunk;
  });
  for (let i = 0; i < 240; i++) {
    if (server.exitCode !== null)
      throw Error(`Worker exited ${server.exitCode}: ${output}`);
    try {
      if ((await fetch(url + "/healthz")).ok) break;
    } catch {}
    if (i === 239) throw Error(`Worker did not start: ${output}`);
    await delay(250);
  }
  const folder = path.join(state, "v3/d1/miniflare-D1DatabaseObject");
  for (const file of await readdir(folder)) {
    if (!file.endsWith(".sqlite")) continue;
    const candidate = new DatabaseSync(path.join(folder, file));
    if (
      candidate
        .prepare("SELECT name FROM sqlite_master WHERE name='local_mail'")
        .get()
    ) {
      db = candidate;
      break;
    }
    candidate.close();
  }
  assert.ok(db, "Local test outbox is available");
}

async function context() {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await ctx.newPage();
  page.on("pageerror", (error) => errors.push(error.stack));
  return { ctx, page };
}
async function refresh(who) {
  await who.page.goto(url);
  who.identity = await who.page
    .locator('meta[name="tt-context"]')
    .evaluate((meta) => JSON.parse(meta.content));
}
async function login(who, email) {
  await who.page.goto(url + "/signup");
  assert.equal(await who.page.locator('input[type="password"]').count(), 0);
  await who.page.getByLabel("Email address").fill(email);
  await who.page.getByRole("button", { name: "Email me a code" }).click();
  await expect(
    who.page.getByRole("heading", { name: "Check your inbox." }),
  ).toBeVisible();
  const body = db
    .prepare(
      "SELECT body FROM local_mail WHERE recipient=? ORDER BY id DESC LIMIT 1",
    )
    .get(email).body;
  const link = body.match(/http:\/\/\S+/)[0];
  // Mail scanners can follow the URL repeatedly without consuming it.
  assert.equal((await who.ctx.request.get(link)).status(), 200);
  assert.equal((await who.ctx.request.get(link)).status(), 200);
  if (email.startsWith("bob")) {
    await who.page.goto(link); // fallback signs in without another click
  } else {
    await who.page
      .getByLabel("Sign-in code")
      .fill(body.match(/code: (\d{6})/)[1]);
  }
  await expect(
    who.page.getByRole("link", { name: "Account", exact: true }),
  ).toBeVisible();
  who.identity = await who.page
    .locator('meta[name="tt-context"]')
    .evaluate((meta) => JSON.parse(meta.content));
  assert.equal(
    (
      await who.ctx.request.post(url + "/auth/verify", {
        form: { token: new URL(link).searchParams.get("token") },
      })
    ).status(),
    400,
  );
  return link;
}
async function request(
  who,
  route,
  {
    method = "GET",
    body,
    status = 200,
    headers = {},
    csrf = true,
    key = crypto.randomUUID(),
  } = {},
) {
  const response = await who.ctx.request.fetch(url + route, {
    method,
    headers: {
      "Idempotency-Key": key,
      ...(csrf && who.identity ? { "X-CSRF-Token": who.identity.csrf } : {}),
      ...headers,
    },
    ...(body === undefined ? {} : { data: body }),
  });
  const text = await response.text();
  assert.equal(response.status(), status, `${method} ${route}: ${text}`);
  return text ? JSON.parse(text) : null;
}
async function api(who, route, options) {
  return request(who, "/api/v1" + route, options);
}
async function createTask(who, project, title, extra = {}) {
  return api(who, "/tasks", {
    method: "POST",
    status: 201,
    body: {
      project_id: project.id,
      title,
      description_markdown:
        "Internal implementation notes: PRIVATE-CUSTOMER-DATA",
      acceptance_criteria: ["Customer can recover a interrupted checkout."],
      ...extra,
    },
  });
}
function cli(token, ...args) {
  return JSON.parse(
    execFileSync("python3", ["-m", "tasktrack", ...args, "--json"], {
      cwd: root,
      env: { ...env, TT_URL: url, TT_TOKEN: token, TT_SESSION: "cli-recovery" },
      encoding: "utf8",
    }),
  );
}

try {
  await start();
  browser = await chromium.launch({ headless: true });
  const alice = await context(),
    bob = await context(),
    customer = await context();
  await alice.page.goto(url + "/signup");
  await alice.page.screenshot({
    path: path.join(artifacts, "hosted-signin.png"),
    fullPage: true,
  });
  await login(alice, "alice@example.invalid");
  await login(bob, "bob@example.invalid");
  assert.notEqual(alice.identity.workspace_id, bob.identity.workspace_id);
  assert.equal(alice.identity.actor, "alice@example.invalid");
  pass(
    "Email-only signup; scanner-safe links are consumed once; each new account gets its own workspace",
  );

  await api(customer, "/projects", { status: 401 });
  await api(alice, "/projects", {
    method: "POST",
    body: { key: "BAD", name: "Bad" },
    csrf: false,
    status: 403,
  });
  await api(alice, "/projects", {
    method: "POST",
    body: {},
    headers: { Origin: "https://other.example" },
    status: 403,
  });
  await alice.page.getByRole("button", { name: "New project" }).click();
  let dialog = alice.page.getByRole("dialog");
  await dialog.getByLabel("Project key").fill("hbr");
  await dialog.getByLabel("Project name").fill("Harbor checkout");
  await dialog
    .getByLabel("Project brief / PRD")
    .fill("# Reliable checkout\nOne order and one receipt, every time.");
  await dialog
    .getByRole("button", { name: "Create project", exact: true })
    .click();
  await expect(
    alice.page.getByRole("heading", { name: "Harbor checkout", exact: true }),
  ).toBeVisible();
  const project = await api(alice, "/projects/HBR");
  const other = await api(bob, "/projects", {
    method: "POST",
    status: 201,
    body: {
      key: "BOB",
      name: "Bob’s private project",
      brief_markdown: "Bob only",
    },
  });
  assert.equal(
    project.id,
    other.id,
    "IDs overlap across independently isolated stores",
  );
  const task = await createTask(
    alice,
    project,
    "A receipt customers can count on",
  );
  const otherTask = await createTask(bob, other, "Bob’s private task");
  assert.equal(task.id, otherTask.id);
  assert.equal((await api(bob, `/tasks/${task.id}`)).title, otherTask.title);
  assert.equal((await api(bob, "/projects")).items.length, 1);
  await api(bob, "/projects/HBR", { status: 404 });
  const extra = await createTask(
    alice,
    project,
    "Keep customer records private",
  );
  await api(bob, `/tasks/${extra.id}`, { status: 404 });
  const history = await api(alice, `/tasks/${task.id}/history`);
  assert.equal(history.items[0].actor, "alice@example.invalid");
  pass(
    "Browser project creation, CSRF protection, and task/project isolation with overlapping IDs",
  );

  await alice.page.goto(url);
  await expect(
    alice.page.getByRole("heading", { name: "Projects", exact: true }),
  ).toBeVisible();
  await alice.page.locator(".triage-entry").click();
  await expect(
    alice.page.getByRole("heading", { name: "TRIAGE", exact: true }),
  ).toBeVisible();
  await expect(alice.page.locator(".triage-item")).toHaveCount(2);
  const published = await request(alice, "/api/account/previews", {
    method: "POST",
    status: 201,
    body: {
      project_id: project.id,
      title: "Private review",
      html: '<!doctype html><h1>Secret design</h1><script>document.body.dataset.interactive="yes"</script>',
    },
  });
  assert.ok(published.url.startsWith(previewUrl));
  await alice.page.goto(published.url);
  await expect(
    alice.page
      .frameLocator("iframe")
      .getByRole("heading", { name: "Secret design" }),
  ).toBeVisible();
  const contentResponse = await alice.ctx.request.get(
    published.url + "/content",
  );
  assert.match(
    contentResponse.headers()["content-security-policy"],
    /sandbox allow-scripts/,
  );
  await bob.page.goto(published.url);
  assert.equal(
    (await bob.page.locator("body").innerText()).includes("Secret design"),
    false,
  );
  await expect(
    bob.page.getByText("This preview is unavailable for your account."),
  ).toBeVisible();
  await alice.page.goto(url + "/projects/HBR");
  await alice.page.setViewportSize({ width: 390, height: 844 });
  await expect(
    alice.page.getByRole("button", { name: "Projects", exact: true }),
  ).toBeVisible();
  assert.ok(
    await alice.page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await alice.page.goto(url + "/tasks/" + task.id);
  assert.ok(
    await alice.page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await alice.page.screenshot({
    path: path.join(artifacts, "hosted-task-mobile.png"),
    fullPage: true,
  });
  await alice.page.setViewportSize({ width: 1440, height: 1000 });
  pass(
    "Project index, recent-work triage, mobile layouts, authenticated preview handoff and cross-tenant denial",
  );

  const key = crypto.randomUUID();
  const claimed = await Promise.all(
    ["first", "second"].map(async (session) => {
      const response = await alice.ctx.request.post(
        url + `/api/v1/tasks/${task.id}/claim`,
        {
          headers: {
            "X-CSRF-Token": alice.identity.csrf,
            "Idempotency-Key": key + session,
            "X-Session": session,
            "X-Actor": "spoofed",
          },
          data: { expected_version: task.version },
        },
      );
      return {
        status: response.status(),
        data: await response.json(),
        session,
      };
    }),
  );
  assert.deepEqual(claimed.map((r) => r.status).sort(), [200, 409]);
  const winner = claimed.find((r) => r.status === 200);
  assert.equal(winner.data.execution.actor, "alice@example.invalid");
  const replay = await api(alice, `/tasks/${task.id}/claim`, {
    method: "POST",
    key: key + winner.session,
    headers: { "X-Session": winner.session },
    body: { expected_version: task.version },
  });
  assert.equal(replay.version, winner.data.version);
  const before = await api(alice, `/tasks/${task.id}/history`);
  await api(alice, `/tasks/${task.id}/complete`, {
    method: "POST",
    body: {
      expected_version: winner.data.version,
      acceptance_note: "Premature",
    },
    status: 422,
  });
  assert.deepEqual(await api(alice, `/tasks/${task.id}/history`), before);
  pass(
    "Concurrent claims have one winner; retries replay one receipt; rejected transitions roll back; actor spoofing fails",
  );

  const epic = await createTask(alice, project, "Dependable checkout", {
    kind: "epic",
    assignee: alice.identity.actor,
  });
  const child = await createTask(alice, project, "Retry once", {
    parent_id: epic.id,
  });
  async function finish(item) {
    let result = await api(
      alice,
      `/tasks/${item.id}/${item.kind === "epic" ? "move" : "claim"}`,
      {
        method: "POST",
        headers: { "X-Session": "finish" },
        body: {
          expected_version: item.version,
          ...(item.kind === "epic" ? { status: "in_progress" } : {}),
        },
      },
    );
    result = await api(alice, `/tasks/${item.id}/submit`, {
      method: "POST",
      headers: { "X-Session": "finish" },
      body: {
        expected_version: result.version,
        result: {
          summary: "Verified.",
          evidence: [{ label: "Check", uri: "https://example.org/checks/1" }],
        },
      },
    });
    return api(alice, `/tasks/${item.id}/complete`, {
      method: "POST",
      body: {
        expected_version: result.version,
        acceptance_note: "Criteria pass.",
      },
    });
  }
  const completedChild = await finish(child),
    completedEpic = await finish(epic);
  const epicHistory = await api(alice, `/tasks/${epic.id}/history`);
  const childBeforeFailure = await api(alice, `/tasks/${child.id}`);
  // Reopen writes the parent first, then rejects the child's ordering anchor.
  // Both the parent write and its event must roll back inside transactionSync.
  await api(alice, `/tasks/${child.id}/move`, {
    method: "POST",
    status: 422,
    body: {
      expected_version: completedChild.version,
      status: "backlog",
      reason: "Regression",
      reopen_parent: true,
      before_id: child.id,
    },
  });
  assert.deepEqual(await api(alice, `/tasks/${epic.id}`), completedEpic);
  assert.deepEqual(await api(alice, `/tasks/${child.id}`), childBeforeFailure);
  assert.deepEqual(await api(alice, `/tasks/${epic.id}/history`), epicHistory);
  pass(
    "A failure after an epic write rolls back parent, child, history and versions together",
  );

  const bytes = Buffer.from("A private attachment.\n");
  const upload = await alice.ctx.request.post(
    url + `/api/v1/tasks/${task.id}/attachments`,
    {
      headers: {
        "X-CSRF-Token": alice.identity.csrf,
        "Idempotency-Key": crypto.randomUUID(),
      },
      multipart: {
        file: { name: "notes.txt", mimeType: "text/plain", buffer: bytes },
      },
    },
  );
  assert.equal(upload.status(), 201, await upload.text());
  const attachment = await upload.json();
  const download = await alice.ctx.request.get(
    url + `/api/v1/attachments/${attachment.id}/content`,
  );
  assert.equal(download.status(), 200);
  assert.deepEqual(await download.body(), bytes);
  await api(bob, `/attachments/${attachment.id}/content`, { status: 404 });
  await api(customer, `/attachments/${attachment.id}/content`, { status: 401 });
  pass(
    "R2 upload/download preserves bytes and enforces workspace authorization",
  );

  await alice.page.goto(`${url}/tasks/${task.id}`);
  await alice.page.getByRole("button", { name: "Share with customer" }).click();
  dialog = alice.page.getByRole("dialog");
  await dialog
    .getByLabel("Customer update")
    .fill(
      "Checkout now keeps your order safe if the connection drops. We’re checking the final details.",
    );
  await dialog
    .getByRole("button", { name: "Create share link", exact: true })
    .click();
  const linkInput = dialog.locator("input[readonly]");
  await expect(linkInput).toBeVisible();
  const shareUrl = await linkInput.inputValue();
  await alice.page.screenshot({
    path: path.join(artifacts, "hosted-share.png"),
  });
  await customer.page.goto(shareUrl);
  await expect(
    customer.page.getByRole("heading", { name: task.title, exact: true }),
  ).toBeVisible();
  const sharedHtml = await (await customer.ctx.request.get(shareUrl)).text();
  assert.ok(!sharedHtml.includes("PRIVATE-CUSTOMER-DATA"));
  assert.ok(!sharedHtml.includes("notes.txt"));
  assert.ok(!sharedHtml.includes(alice.identity.email));
  assert.match(sharedHtml, /property="og:image"/);
  const preview = await customer.ctx.request.get(shareUrl + "/preview.png");
  assert.equal(preview.status(), 200);
  const png = await preview.body();
  assert.equal(png.readUInt32BE(16), 1200);
  assert.equal(png.readUInt32BE(20), 630);
  await writeFile(path.join(artifacts, "task-preview.png"), png);
  await customer.page.setViewportSize({ width: 390, height: 844 });
  await customer.page.screenshot({
    path: path.join(artifacts, "hosted-customer-mobile.png"),
    fullPage: true,
  });
  assert.ok(
    await customer.page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  );
  const shares = await api(alice, `/tasks/${task.id}/shares`);
  assert.equal((await api(bob, `/tasks/${task.id}/shares`)).items.length, 0);
  await api(bob, `/tasks/${task.id}/shares/${shares.items[0].id}`, {
    method: "DELETE",
    body: {},
  });
  assert.equal((await customer.ctx.request.get(shareUrl)).status(), 200);
  const current = await api(alice, `/tasks/${task.id}`);
  await api(alice, `/tasks/${task.id}/submit`, {
    method: "POST",
    headers: { "X-Session": winner.session },
    body: {
      expected_version: current.version,
      result: {
        summary: "Checkout retry checks pass.",
        evidence: [{ label: "Checks", uri: "https://example.org/checks/1" }],
      },
    },
  });
  await customer.page.reload();
  await expect(customer.page.locator(".shared-status")).toHaveText("Review");
  await api(alice, `/tasks/${task.id}/shares/${shares.items[0].id}`, {
    method: "DELETE",
    body: {},
  });
  assert.equal((await customer.ctx.request.get(shareUrl)).status(), 404);
  assert.equal(
    (await customer.ctx.request.get(shareUrl + "/preview.png")).status(),
    404,
  );
  pass(
    "Customer sharing renders metadata and a 1200×630 PNG, hides internal data, tracks current status, and revokes page and image",
  );

  await alice.page.goto(url + "/account");
  await alice.page.getByLabel("Agent name").fill("codex");
  await alice.page
    .getByRole("button", { name: "Create token", exact: true })
    .click();
  const tokenText = alice.page.locator("#token-result pre");
  await expect(tokenText).toBeVisible();
  const token = (await tokenText.textContent()).match(
    /tt_[A-Za-z0-9_-]{43}/,
  )[0];
  assert.equal(cli(token, "project", "get", "HBR").id, project.id);
  const taskFile = path.join(temp, "task.json");
  await writeFile(
    taskFile,
    JSON.stringify({
      title: "Resume a disconnected agent",
      description_markdown: "Keep context across sessions.",
      acceptance_criteria: ["A replacement can continue."],
    }),
  );
  const recovery = cli(token, "task", "create", "HBR", "--file", taskFile);
  assert.equal(
    cli(
      token,
      "task",
      "claim",
      String(recovery.id),
      "--version",
      String(recovery.version),
    ).execution.actor,
    "agent:codex",
  );
  const cp = path.join(temp, "checkpoint.json");
  await writeFile(
    cp,
    JSON.stringify({
      summary: "First pass complete.",
      next_action: "Run the remaining checks.",
      workspace: "/work/tasktrack",
      branch: "feature/recovery",
      acceptance_remaining: ["Resume works"],
      evidence: [{ label: "Log", uri: "https://example.org/checks/2" }],
    }),
  );
  const checkpointed = cli(
    token,
    "task",
    "checkpoint",
    String(recovery.id),
    "--version",
    "2",
    "--file",
    cp,
  );
  assert.equal(
    checkpointed.checkpoint.next_action,
    "Run the remaining checks.",
  );
  assert.equal(
    cli(token, "brief").items.some((t) => t.id === recovery.id),
    true,
  );
  const handed = cli(
    token,
    "task",
    "handoff",
    String(recovery.id),
    "--version",
    String(checkpointed.version),
    "--file",
    cp,
  );
  assert.equal(handed.execution, null);
  const cliFile = path.join(temp, "cli.txt"),
    savedFile = path.join(temp, "download.txt");
  await writeFile(cliFile, bytes);
  const cliAttachment = cli(
    token,
    "task",
    "attach",
    String(recovery.id),
    cliFile,
  );
  cli(token, "task", "download", String(cliAttachment.id), savedFile);
  assert.deepEqual(await readFile(savedFile), bytes);
  const noSettings = await fetch(url + "/api/account/invite", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
    },
    body: "{}",
  });
  assert.equal(noSettings.status, 403);
  await alice.page.reload();
  await alice.page.locator("[data-revoke-token]").click();
  await expect(alice.page.locator("[data-revoke-token]")).toHaveCount(0);
  assert.equal(
    (
      await fetch(url + "/api/v1/projects", {
        headers: { Authorization: "Bearer " + token },
      })
    ).status,
    401,
  );
  pass(
    "Browser-issued agent tokens support the remote CLI, recovery, attachments, and immediate revocation",
  );

  await alice.page
    .getByRole("button", { name: "Create invitation link" })
    .click();
  await expect(alice.page.locator("#invite-result input")).toBeVisible();
  const invitation = await alice.page
    .locator("#invite-result input")
    .inputValue();
  await bob.page.goto(invitation);
  await bob.page.getByRole("button", { name: "Join workspace" }).click();
  await refresh(bob);
  assert.equal(bob.identity.workspace_id, alice.identity.workspace_id);
  assert.equal((await api(bob, "/projects/HBR")).id, project.id);
  assert.equal((await customer.ctx.request.get(invitation)).status(), 404);
  await request(bob, "/api/account/token", {
    method: "POST",
    body: { name: "bypass" },
    status: 403,
  });
  await request(bob, "/api/account/workspace-name", {
    method: "POST",
    body: { name: "bypass" },
    status: 403,
  });
  await bob.page.goto(published.url);
  await expect(
    bob.page.getByText("This preview is unavailable for your account."),
  ).toBeVisible();
  await request(alice, "/api/account/preview-projects/" + project.id, {
    method: "POST",
    body: { emails: ["bob@example.invalid"], team_access: false },
  });
  await bob.page.goto(published.url);
  await expect(
    bob.page
      .frameLocator("iframe")
      .getByRole("heading", { name: "Secret design" }),
  ).toBeVisible();
  await request(alice, "/api/account/preview-projects/" + project.id, {
    method: "POST",
    body: { emails: [], team_access: false },
  });
  assert.equal(
    (await bob.ctx.request.get(published.url + "/content")).status(),
    404,
  );
  await request(alice, "/api/account/preview-projects/" + project.id, {
    method: "POST",
    body: { emails: [], team_access: true },
  });
  assert.equal(
    (await bob.ctx.request.get(published.url + "/content")).status(),
    200,
  );
  await bob.page.goto(url + "/account");
  const previousIdentity = bob.identity;
  await bob.page
    .getByLabel("Open workspace")
    .selectOption({ label: "bob’s workspace" });
  await bob.page.getByRole("button", { name: "Open", exact: true }).click();
  await refresh(bob);
  assert.notEqual(bob.identity.workspace_id, alice.identity.workspace_id);
  assert.equal((await api(bob, "/projects")).items[0].key, "BOB");
  await api(bob, "/projects", {
    headers: { "X-Workspace-ID": previousIdentity.workspace_id },
    status: 409,
  });
  await api(bob, "/projects", {
    method: "POST",
    body: { key: "WRONG", name: "Stale tab" },
    headers: { "X-CSRF-Token": previousIdentity.csrf },
    status: 403,
  });
  await alice.page.reload();
  await alice.page.locator("[data-remove-member]").click();
  await expect(alice.page.locator("[data-remove-member]")).toHaveCount(0);
  assert.equal(
    (await bob.ctx.request.get(published.url + "/content")).status(),
    404,
  );
  const switchBack = await bob.ctx.request.post(url + "/account/switch", {
    form: {
      workspace_id: alice.identity.workspace_id,
      csrf: bob.identity.csrf,
    },
  });
  assert.equal(switchBack.status(), 404);
  assert.equal((await api(bob, "/projects")).items[0].key, "BOB");
  pass(
    "Single-use invitations, member removal, and stale-tab checks preserve workspace isolation",
  );

  const gallery = await request(alice, "/api/account/previews", {
    method: "POST",
    status: 201,
    body: {
      project_id: project.id,
      title: "Design gallery",
      html: await readFile(
        path.join(root, "docs/product/wireframes.html"),
        "utf8",
      ),
    },
  });
  await alice.page.setViewportSize({ width: 390, height: 844 });
  await alice.page.goto(gallery.url + "#invoice");
  const frame = alice.page.frameLocator("iframe");
  await expect(frame.locator("#invoice")).toBeVisible();
  const screens = await frame
    .locator("#screen-select option")
    .evaluateAll((nodes) => nodes.map((n) => n.value));
  for (const screen of screens) {
    await frame.locator("#screen-select").selectOption(screen);
    await expect(frame.locator("#" + screen)).toBeVisible();
    const overflow = await frame
      .locator("body")
      .evaluate((node) => node.scrollWidth > window.innerWidth);
    assert.equal(overflow, false, "Mobile preview overflow: " + screen);
  }
  await frame.locator("#screen-select").selectOption("invoice");
  await alice.page.screenshot({
    path: path.join(artifacts, "hosted-preview-mobile.png"),
    fullPage: true,
  });
  await alice.page.setViewportSize({ width: 1440, height: 1000 });
  pass(
    "Project preview grants, team mode, immediate revocation and all 22 mobile wireframes",
  );

  const expiredSecret = "E".repeat(43);
  const { createHash } = await import("node:crypto");
  db.prepare("INSERT INTO login_links VALUES (?,?,?,?)").run(
    createHash("sha256").update(expiredSecret).digest("hex"),
    "expired@example.invalid",
    "/",
    1,
  );
  assert.equal(
    (
      await customer.ctx.request.post(url + "/auth/verify", {
        form: { token: expiredSecret },
      })
    ).status(),
    400,
  );
  const rateEmail = "rate@example.invalid";
  for (let i = 0; i < 4; i++) {
    assert.equal(
      (
        await customer.ctx.request.post(url + "/auth/link", {
          form: { email: rateEmail },
        })
      ).status(),
      i < 3 ? 200 : 429,
    );
  }
  await alice.page.goto(url + "/account");
  await alice.page
    .getByRole("button", { name: "Sign out", exact: true })
    .click();
  await api(alice, "/projects", { status: 401 });
  await login(alice, "alice@example.invalid");
  assert.equal((await api(alice, "/projects/HBR")).id, project.id);
  assert.equal(
    db
      .prepare(
        "SELECT count(*) AS count FROM workspaces WHERE created_by=(SELECT id FROM users WHERE email=?)",
      )
      .get("alice@example.invalid").count,
    1,
  );
  db.exec("DELETE FROM rate_limits");
  const issueCode = async (email) => {
    const response = await customer.ctx.request.post(url + "/auth/link", {
      form: { email, next: "/triage" },
    });
    assert.equal(response.status(), 200);
    const challenge = (await response.text()).match(
      /name="challenge" value="([^"]+)"/,
    )[1];
    const body = db
      .prepare(
        "SELECT body FROM local_mail WHERE recipient=? ORDER BY id DESC LIMIT 1",
      )
      .get(email).body;
    return {
      challenge,
      code: body.match(/code: (\d{6})/)[1],
      token: new URL(body.match(/http:\/\/\S+/)[0]).searchParams.get("token"),
    };
  };
  const locked = await issueCode("locked@example.invalid");
  for (let i = 0; i < 5; i++)
    assert.equal(
      (
        await customer.ctx.request.post(url + "/auth/code", {
          form: { challenge: locked.challenge, code: "wrong" },
        })
      ).status(),
      400,
    );
  assert.equal(
    (
      await customer.ctx.request.post(url + "/auth/code", { form: locked })
    ).status(),
    400,
  );
  assert.equal(
    (
      await customer.ctx.request.post(url + "/auth/verify", {
        form: { token: locked.token },
        maxRedirects: 0,
      })
    ).status(),
    303,
  );
  const racing = await issueCode("race@example.invalid");
  const attempts = await Promise.all(
    [1, 2].map(() =>
      customer.ctx.request.post(url + "/auth/code", {
        form: { challenge: racing.challenge, code: racing.code },
        maxRedirects: 0,
      }),
    ),
  );
  assert.deepEqual(attempts.map((r) => r.status()).sort(), [303, 400]);
  assert.equal(
    attempts.find((r) => r.status() === 303).headers().location,
    "/triage",
  );
  assert.equal(
    (
      await customer.ctx.request.post(url + "/auth/verify", {
        form: { token: racing.token },
        maxRedirects: 0,
      })
    ).status(),
    400,
  );
  const old = await issueCode("old@example.invalid");
  db.prepare("UPDATE login_codes SET expires_at=1 WHERE challenge_hash=?").run(
    createHash("sha256").update(old.challenge).digest("hex"),
  );
  assert.equal(
    (
      await customer.ctx.request.post(url + "/auth/code", {
        form: { challenge: old.challenge, code: old.code },
      })
    ).status(),
    400,
  );
  pass(
    "Codes expire, lock after five failures, share single-use redemption with links, and preserve return paths under concurrent submission",
  );

  db.prepare("INSERT INTO rate_limits VALUES (?,?,?,?)").run(
    "expired-test",
    0,
    1,
    1,
  );
  assert.equal(
    (await fetch(url + "/cdn-cgi/local/scheduled?cron=23+*+*+*+*")).status,
    200,
  );
  assert.equal(
    db
      .prepare("SELECT count(*) AS count FROM login_links WHERE expires_at=1")
      .get().count,
    0,
  );
  assert.equal(
    db
      .prepare(
        "SELECT count(*) AS count FROM rate_limits WHERE key='expired-test'",
      )
      .get().count,
    0,
  );
  pass(
    "Expiry, rate limits, logout, repeat sign-in and scheduled credential cleanup work",
  );
  assert.deepEqual(errors, [], "No browser JavaScript exceptions");
  assert.ok(!output.includes("Tasktrack request failed:"), output);
  await writeFile(
    path.join(root, "docs/hosted-verification.json"),
    JSON.stringify(
      {
        checked_at: new Date().toISOString(),
        runtime: "Cloudflare workerd / D1 / Durable Object SQLite / R2",
        checks,
        browser_errors: errors,
        email_delivery: "Local outbox; no external email sent",
      },
      null,
      2,
    ) + "\n",
  );
  console.log(`Passed ${checks.length} hosted behavior groups.`);
} catch (error) {
  await delay(200);
  console.error(output.slice(-12000));
  throw error;
} finally {
  if (browser) await browser.close();
  if (db) db.close();
  if (server?.pid && server.exitCode === null) {
    process.kill(-server.pid, "SIGTERM");
    await Promise.race([
      new Promise((resolve) => server.once("exit", resolve)),
      delay(5000),
    ]);
  }
  await rm(temp, { recursive: true, force: true });
}
