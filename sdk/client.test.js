import { test } from "node:test";
import assert from "node:assert/strict";
import { TasktrackClient } from "./client.js";
test("one concurrent credential exchange, pagination and server-only secrets", async () => {
  let exchanges = 0;
  const calls = [];
  const client = new TasktrackClient({
    origin: "http://localhost:8787",
    clientId: "app",
    clientSecret: "secret",
    fetch: async (url, opts) => {
      calls.push([String(url), opts]);
      if (String(url).endsWith("/oauth/token")) {
        exchanges++;
        return Response.json({ access_token: "short", expires_in: 900 });
      }
      assert.equal(opts.headers.Authorization, "Bearer short");
      return Response.json(
        String(url).includes("cursor=next")
          ? { items: [{ id: 2 }], has_more: false }
          : { items: [{ id: 1 }], has_more: true, next_cursor: "next" },
      );
    },
  });
  await Promise.all([client.projects(), client.tasks({ project_id: 1 })]);
  assert.equal(exchanges, 1);
  const tasks = [];
  for await (const t of client.allTasks({ project_id: 1 })) tasks.push(t.id);
  assert.deepEqual(tasks, [1, 2]);
  assert.ok(calls.every(([url]) => !url.includes("secret")));
  assert.ok(!JSON.stringify(client).includes("secret"));
});
test("revoked app fails closed and does not loop", async () => {
  let count = 0;
  const client = new TasktrackClient({
    clientId: "app",
    clientSecret: "secret",
    fetch: async (url) => {
      count++;
      return Response.json({ error: "invalid_client" }, { status: 400 });
    },
  });
  await assert.rejects(
    client.tasks({ project_id: 1 }),
    /authentication failed/,
  );
  assert.equal(count, 1);
});
test("reject insecure or credential-bearing origins", () => {
  for (const origin of [
    "http://example.com",
    "https://user:secret@example.com",
    "https://example.com/path",
  ])
    assert.throws(
      () => new TasktrackClient({ origin, clientId: "a", clientSecret: "s" }),
    );
});
