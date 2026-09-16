"use strict";
const magic = document.querySelector("#magic-form");
if (magic && document.visibilityState === "visible") magic.requestSubmit();
const form = document.querySelector("#code-form");
if (form) {
  const code = form.elements.code,
    button = form.querySelector("button"),
    error = document.querySelector("#code-error");
  let submitting = false;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting || !form.reportValidity()) return;
    submitting = true;
    button.disabled = true;
    error.textContent = "Signing in…";
    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: { Accept: "application/json" },
        body: new URLSearchParams(new FormData(form)),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error.message);
      location.replace(data.next);
    } catch (e) {
      error.textContent = e.message;
      code.select();
    } finally {
      submitting = false;
      button.disabled = false;
    }
  });
  code.addEventListener("input", () => {
    if (/^[0-9]{6}$/.test(code.value)) form.requestSubmit();
  });
  code.addEventListener("change", () => {
    if (/^[0-9]{6}$/.test(code.value)) form.requestSubmit();
  });
}
