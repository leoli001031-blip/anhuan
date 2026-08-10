import type { UserManagerSettings } from "oidc-client-ts";

const origin = window.location.origin;

export const oidcConfig: UserManagerSettings = {
  authority: `${origin}/realms/anhuan`,
  client_id: "anhuan-web",
  redirect_uri: `${origin}/callback`,
  post_logout_redirect_uri: `${origin}/`,
  response_type: "code",
  scope: "openid profile email",
  automaticSilentRenew: true,
};
