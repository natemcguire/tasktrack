// Actual Chromium interactions against a disposable HTTP process and SQLite DB.
import { chromium, expect } from "@playwright/test";
import { spawn, execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temp = await mkdtemp(path.join(tmpdir(), "tasktrack-browser-"));
const env = {
  ...process.env,
  TT_DATA_DIR: path.join(temp, "data"),
  PYTHONPATH: root,
};
delete env.TT_ACTOR;
delete env.TT_SESSION;
const artifacts = path.join(root, "docs/screenshots");
await mkdir(artifacts, { recursive: true });
let server, url, browser, page;
const errors = [];
async function start() {
  server = spawn("python3", ["-m", "tasktrack", "serve", "--port", "0"], {
    cwd: root,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  server.stderr.on("data", (data) => errors.push(data.toString()));
  url = await new Promise((resolve, reject) => {
    let data = "";
    const timeout = setTimeout(
      () => reject(Error("Server startup timed out")),
      10000,
    );
    server.stdout.on("data", (chunk) => {
      data += chunk;
      const match = data.match(/listening on (http:\/\/\S+)/);
      if (match) {
        clearTimeout(timeout);
        resolve(match[1]);
      }
    });
    server.on("exit", (code) => {
      clearTimeout(timeout);
      reject(Error(`Server exited ${code}: ${errors.join("")}`));
    });
  });
}
async function stop() {
  if (server && server.exitCode === null) {
    const done = new Promise((resolve) => server.once("exit", resolve));
    server.kill();
    await done;
  }
}
function cli(...args) {
  return JSON.parse(
    execFileSync("python3", ["-m", "tasktrack", ...args, "--json"], {
      cwd: root,
      env,
      encoding: "utf8",
    }),
  );
}
async function api(route, method = "GET", body, actor = "nate", session) {
  const response = await fetch(url + "/api/v1" + route, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Actor": actor,
      "Idempotency-Key": crypto.randomUUID(),
      ...(session ? { "X-Session": session } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw Error(JSON.stringify(value));
  return value;
}
const dialog = () => page.getByRole("dialog");
async function save(name) {
  await dialog().getByRole("button", { name, exact: true }).click();
  await expect(dialog()).not.toBeVisible();
}
async function openTask(id) {
  await page.goto(`${url}/tasks/${id}`);
  await expect(
    page.getByRole("button", { name: "Edit task", exact: true }),
  ).toBeVisible();
}
async function moveInBrowser(id, to) {
  await page.locator(`[data-move="${id}"]`).focus();
  await page.keyboard.press("Enter");
  await dialog().getByLabel("Column", { exact: true }).selectOption(to);
}
async function action(id, name, body = {}, actor = "nate", session) {
  const task = await api(`/tasks/${id}`);
  return api(
    `/tasks/${id}/${name}`,
    "POST",
    { expected_version: task.version, ...body },
    actor,
    session,
  );
}
async function create(title, extra = {}) {
  return api("/tasks", "POST", {
    project_id: project.id,
    title,
    description_markdown:
      "Give a disconnected client the same order and receipt.",
    acceptance_criteria: ["A retry returns the original receipt."],
    assignee: "nate",
    ...extra,
  });
}
let project, epic, task, second;
try {
  await start();
  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({
    viewport: { width: 1512, height: 1100 },
    deviceScaleFactor: 1,
  });
  page.on("pageerror", (error) => errors.push(error.stack));
  await page.goto(url);
  await expect(
    page.getByRole("heading", { name: "Good work starts with context." }),
  ).toBeVisible();
  await page.getByLabel("Acting as").fill("nate");
  await page.getByLabel("Acting as").blur();
  await page.getByRole("button", { name: "Create your first project" }).click();
  await dialog().getByLabel("Project key").fill("hbr");
  await dialog().getByLabel("Project name").fill("Harbor checkout");
  await dialog()
    .getByLabel("Project brief / PRD")
    .fill(
      "# A calmer checkout\nMake every purchase dependable, even when the connection is not.\n\n## Success looks like\n- One order per checkout.\n- A clear, recoverable next step.",
    );
  await save("Create project");
  await expect(
    page.getByRole("heading", { name: "Harbor checkout", exact: true }),
  ).toBeVisible();
  project = cli("project", "get", "HBR");
  expect(project.brief_markdown).toContain("One order per checkout");
  await page.getByRole("button", { name: "New epic", exact: true }).click();
  await dialog()
    .getByLabel("Title", { exact: true })
    .fill("A dependable checkout");
  await dialog()
    .getByLabel("Epic PRD / description")
    .fill(
      "## Checkout reliability\nRecover an order without duplicate charges.",
    );
  await dialog()
    .getByLabel("Acceptance criteria")
    .fill("Every checkout regression passes.");
  await dialog().getByLabel("Assignee", { exact: true }).fill("nate");
  await save("Create");
  await expect(
    page.getByRole("link", { name: "A dependable checkout", exact: true }),
  ).toBeVisible();
  epic = (await api(`/tasks?project_id=${project.id}&kind=epic`)).items[0];
  await page.getByRole("button", { name: "New task", exact: false }).click();
  await dialog()
    .getByLabel("Title", { exact: true })
    .fill("Make checkout retries safe");
  await dialog()
    .getByLabel("Description", { exact: true })
    .fill("Keep one order after a disconnect.");
  await dialog()
    .getByLabel("Acceptance criteria")
    .fill(
      "A retry returns the original receipt.\nA disconnect creates no second order.",
    );
  await dialog().getByLabel("Assignee", { exact: true }).fill("nate");
  await dialog().getByLabel("Priority", { exact: true }).selectOption("high");
  await dialog().getByLabel("Parent epic").selectOption(String(epic.id));
  await save("Create");
  await expect(
    page.getByRole("link", { name: "Make checkout retries safe", exact: true }),
  ).toBeVisible();
  task = (await api(`/tasks?project_id=${project.id}&q=Make+checkout`))
    .items[0];
  expect(cli("task", "get", String(task.id)).parent_id).toBe(epic.id);
  console.log(
    "PASS: browser-created project, PRD, epic and task are visible through CLI/API",
  );

  await openTask(task.id);
  await page.getByRole("button", { name: "Edit task", exact: true }).click();
  await dialog()
    .getByLabel("Title", { exact: true })
    .fill("Make disconnected checkout retries safe");
  cli(
    "task",
    "update",
    String(task.id),
    "--version",
    "1",
    "--title",
    "Concurrent CLI revision",
    "--as",
    "codex",
  );
  await dialog()
    .getByRole("button", { name: "Save task", exact: true })
    .click();
  await expect(
    dialog().getByText("Your unsaved input is still here.", { exact: false }),
  ).toBeVisible();
  await expect(dialog().getByLabel("Title", { exact: true })).toHaveValue(
    "Make disconnected checkout retries safe",
  );
  expect((await api(`/tasks/${task.id}`)).title).toBe(
    "Concurrent CLI revision",
  );
  await dialog().getByRole("button", { name: "Review latest version" }).click();
  await expect(
    dialog().getByText("Latest saved version: 2.", { exact: false }),
  ).toBeVisible();
  await save("Save task");
  await expect(
    page.getByRole("heading", {
      name: "Make disconnected checkout retries safe",
    }),
  ).toBeVisible();
  expect((await api(`/tasks/${task.id}`)).version).toBe(3);
  console.log(
    "PASS: stale browser edit preserves input and requires review of the CLI revision",
  );

  await page.goto(`${url}/projects/HBR`);
  await expect(
    page.getByRole("heading", { name: "Harbor checkout", exact: true }),
  ).toBeVisible();
  second = await create("Write the checkout recovery guide", {
    priority: "normal",
  });
  await page.reload();
  await expect(page.locator(`[data-move="${second.id}"]`)).toBeVisible();
  await moveInBrowser(second.id, "backlog");
  await dialog()
    .getByLabel("Position", { exact: true })
    .selectOption(String(task.id));
  await save("Move task");
  expect(
    (await api(`/tasks?project_id=${project.id}&status=backlog`)).items
      .map((x) => x.id)
      .indexOf(second.id),
  ).toBeLessThan(
    (await api(`/tasks?project_id=${project.id}&status=backlog`)).items
      .map((x) => x.id)
      .indexOf(task.id),
  );
  await page
    .getByLabel("Search tasks", { exact: true })
    .fill("Make disconnected");
  await expect(page.locator(".card")).toHaveCount(1);
  await expect(page.locator("#board-counts")).toContainText(
    "1 matching · 3 total",
  );
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await expect(page.locator(".card")).toHaveCount(3);
  await page.getByLabel("Filter by priority").selectOption("high");
  await expect(page.locator(".card")).toHaveCount(1);
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await page.getByLabel("Filter by epic").selectOption(String(epic.id));
  await expect(page.locator(".card")).toHaveCount(1);
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await page.getByLabel("Filter by assignee").fill("nate");
  await expect(page.locator(".card")).toHaveCount(3);
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await action(second.id, "block", { reason: "Waiting for product review" });
  await page.reload();
  await page.getByLabel("Filter by blocked state").selectOption("true");
  await expect(page.locator(".card")).toHaveCount(1);
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await moveInBrowser(second.id, "in_progress");
  await dialog()
    .getByRole("button", { name: "Move task", exact: true })
    .click();
  await expect(
    dialog().getByText("Resolve blockers before starting", { exact: false }),
  ).toBeVisible();
  await dialog().getByRole("button", { name: "Cancel", exact: true }).click();
  await action(second.id, "unblock", { reason: "Product review is complete" });
  await page.reload();
  // Real drag/drop also uses the same reviewable transition dialog.
  await page
    .locator(`.card[data-id="${second.id}"]`)
    .dragTo(page.locator('.column[data-status="in_progress"]'));
  await expect(dialog().getByLabel("Column", { exact: true })).toHaveValue(
    "in_progress",
  );
  await save("Move task");
  expect((await api(`/tasks/${second.id}`)).status).toBe("in_progress");
  console.log(
    "PASS: filters, keyboard reorder, blocker validation and real drag/drop persist correctly",
  );

  await openTask(task.id);
  await page.getByLabel("Session", { exact: true }).fill("checkout-browser-1");
  await page.getByLabel("Session", { exact: true }).blur();
  await page.getByRole("button", { name: "Claim", exact: true }).click();
  await save("Claim task");
  expect((await api(`/tasks/${task.id}`)).execution.session).toBe(
    "checkout-browser-1",
  );
  await page.getByRole("button", { name: "Checkpoint", exact: true }).click();
  await dialog()
    .getByLabel("Progress summary")
    .fill("The retry key now survives a disconnected request.");
  await dialog()
    .getByLabel("Next action")
    .fill("Run the disconnect regression, then review the receipt.");
  await dialog().getByLabel("Workspace", { exact: true }).fill("/work/harbor");
  await dialog()
    .getByLabel("Branch", { exact: true })
    .fill("fix/checkout-retries");
  await dialog()
    .getByLabel("Acceptance criteria still remaining")
    .fill("The disconnect regression passes.");
  await dialog()
    .locator('[data-links="checkpoint_evidence"]')
    .getByLabel("Label", { exact: true })
    .fill("Current regression run");
  await dialog()
    .locator('[data-links="checkpoint_evidence"]')
    .getByLabel("URL", { exact: true })
    .fill("https://example.org/harbor/runs/42");
  await save("Save checkpoint");
  await expect(
    page
      .locator(".callout")
      .filter({
        hasText: "Run the disconnect regression, then review the receipt.",
      }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Add comment", exact: true }).click();
  await dialog()
    .getByLabel("Comment", { exact: true })
    .fill(
      "Decision: preserve the first receipt. See the linked regression evidence.",
    );
  await save("Add comment");
  await expect(page.locator(".comment .markdown")).toContainText(
    "Decision: preserve the first receipt.",
  );
  await api(`/tasks/${second.id}/comments`, "POST", {
    body: "<script>window.tasktrackXSS=true</script>\n[unsafe](javascript:alert)",
  });
  await openTask(second.id);
  await expect(page.locator(".comment .markdown")).toContainText("<script>");
  expect(await page.evaluate(() => window.tasktrackXSS)).toBeUndefined();
  expect(await page.locator(".comment a").getAttribute("href")).toBe("#");
  await openTask(task.id);
  const upload = path.join(temp, "review-evidence.txt");
  const bytes = Buffer.from("Review evidence\nExact bytes: \u0000\u00ff\n");
  await writeFile(upload, bytes);
  await page.getByRole("button", { name: "Attach file", exact: true }).click();
  await dialog().getByLabel("File", { exact: true }).setInputFiles(upload);
  await save("Upload file");
  await expect(
    page.getByRole("link", { name: "review-evidence.txt", exact: true }),
  ).toBeVisible();
  const attachments = await api(`/tasks/${task.id}/attachments`);
  const download = await fetch(url + attachments.items[0].download_url);
  expect(Buffer.from(await download.arrayBuffer())).toEqual(bytes);
  await page
    .getByRole("button", { name: "Submit for review", exact: true })
    .click();
  await dialog()
    .getByLabel("Result summary")
    .fill("A disconnected retry returns the original order and receipt.");
  await dialog()
    .getByRole("button", { name: "Submit for review", exact: true })
    .click();
  await expect(
    dialog().getByText("evidence must contain", { exact: false }),
  ).toBeVisible();
  await dialog()
    .locator('[data-links="result_evidence"]')
    .getByLabel("Label", { exact: true })
    .fill("Disconnect regression — passing");
  await dialog()
    .locator('[data-links="result_evidence"]')
    .getByLabel("URL", { exact: true })
    .fill("https://example.org/harbor/runs/43");
  await save("Submit for review");
  expect((await api(`/tasks/${task.id}`)).execution).toBeNull();
  await page.getByRole("button", { name: "Complete", exact: true }).click();
  await dialog()
    .getByLabel("Acceptance note")
    .fill(
      "Both retry cases pass. The response preserves the original receipt.",
    );
  await save("Complete task");
  expect((await api(`/tasks/${task.id}`)).status).toBe("done");
  await page.getByRole("button", { name: "Archive", exact: true }).click();
  await dialog()
    .getByLabel("Reason", { exact: true })
    .fill("Accepted and recorded.");
  await save("Archive task");
  expect((await api(`/tasks/${task.id}`)).archived_at).not.toBeNull();
  await page.goto(`${url}/projects/HBR`);
  await page.getByRole("tab", { name: "Archived", exact: true }).click();
  await expect(
    page.getByRole("link", {
      name: "Make disconnected checkout retries safe",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("link", {
      name: "Make disconnected checkout retries safe",
      exact: true,
    })
    .click();
  await page.getByRole("button", { name: "Restore", exact: true }).click();
  await dialog()
    .getByLabel("Reason", { exact: true })
    .fill("Keep accepted work on the board.");
  await save("Restore task");
  console.log(
    "PASS: browser claim, checkpoint, safe Markdown, attachment bytes, evidence gate, completion and archive/restore",
  );

  await page.goto(`${url}/projects/HBR`);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await dialog().getByLabel("Project key").fill("HARBOR");
  await dialog().getByLabel("Project name").fill("Harbor");
  await save("Save settings");
  await expect(page).toHaveURL(`${url}/projects/HARBOR`);
  expect(cli("project", "get", "HBR").key).toBe("HARBOR");
  await page.goto(`${url}/tasks/HBR-${task.id}`);
  await expect(
    page.getByRole("heading", {
      name: "Make disconnected checkout retries safe",
    }),
  ).toBeVisible();
  expect((await api(`/tasks/${task.id}/attachments`)).items[0].sha256).toBe(
    attachments.items[0].sha256,
  );
  const persistentId = (await api("/health")).instance_id;
  await stop();
  await start();
  await page.goto(`${url}/projects/HBR`);
  await expect(page).toHaveURL(`${url}/projects/HARBOR`);
  expect((await api("/health")).instance_id).toBe(persistentId);
  expect((await api(`/tasks/${task.id}`)).status).toBe("done");
  console.log(
    "PASS: browser project rename, old references and full service restart preserve relationships and content",
  );

  // More synthetic work produces a useful, honestly captured product screenshot.
  await create("Map the interrupted checkout journey", {
    assignee: "nate",
    priority: "normal",
    parent_id: epic.id,
  });
  await create("Add a receipt lookup endpoint", {
    assignee: "codex@harbor",
    priority: "high",
    parent_id: epic.id,
  });
  const blocked = await create("Confirm the payment provider retry policy", {
    assignee: "nate",
    priority: "normal",
  });
  await action(blocked.id, "block", {
    reason: "Waiting for provider response",
  });
  const active = await create("Preserve context between agent sessions", {
    assignee: "codex@harbor",
    priority: "high",
  });
  await action(active.id, "claim", {}, "codex@harbor", "recovery-2");
  const review = await create("Explain what happens after a disconnect", {
    assignee: "nate",
    parent_id: epic.id,
  });
  await action(review.id, "move", { status: "in_progress" });
  await action(review.id, "submit", {
    result: {
      summary: "The recovery states are documented.",
      evidence: [
        { label: "Copy review", uri: "https://example.org/reviews/17" },
      ],
    },
  });
  const done = await create("Write the first checkout acceptance criteria", {
    priority: "low",
  });
  await action(done.id, "move", { status: "in_progress" });
  await action(done.id, "submit", {
    result: {
      summary: "Acceptance criteria are ready.",
      evidence: [
        { label: "Planning notes", uri: "https://example.org/notes/1" },
      ],
    },
  });
  await action(done.id, "complete", {
    acceptance_note: "Requirements cover retry and recovery.",
  });
  await page.goto(`${url}/projects/HARBOR`);
  await expect(page.locator(".card")).toHaveCount(9);
  await page.screenshot({
    path: path.join(artifacts, "board.png"),
    fullPage: true,
  });
  await openTask(task.id);
  await page.screenshot({
    path: path.join(artifacts, "task-detail.png"),
    fullPage: true,
  });
  await page.locator("#history-section").scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(artifacts, "history.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${url}/projects/HARBOR`);
  await expect(page.getByLabel("Board column")).toBeVisible();
  await page.getByLabel("Board column").selectOption("done");
  await expect(page.locator(".column.mobile-active .card")).toHaveCount(2);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: path.join(artifacts, "mobile-board.png"),
    fullPage: true,
  });
  await page
    .getByRole("link", {
      name: "Make disconnected checkout retries safe",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Make disconnected checkout retries safe",
    }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Edit task", exact: true }).click();
  await expect(dialog().getByLabel("Title", { exact: true })).toHaveValue(
    "Make disconnected checkout retries safe",
  );
  expect(
    await dialog().evaluate((el) => el.scrollWidth <= el.clientWidth),
  ).toBe(true);
  await dialog().getByRole("button", { name: "Cancel", exact: true }).click();
  await page.screenshot({
    path: path.join(artifacts, "mobile-detail.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
  console.log(
    "PASS: desktop/mobile screenshots, accessible column switcher, unclipped detail/forms, and no browser/server errors",
  );
  const report = {
    checked_at: new Date().toISOString(),
    browser: await browser.version(),
    viewport: { desktop: [1512, 1100], mobile: [390, 844] },
    screenshots: [
      "board.png",
      "task-detail.png",
      "history.png",
      "mobile-board.png",
      "mobile-detail.png",
    ],
    scenarios: 7,
    result: "passed",
  };
  await writeFile(
    path.join(root, "docs/browser-verification.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  await browser.close();
  browser = null;
  await stop();
  await rm(temp, { recursive: true, force: true });
} catch (error) {
  if (page)
    await page
      .screenshot({
        path: path.join(root, ".git/browser-failure.png"),
        fullPage: true,
      })
      .catch(() => {});
  console.error(error);
  console.error(
    `Disposable failure data: ${temp}\nServer log: ${errors.join("\n")}`,
  );
  if (browser) await browser.close();
  await stop();
  process.exitCode = 1;
}
