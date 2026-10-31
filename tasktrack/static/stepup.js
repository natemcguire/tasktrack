"use strict";
// Step-up confirmation shared by the agents, internal apps and imports pages.
// A challenge from the server says which methods the account has: a passkey
// prompt when this browser may hold one, and the approval password typed into
// an inline form when it doesn't (or when the passkey prompt is cancelled).
// Returns the proof to send with the decision: {challenge_id, credential} or
// {challenge_id, password}. Nothing here approves anything by itself.
(() => {
  const decode = (s) =>
    Uint8Array.from(atob(s.replaceAll("-", "+").replaceAll("_", "/")), (c) =>
      c.charCodeAt(0),
    );
  const encode = (b) =>
    btoa(String.fromCharCode(...new Uint8Array(b)))
      .replaceAll("+", "-")
      .replaceAll("/", "_")
      .replaceAll("=", "");
  async function passkey(challenge) {
    const options = challenge.options;
    const key = await navigator.credentials.get({
      publicKey: {
        ...options,
        challenge: decode(options.challenge),
        allowCredentials: options.allowCredentials.map((c) => ({
          ...c,
          id: decode(c.id),
        })),
      },
    });
    return {
      challenge_id: challenge.challenge_id,
      credential: {
        id: key.id,
        rawId: encode(key.rawId),
        type: key.type,
        response: {
          authenticatorData: encode(key.response.authenticatorData),
          clientDataJSON: encode(key.response.clientDataJSON),
          signature: encode(key.response.signature),
          userHandle: key.response.userHandle
            ? encode(key.response.userHandle)
            : null,
        },
        clientExtensionResults: key.getClientExtensionResults(),
      },
    };
  }
  // One inline form at a time, placed right after the control that started the approval.
  function password(challenge, anchor, note) {
    document.querySelector(".stepup-password")?.remove();
    return new Promise((resolve, reject) => {
      const form = document.createElement("form");
      form.className = "stepup-password account-section";
      const label = document.createElement("label");
      label.htmlFor = "stepup-password-value";
      label.textContent = "Approval password";
      const hint = document.createElement("p");
      hint.className = "small muted";
      hint.textContent =
        note || "Confirm with the approval password from your Account settings.";
      const row = document.createElement("div");
      row.className = "account-row";
      const input = document.createElement("input");
      input.id = "stepup-password-value";
      input.type = "password";
      input.autocomplete = "current-password";
      input.required = true;
      input.maxLength = 200;
      const confirm = document.createElement("button");
      confirm.className = "button primary";
      confirm.textContent = "Confirm with password";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "button";
      cancel.textContent = "Cancel";
      row.append(input, confirm, cancel);
      form.append(label, hint, row);
      const finish = (fn) => {
        form.remove();
        fn();
      };
      form.onsubmit = (event) => {
        event.preventDefault();
        if (!input.value) return;
        finish(() =>
          resolve({ challenge_id: challenge.challenge_id, password: input.value }),
        );
      };
      cancel.onclick = () =>
        finish(() => {
          const error = new Error("Approval cancelled.");
          error.name = "AbortError";
          reject(error);
        });
      (anchor?.parentElement || document.body).insertBefore(
        form,
        anchor?.nextSibling || null,
      );
      form.scrollIntoView({ behavior: "smooth", block: "nearest" });
      input.focus();
    });
  }
  window.ttStepUp = async function (challenge, anchor) {
    const methods = challenge.methods || (challenge.options ? ["passkey"] : []);
    const canPassword = methods.includes("password");
    if (challenge.options && methods.includes("passkey")) {
      try {
        return await passkey(challenge);
      } catch (error) {
        if (!canPassword) throw error;
        return password(
          challenge,
          anchor,
          "No passkey answered on this device. Use your approval password instead.",
        );
      }
    }
    if (canPassword) return password(challenge, anchor);
    throw new Error(
      "Add a passkey or an approval password in Account settings before approving.",
    );
  };
})();
