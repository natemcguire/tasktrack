import {
  startRegistration,
  startAuthentication,
  browserSupportsWebAuthn,
} from "@simplewebauthn/browser";
const message = document.querySelector("#passkey-message");
async function call(action, data = {}) {
  const r = await fetch("/auth/passkeys/" + action, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token":
        document.querySelector('meta[name="tt-csrf"]')?.content || "",
    },
    body: JSON.stringify(data),
  });
  const result = await r.json().catch(() => {
    throw new Error("We couldn’t reach Tasktrack. Try again in a moment.");
  });
  if (!r.ok) {
    const error = new Error(
      result.error?.message ||
        "We couldn’t complete that request. Please try again.",
    );
    error.code = result.error?.code;
    throw error;
  }
  return result;
}
function failure(error) {
  message.replaceChildren();
  message.textContent =
    error.name === "NotAllowedError"
      ? "Passkey request cancelled or unavailable. Try again or use an email code."
      : error.code
        ? error.message
        : error.name === "InvalidStateError"
          ? "This device already has a passkey for your account. Use it to sign in."
          : "We couldn’t complete the passkey request. Try again or use an email code.";
  if (error.code === "recent_sign_in_required") {
    const link = document.createElement("a");
    link.href = "/login?next=/account";
    link.textContent = " Sign in again";
    message.append(link);
  }
}
const login = document.querySelector("#passkey-login"),
  form = document.querySelector("#add-passkey");
if (browserSupportsWebAuthn() && window.isSecureContext) {
  if (login) {
    login.hidden = false;
    login.onclick = async () => {
      login.disabled = true;
      message.textContent = "Waiting for your device…";
      try {
        const options = await call("login/options", {
          next: new URLSearchParams(location.search).get("next") || "/",
        });
        const credential = await startAuthentication({ optionsJSON: options });
        const result = await call("login/verify", { credential });
        location.replace(result.next);
      } catch (e) {
        failure(e);
      } finally {
        login.disabled = false;
      }
    };
  }
  if (form) {
    form.hidden = false;
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const button = form.querySelector("button");
      button.disabled = true;
      message.textContent = "Follow your device’s instructions…";
      try {
        const options = await call("register/options");
        const credential = await startRegistration({ optionsJSON: options });
        await call("register/verify", {
          credential,
          name: document.querySelector("#passkey-name").value,
        });
        location.reload();
      } catch (e) {
        failure(e);
      } finally {
        button.disabled = false;
      }
    };
  }
} else if (message) {
  message.textContent =
    "Passkeys are unavailable in this browser. You can sign in with an email code.";
}
document.querySelectorAll("[data-remove-passkey]").forEach((button) => {
  button.onclick = async () => {
    if (
      !confirm("Remove this passkey? You can still sign in with an email code.")
    )
      return;
    button.disabled = true;
    try {
      await call("remove", { id: button.dataset.removePasskey });
      location.reload();
    } catch (e) {
      failure(e);
      button.disabled = false;
    }
  };
});
