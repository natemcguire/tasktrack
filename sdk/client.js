/** Server-side Tasktrack client. Never bundle app credentials into browser code. */
export class TasktrackError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "TasktrackError";
    this.status = status;
    this.code = code;
  }
}
export class TasktrackClient {
  #secret;
  #token;
  #expires = 0;
  #pending;
  #fetch;
  #id;
  constructor({
    origin = "https://tasks.eastbayprojects.com",
    clientId,
    clientSecret,
    fetch: transport = globalThis.fetch,
  }) {
    if (typeof window !== "undefined")
      throw new Error(
        "TasktrackClient must run on your server; use the components with your own authenticated backend.",
      );
    const url = new URL(origin);
    if (
      (url.protocol !== "https:" &&
        !(
          url.protocol === "http:" &&
          ["localhost", "127.0.0.1"].includes(url.hostname)
        )) ||
      url.username ||
      url.password ||
      url.pathname !== "/" ||
      url.search ||
      url.hash
    )
      throw new Error("Use an HTTPS origin or loopback development server.");
    if (!clientId || !clientSecret)
      throw new Error("Service app credentials are required.");
    this.origin = url.origin;
    this.#id = clientId;
    this.#secret = clientSecret;
    this.#fetch = transport;
  }
  async #access() {
    if (this.#token && this.#expires > Date.now() + 60000) return this.#token;
    if (!this.#pending)
      this.#pending = (async () => {
        const response = await this.#fetch(this.origin + "/oauth/token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            grant_type: "client_credentials",
            client_id: this.#id,
            client_secret: this.#secret,
          }),
          redirect: "error",
          cache: "no-store",
          signal: AbortSignal.timeout(15000),
        });
        const result = await response.json();
        if (!response.ok || !result.access_token)
          throw new TasktrackError(
            response.status,
            result.error || "invalid_response",
            "Tasktrack app authentication failed. Check whether the app was revoked or its secret rotated.",
          );
        this.#token = result.access_token;
        this.#expires = Date.now() + result.expires_in * 1000;
        return this.#token;
      })().finally(() => {
        this.#pending = null;
      });
    return this.#pending;
  }
  async #get(path, query = {}) {
    const url = new URL("/api/v1" + path, this.origin);
    for (const [k, v] of Object.entries(query))
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    for (let attempt = 0; attempt < 2; attempt++) {
      const token = await this.#access();
      const response = await this.#fetch(url, {
        headers: {
          Authorization: "Bearer " + token,
          Accept: "application/json",
        },
        redirect: "error",
        cache: "no-store",
        signal: AbortSignal.timeout(15000),
      });
      if (response.status === 401 && !attempt) {
        this.#token = null;
        continue;
      }
      const data = await response.json();
      if (!response.ok)
        throw new TasktrackError(
          response.status,
          data.error?.code || "request_failed",
          data.error?.message || "Tasktrack request failed.",
        );
      return data;
    }
  }
  projects(query = {}) {
    return this.#get("/projects", query);
  }
  project(id) {
    return this.#get("/projects/" + encodeURIComponent(id));
  }
  tasks(query) {
    return this.#get("/tasks", query);
  }
  task(id) {
    return this.#get("/tasks/" + encodeURIComponent(id));
  }
  board(projectId, query = {}) {
    return this.#get("/board", { ...query, project_id: projectId });
  }
  comments(taskId, query = {}) {
    return this.#get(
      "/tasks/" + encodeURIComponent(taskId) + "/comments",
      query,
    );
  }
  attachments(taskId, query = {}) {
    return this.#get(
      "/tasks/" + encodeURIComponent(taskId) + "/attachments",
      query,
    );
  }
  events(query) {
    return this.#get("/events", query);
  }
  health() {
    return this.#get("/health");
  }
  async *allTasks(query) {
    let cursor;
    do {
      const page = await this.tasks({ ...query, cursor });
      for (const task of page.items) yield task;
      if (page.has_more && (!page.next_cursor || page.next_cursor === cursor))
        throw new Error("Invalid pagination cursor");
      cursor = page.has_more ? page.next_cursor : null;
    } while (cursor);
  }
  disconnect() {
    this.#token = null;
    this.#secret = null;
    this.#expires = 0;
  }
}
