import type { UserManagerSettings } from "oidc-client-ts";

export const oidcConfig: UserManagerSettings = {
  authority: "http://127.0.0.1:8080/realms/anhuan",
  client_id: "anhuan-web",
  redirect_uri: "http://127.0.0.1:5173/callback",
  post_logout_redirect_uri: "http://127.0.0.1:5173/",
  response_type: "code",
  scope: "openid profile email",
  automaticSilentRenew: true,
};
