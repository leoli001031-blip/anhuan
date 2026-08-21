import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { User } from "oidc-client-ts";
import { userManager } from "./userManager";

interface AuthContextValue {
  user: User | null;
  isAuthenticated: boolean;
  isInitializing: boolean;
  authError: string | null;
  login: () => Promise<void>;
  logout: () => Promise<void>;
  completeSigninCallback: () => Promise<void>;
  getAccessToken: () => string | null;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  isAuthenticated: false,
  isInitializing: true,
  authError: null,
  login: () => Promise.resolve(),
  logout: () => Promise.resolve(),
  completeSigninCallback: () => Promise.resolve(),
  getAccessToken: () => null,
});

export function OidcProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isInitializing, setIsInitializing] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const isSigninCallback = window.location.pathname === "/callback";
    if (isSigninCallback) {
      // The callback owns the single storage transition.  Starting getUser()
      // in parallel can resolve an older null snapshot after the callback and
      // overwrite the newly authenticated user.
      setIsInitializing(false);
    } else {
      userManager.getUser()
        .then((loaded) => {
          if (!active) return;
          setUser(loaded && !loaded.expired ? loaded : null);
        })
        .catch(() => {
          if (active) setAuthError("OIDC_SESSION_LOAD_FAILED");
        })
        .finally(() => {
          if (active) setIsInitializing(false);
        });
    }
    const onUserLoaded = (loaded: User) => {
      setUser(loaded);
      setAuthError(null);
      setIsInitializing(false);
    };
    const onUserUnavailable = () => setUser(null);
    const onRenewError = () => setAuthError("OIDC_SESSION_RENEW_FAILED");
    userManager.events.addUserLoaded(onUserLoaded);
    userManager.events.addUserUnloaded(onUserUnavailable);
    userManager.events.addAccessTokenExpired(onUserUnavailable);
    userManager.events.addSilentRenewError(onRenewError);
    return () => {
      active = false;
      userManager.events.removeUserLoaded(onUserLoaded);
      userManager.events.removeUserUnloaded(onUserUnavailable);
      userManager.events.removeAccessTokenExpired(onUserUnavailable);
      userManager.events.removeSilentRenewError(onRenewError);
    };
  }, []);

  const login = async () => {
    setAuthError(null);
    await userManager.signinRedirect();
  };
  const logout = async () => {
    await userManager.signoutRedirect();
  };
  const completeSigninCallback = async () => {
    try {
      const loaded = await userManager.signinRedirectCallback();
      if (loaded.expired) throw new Error("OIDC_CALLBACK_EXPIRED");
      setUser(loaded);
      setAuthError(null);
      setIsInitializing(false);
    } catch {
      setUser(null);
      setAuthError("OIDC_CALLBACK_FAILED");
      setIsInitializing(false);
      throw new Error("OIDC_CALLBACK_FAILED");
    }
  };
  const getAccessToken = () => user && !user.expired ? user.access_token : null;

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: Boolean(user && !user.expired),
        isInitializing,
        authError,
        login,
        logout,
        completeSigninCallback,
        getAccessToken,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
