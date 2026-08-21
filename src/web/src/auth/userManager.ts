import { UserManager } from "oidc-client-ts";
import { oidcConfig } from "./oidcConfig";

export const userManager = new UserManager(oidcConfig);
