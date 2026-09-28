const utf8 = new TextEncoder();
const b64 = (a) => {
  let s = "";
  for (let i = 0; i < a.length; i += 8192)
    s += String.fromCharCode(...a.subarray(i, i + 8192));
  return btoa(s);
};
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
async function key(raw) {
  const bytes = unb64(raw);
  if (bytes.length !== 32) throw new Error("Invalid encryption key");
  return crypto.subtle.importKey("raw", bytes, "AES-GCM", false, [
    "encrypt",
    "decrypt",
  ]);
}
export async function seal(raw, binding, value) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: utf8.encode(binding) },
    await key(raw),
    utf8.encode(value),
  );
  return JSON.stringify({ iv: b64(iv), data: b64(new Uint8Array(encrypted)) });
}
export async function unseal(raw, binding, value) {
  const p = JSON.parse(value);
  return new TextDecoder().decode(
    await crypto.subtle.decrypt(
      {
        name: "AES-GCM",
        iv: unb64(p.iv),
        additionalData: utf8.encode(binding),
      },
      await key(raw),
      unb64(p.data),
    ),
  );
}
export async function request(input) {
  const {
    url,
    headers,
    method = "GET",
    body,
    binary = false,
    limit = 10485760,
  } = JSON.parse(input);
  const r = await fetch(url, {
    method,
    headers,
    body: body === undefined ? undefined : body,
    redirect: "manual",
    signal: AbortSignal.timeout(30000),
  });
  const reader = r.body?.getReader();
  let size = 0;
  const chunks = [];
  if (reader) {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > limit) {
        await reader.cancel();
        throw new Error("Provider response exceeds limit");
      }
      chunks.push(value);
    }
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const c of chunks) {
    bytes.set(c, offset);
    offset += c.length;
  }
  return JSON.stringify({
    status: r.status,
    headers: Object.fromEntries(r.headers),
    body: binary ? b64(bytes) : new TextDecoder().decode(bytes),
  });
}
