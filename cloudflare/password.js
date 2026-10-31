// PBKDF2 for the approval password, on WebCrypto. Pyodide's hashlib has no
// pbkdf2_hmac and a pure-Python loop of 210k HMAC rounds would eat the CPU budget;
// the Worker's native implementation takes milliseconds. Host tests use hashlib.
export async function pbkdf2(password, saltHex, iterations) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const salt = Uint8Array.from(saltHex.match(/../g), (h) => parseInt(h, 16));
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-512", salt, iterations },
    key,
    512,
  );
  return [...new Uint8Array(bits)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
