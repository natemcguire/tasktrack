/** Framework-independent display widgets. Fetches only your same-origin app backend. */
class TasktrackTasks extends HTMLElement {
  connectedCallback() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.refresh();
  }
  disconnectedCallback() {
    this.abort?.abort();
  }
  static get observedAttributes() {
    return ["src", "view", "tasks-src"];
  }
  attributeChangedCallback() {
    if (this.isConnected) this.refresh();
  }
  async refresh() {
    this.abort?.abort();
    this.abort = new AbortController();
    const root = this.shadowRoot;
    root.replaceChildren();
    const style = document.createElement("style");
    style.textContent =
      ":host{display:block;font:14px system-ui;color:var(--tasktrack-text,#143b32)}.board{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(240px,1fr);gap:16px;overflow:auto}section{background:var(--tasktrack-column,#f3f5f3);border-radius:12px;padding:12px}article{background:var(--tasktrack-card,white);border:1px solid #d9e1dd;border-radius:8px;padding:12px;margin:8px 0}h2{font-size:15px}h3{font-size:14px;margin:0 0 8px}p{margin:4px 0;color:#52655f}button{font:inherit;padding:8px 12px}";
    root.append(style);
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    status.textContent = "Loading tasks…";
    root.append(status);
    try {
      const url = new URL(
        this.getAttribute("src") || "/tasktrack/tasks",
        location.href,
      );
      if (url.origin !== location.origin)
        throw new Error("Use a same-origin authenticated app endpoint.");
      const response = await fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        signal: this.abort.signal,
      });
      if (!response.ok)
        throw new Error(
          response.status === 401
            ? "Sign in to this app to see tasks."
            : "Tasks are currently unavailable.",
        );
      const data = await response.json();
      status.remove();
      const card = (task, parent) => {
        const article = document.createElement("article");
        const title = document.createElement("h3");
        title.textContent = task.title;
        const detail = document.createElement("p");
        detail.textContent = [
          task.reference || task.id,
          task.status,
          task.assignee || "Unassigned",
        ].join(" · ");
        article.append(title, detail);
        parent.append(article);
      };
      if (this.getAttribute("view") === "board") {
        const board = document.createElement("div");
        board.className = "board";
        root.append(board);
        for (const column of data.configuration.columns) {
          const section = document.createElement("section");
          const title = document.createElement("h2");
          title.textContent = column.name;
          section.append(title);
          board.append(section);
          const bucket =
            data.configured_columns?.[String(column.id)] ||
            data.columns[column.allowed_phases[0]];
          const cards = document.createElement("div");
          section.append(cards);
          for (const task of bucket?.items || []) card(task, cards);
          if (bucket?.has_more) {
            const endpoint = this.getAttribute("tasks-src");
            if (!endpoint) {
              const notice = document.createElement("p");
              notice.textContent = "More tasks available in this column.";
              section.append(notice);
              continue;
            }
            let cursor = bucket.next_cursor;
            const more = document.createElement("button");
            more.textContent = "Load more in " + column.name;
            const error = document.createElement("p");
            error.setAttribute("role", "status");
            section.append(more, error);
            const signal = this.abort.signal;
            more.onclick = async () => {
              more.disabled = true;
              error.textContent = "";
              try {
                const next = new URL(endpoint, location.href);
                if (next.origin !== location.origin)
                  throw new Error(
                    "Use a same-origin authenticated app endpoint.",
                  );
                next.searchParams.set("view", "summary");
                next.searchParams.set("cursor", cursor);
                if (data.configured_columns) {
                  next.searchParams.delete("status");
                  next.searchParams.set("column_id", column.id);
                } else {
                  next.searchParams.delete("column_id");
                  next.searchParams.set("status", column.allowed_phases[0]);
                }
                const response = await fetch(next, {
                  credentials: "same-origin",
                  cache: "no-store",
                  redirect: "error",
                  signal,
                });
                if (!response.ok)
                  throw new Error("Could not load this column. Try again.");
                const page = await response.json();
                if (
                  !Array.isArray(page.items) ||
                  (page.has_more &&
                    (!page.next_cursor || page.next_cursor === cursor))
                )
                  throw new Error("Invalid task page. Refresh the board.");
                for (const task of page.items) card(task, cards);
                cursor = page.next_cursor;
                if (!page.has_more) more.remove();
              } catch (e) {
                if (e.name !== "AbortError") error.textContent = e.message;
              } finally {
                more.disabled = false;
              }
            };
          }
        }
      } else {
        for (const task of data.items || []) card(task, root);
        if (!data.items?.length) {
          const empty = document.createElement("p");
          empty.textContent = "No tasks.";
          root.append(empty);
        }
        if (data.has_more) {
          const more = document.createElement("button");
          more.textContent = "Next page";
          more.onclick = () => {
            url.searchParams.set("cursor", data.next_cursor);
            this.setAttribute("src", url.pathname + url.search);
          };
          root.append(more);
        }
      }
      this.dispatchEvent(
        new CustomEvent("tasktrack-loaded", {
          detail: { total: data.total },
          bubbles: true,
        }),
      );
    } catch (e) {
      if (e.name === "AbortError") return;
      status.textContent = e.message;
      this.dispatchEvent(
        new CustomEvent("tasktrack-error", {
          detail: { message: e.message },
          bubbles: true,
        }),
      );
    }
  }
}
if (!customElements.get("tasktrack-tasks"))
  customElements.define("tasktrack-tasks", TasktrackTasks);
export { TasktrackTasks };
