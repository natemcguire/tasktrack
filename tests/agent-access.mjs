// Run against npm run dev:cloudflare. Only localhost with the local test outbox.
import { chromium, expect } from "@playwright/test";
import assert from "node:assert/strict";
import { readdir } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import path from "node:path";
const origin = process.env.TT_TEST_URL || "http://localhost:8787";
assert.equal(new URL(origin).hostname, "localhost");
const folder = ".wrangler/state/v3/d1/miniflare-D1DatabaseObject";
let db;
for (const name of await readdir(folder)) {
  if (!name.endsWith(".sqlite")) continue;
  const candidate = new DatabaseSync(path.join(folder, name));
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
assert.ok(db);
const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2",
      transport: "internal",
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  });
  const email = "agent-test-" + Date.now() + "@example.test";
  await page.goto(origin + "/login");
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Email me a code" }).click();
  await expect(page.getByLabel("Sign-in code")).toBeVisible();
  const mail = db
    .prepare(
      "SELECT body FROM local_mail WHERE recipient=? ORDER BY id DESC LIMIT 1",
    )
    .get(email);
  await page.waitForLoadState("networkidle");
  await page
    .getByLabel("Sign-in code")
    .fill(mail.body.match(/code: (\d{6})/)[1]);
  await expect(
    page.getByRole("link", { name: "Account", exact: true }),
  ).toBeVisible();
  await page.goto(origin + "/account");
  await page.getByRole("button", { name: "Add passkey", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Remove passkey" }),
  ).toBeVisible();
  let csrf = await page.locator('meta[name="tt-csrf"]').getAttribute("content");
  async function api(route, body, token) {
    const r = await ctx.request.fetch(origin + route, {
      method: body === undefined ? "GET" : "POST",
      headers: token
        ? {
            Authorization: "Bearer " + token,
            "Idempotency-Key": crypto.randomUUID(),
          }
        : { "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
      ...(body === undefined ? {} : { data: body }),
    });
    const data = await r.json();
    return { status: r.status(), data };
  }
  const me = (await api("/api/v1/me")).data;
  const project = (
    await api("/api/v1/projects", { key: "AUTH", name: "Agent tests" })
  ).data;
  assert.ok(project.id);
  const task = (
    await api("/api/v1/tasks", {
      project_id: project.id,
      title: "Archive after human approval",
    })
  ).data;
  const enrollment = await api("/oauth/device_authorization", {
    workspace_id: me.workspace_id,
    name: "Test agent",
    project_ids: [project.id],
    capabilities: [
      "project.read",
      "work.read",
      "work.edit",
      "work.accept",
      "notifications.deliver",
    ],
  });
  assert.equal(enrollment.status, 200);
  await page.goto(origin + "/agents");
  await page
    .getByLabel("Code shown by your agent")
    .fill(enrollment.data.user_code);
  await page
    .getByRole("button", { name: "Review request", exact: true })
    .click();
  await expect(page.locator("#agent-review")).toBeVisible();
  await page
    .getByRole("button", { name: "Approve with passkey", exact: true })
    .click();
  await expect(page.locator("#agent-message")).toContainText("Approved.");
  const grant = await api("/oauth/token", {
    grant_type: "urn:ietf:params:oauth:grant-type:device_code",
    device_code: enrollment.data.device_code,
  });
  assert.equal(grant.status, 200, JSON.stringify(grant.data));
  const token = grant.data.access_token;
  await page.getByLabel("Phone number", { exact: true }).fill("+14155550123");
  await page
    .getByRole("button", { name: "Send verification code", exact: true })
    .click();
  await expect(page.locator("#channel-verify")).toBeVisible();
  const delivery = (await api("/api/v1/agent-deliveries/claim", {}, token)).data
    .delivery;
  assert.ok(delivery);
  assert.equal(
    (await api("/api/v1/agent-deliveries/claim", {}, token)).data.delivery,
    null,
  );
  await api(
    "/api/v1/agent-deliveries/ack",
    { id: delivery.id, lease_token: delivery.lease_token, status: "sent" },
    token,
  );
  await page
    .getByLabel("Phone verification code")
    .fill(delivery.body.match(/code is (\d{6})/)[1]);
  await page.getByRole("button", { name: "Verify phone with passkey" }).click();
  await expect(page.locator("#agent-message")).toContainText("Phone verified");
  assert.equal(
    (
      await api(
        "/api/v1/tasks/" + task.id + "/archive",
        { expected_version: 1, reason: "bypass" },
        token,
      )
    ).status,
    403,
  );
  const request = await api(
    "/api/v1/approval-requests",
    {
      operation: "task.archive",
      task_id: task.id,
      expected_version: 1,
      reason: "Reviewed cleanup",
    },
    token,
  );
  assert.equal(request.status, 201, JSON.stringify(request.data));
  const notice = (await api("/api/v1/agent-deliveries/claim", {}, token)).data
    .delivery;
  assert.ok(notice.body.includes("/agents"));
  assert.ok(!notice.body.includes(token));
  await api(
    "/api/v1/agent-deliveries/ack",
    { id: notice.id, lease_token: notice.lease_token, status: "sent" },
    token,
  );
  assert.equal(
    (
      await api(
        "/api/v1/approval-requests/" + request.data.id + "/decision",
        { decision: "approve" },
        token,
      )
    ).status,
    403,
  );
  await page.reload();
  await expect(page.locator("#approval-list")).toContainText(
    "Archive after human approval",
  );
  await page
    .locator("#approval-list")
    .getByRole("button", { name: "Approve with passkey" })
    .click();
  await expect(page.locator("#approval-list")).toContainText("No pending");
  const executed = await api(
    "/api/v1/approval-requests/" + request.data.id + "/execute",
    {},
    token,
  );
  assert.equal(executed.status, 200, JSON.stringify(executed.data));
  assert.ok(executed.data.archived_at);
  await page.goto(origin + "/imports");
  await expect(
    page.getByRole("heading", { name: "Import work", exact: true }),
  ).toBeVisible();
  await page.selectOption("#import-provider", "kanban");
  await page.locator("#import-file").setInputFiles({
    name: "work.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "id,title,status,phase\n1,Imported from Kanban,Backlog,backlog\n2,Imported Shipping,Shipping,done\n",
    ),
  });
  await page.getByRole("button", { name: "Review file", exact: true }).click();
  await expect(page.locator("#import-message")).toContainText(
    "Found 2 records",
  );
  await page.locator("#import-visibility").check();
  await page
    .getByRole("button", { name: "Prepare preview", exact: true })
    .click();
  await expect(page.locator("#import-review")).toBeVisible();
  await page
    .getByRole("button", { name: "Import reviewed work", exact: true })
    .click();
  await expect(page.locator("#import-summary")).toContainText("Created 2");
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
    false,
    "Phone layout must not overflow",
  );
  await page.screenshot({
    path: "/tmp/tasktrack-import-phone.png",
    fullPage: true,
  });
  const sourceLink = await page
    .getByRole("link", { name: "Download original source export" })
    .getAttribute("href");
  const original = await ctx.request.get(origin + sourceLink);
  assert.equal(original.status(), 200);
  assert.ok((await original.json()).includes("Imported Shipping"));
  await page.goto(origin + "/projects/AUTH");
  await expect(page.locator('.column[aria-label="Shipping"]')).toContainText(
    "Imported Shipping",
  );
  await page.selectOption("#mobile-columns", { label: "Shipping" });
  await page.screenshot({
    path: "/tmp/tasktrack-custom-columns-phone.png",
    fullPage: true,
  });
  // Verify original attachment bytes through the real R2-backed import path.
  const bytes = Buffer.from("original imported attachment\n");
  const sha = Buffer.from(
    await crypto.subtle.digest("SHA-256", bytes),
  ).toString("hex");
  const fileJob = (
    await api("/api/v1/import-previews", {
      provider: "kanban",
      project_id: project.id,
      allow_visibility_change: true,
      source: {
        schema_version: 1,
        provider: "kanban",
        account_id: "test",
        source_project: "files",
        complete: true,
        records: [
          {
            id: "file-card",
            title: "Imported attachment",
            status: "Backlog",
            phase: "backlog",
            attachments: [
              {
                id: "original",
                filename: "original.txt",
                size: bytes.length,
                sha256: sha,
              },
            ],
          },
        ],
      },
    })
  ).data;
  assert.ok(fileJob.id, JSON.stringify(fileJob));
  const upload = await ctx.request.post(
    origin + "/api/v1/import-jobs/" + fileJob.id + "/files/" + sha,
    {
      headers: {
        "X-CSRF-Token": csrf,
        "Content-Type": "application/octet-stream",
        "Idempotency-Key": crypto.randomUUID(),
      },
      data: bytes,
    },
  );
  assert.equal(upload.status(), 200, await upload.text());
  const committed = await api("/api/v1/import-jobs/" + fileJob.id + "/commit", {
    digest: fileJob.digest,
  });
  assert.equal(committed.status, 200, JSON.stringify(committed.data));
  const imported = (
    await api("/api/v1/tasks?project_id=" + project.id)
  ).data.items.find((t) => t.title === "Imported attachment");
  const attachment = (
    await api("/api/v1/tasks/" + imported.id + "/attachments")
  ).data.items[0];
  const downloaded = await ctx.request.get(origin + attachment.download_url);
  assert.deepEqual(await downloaded.body(), bytes);
  await page.goto(origin + "/agents");
  await page.getByRole("button", { name: "Revoke access" }).click();
  await expect(page.locator("#agent-list")).toContainText("Revoked");
  assert.equal((await api("/api/v1/tasks", undefined, token)).status, 401);
  assert.deepEqual(errors, []);
  console.log(
    "PASS: phone passkey enrollment, token scope, approval enforcement, import preview/commit, and revocation in real Worker/D1/DO",
  );
} finally {
  db.close();
  await browser.close();
}
