import {
  verifyRegistrationResponse,
  verifyAuthenticationResponse,
} from "@simplewebauthn/server";
const encode = (bytes) =>
  btoa(String.fromCharCode(...bytes))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
const decode = (text) =>
  Uint8Array.from(atob(text.replaceAll("-", "+").replaceAll("_", "/")), (c) =>
    c.charCodeAt(0),
  );
export async function verify(input) {
  try {
    const { kind, response, challenge, origin, rpID, credential } =
      JSON.parse(input);
    const client = JSON.parse(
      new TextDecoder().decode(decode(response.response.clientDataJSON)),
    );
    if (client.crossOrigin === true || client.topOrigin)
      return JSON.stringify({ verified: false });
    const options = {
      response,
      expectedChallenge: challenge,
      expectedOrigin: origin,
      expectedRPID: rpID,
      requireUserVerification: true,
    };
    if (kind === "register") {
      const r = await verifyRegistrationResponse({
        ...options,
        supportedAlgorithmIDs: [-7],
      });
      if (!r.verified) return JSON.stringify({ verified: false });
      const i = r.registrationInfo;
      return JSON.stringify({
        verified: true,
        id: i.credential.id,
        public_key: encode(i.credential.publicKey),
        counter: i.credential.counter,
        backed_up: i.credentialBackedUp,
        device_type: i.credentialDeviceType,
      });
    }
    const r = await verifyAuthenticationResponse({
      ...options,
      credential: {
        id: credential.id,
        publicKey: decode(credential.public_key),
        counter: credential.counter,
      },
    });
    return JSON.stringify({
      verified: r.verified,
      counter: r.authenticationInfo.newCounter,
    });
  } catch {
    return JSON.stringify({ verified: false });
  }
}
