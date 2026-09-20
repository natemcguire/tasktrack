// Runnable internal dashboard: all Tasktrack credentials remain on this server.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { timingSafeEqual, createHash } from "node:crypto";
import { TasktrackClient } from "../client.js";
const password = process.env.DASHBOARD_PASSWORD;
const project = Number(process.env.TT_PROJECT_ID);
if (
  !password ||
  password.length < 16 ||
  !Number.isSafeInteger(project) ||
  project < 1
)
  throw new Error(
    "Set DASHBOARD_PASSWORD (16+ chars), TT_PROJECT_ID, TT_CLIENT_ID and TT_CLIENT_SECRET.",
  );
const expected = createHash("sha256")
  .update("Basic " + Buffer.from("internal:" + password).toString("base64"))
  .digest();
const client = new TasktrackClient({
  origin: process.env.TT_URL,
  clientId: process.env.TT_CLIENT_ID,
  clientSecret: process.env.TT_CLIENT_SECRET,
});
const html =
  '<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>Internal project dashboard</title></head><body><h1>Project work</h1><tasktrack-tasks src="/tasktrack/board" view="board"></tasktrack-tasks><script type="module" src="/components.js"></script></body></html>';
const server = createServer(async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader(
    "Content-Security-Policy",
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
  );
  if (
    !timingSafeEqual(
      expected,
      createHash("sha256")
        .update(req.headers.authorization || "")
        .digest(),
    )
  ) {
    res.writeHead(401, {
      "WWW-Authenticate": 'Basic realm="Internal dashboard", charset="UTF-8"',
    });
    return res.end(
      "Sign in with username internal and your dashboard password.",
    );
  }
  if (req.method !== "GET") {
    res.writeHead(405);
    return res.end();
  }
  const url = new URL(req.url, "http://localhost");
  try {
    if (url.pathname === "/") {
      res.setHeader("Content-Type", "text/html; charset=utf-8");
      return res.end(html);
    }
    if (url.pathname === "/components.js") {
      res.setHeader("Content-Type", "application/javascript");
      return res.end(
        await readFile(new URL("../components.js", import.meta.url)),
      );
    }
    let data;
    if (url.pathname === "/tasktrack/board") data = await client.board(project);
    else if (url.pathname === "/tasktrack/tasks")
      data = await client.tasks({
        project_id: project,
        limit: 50,
        cursor: url.searchParams.get("cursor") || undefined,
      });
    else {
      res.writeHead(404);
      return res.end();
    }
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(data));
  } catch (e) {
    res.writeHead(e.status === 403 ? 403 : 502, {
      "Content-Type": "application/json",
    });
    res.end(
      JSON.stringify({
        error: {
          message:
            "Project data unavailable. Check the server integration configuration.",
        },
      }),
    );
  }
});
server.listen(Number(process.env.PORT || 7790), "127.0.0.1", () =>
  console.log(
    "Internal dashboard on http://127.0.0.1:" + server.address().port,
  ),
);
