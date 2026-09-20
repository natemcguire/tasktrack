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
    return ["src", "view"];
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
          for (const task of bucket?.items || []) card(task, section);
          if (bucket?.has_more) {
            const more = document.createElement("p");
            more.textContent = "More tasks available in this column.";
            section.append(more);
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
