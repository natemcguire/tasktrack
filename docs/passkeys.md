# Passkeys

Sign in with an email code, open **Account**, and choose **Add passkey**. Give it a
recognizable name, such as “Personal laptop.” Your browser asks you to confirm with
Touch ID, Face ID, a device PIN, or another supported authenticator. No password is
needed. Email codes and sign-in links remain available for recovery.

Passkeys belong to your account across workspaces. Removing one also ends sessions
created with that passkey. Adding or removing a passkey requires signing in within
the last ten minutes. Agent tokens cannot manage passkeys.

## Hosting

Deploy normally; migration `0006_passkeys.sql` adds credential, challenge, and
session-link tables. `PUBLIC_URL` determines the exact allowed origin and relying
party hostname. Keep that hostname stable: credentials registered for one hostname
cannot sign in on an unrelated hostname. HTTPS is required outside localhost.

The build bundles SimpleWebAuthn separately for the browser and Worker. Private
keys and biometric data never reach Tasktrack. The server stores public keys and
credential metadata. Discoverable credentials and user verification are required.
Challenges expire after five minutes, are bound to the initiating browser, and are
consumed once, including failed attempts. Registration also binds the challenge to
the signed-in account and session. Verification checks origin, relying party,
signature, user verification, user handle, and signature counters.

## Local verification

Use `http://localhost:8787` with `npm run dev:cloudflare`; an IP address is not a
valid relying party hostname for these browser checks.

```sh
npm run test:hosted
```

The hosted suite uses a Chromium virtual authenticator for registration, sign-in,
removal and session revocation. It also checks replay, expired challenges, altered
signatures, browser binding, origin and CSRF rejection, recent sign-in, account
ownership, agent-token restrictions, and email recovery. Set
`TEST_BROWSER_CHANNEL=chrome` to use installed Chrome. Virtual-authenticator tests
do not substitute for a person enrolling and using their physical device.
