import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { User, UserManager } from "oidc-client-ts";
import { oidcConfig } from "./oidcConfig";

const userManager = new UserManager(oidcConfig);

interface AuthContextValue {
  user: User | null;
  isAuthenticated: boolean;
  login: () => Promise<void>;
  logout: () => Promise<void>;
  getAccessToken: () => string | null;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  isAuthenticated: false,
  login: () => Promise.resolve(),
  logout: () => Promise.resolve(),
  getAccessToken: () => null,
});

export function OidcProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    userManager.getUser().then((u) => setUser(u ?? null));
    const onUserLoaded = (u: User) => setUser(u);
    userManager.events.addUserLoaded(onUserLoaded);
    return () => userManager.events.removeUserLoaded(onUserLoaded);
  }, []);

  const login = async () => {
    await userManager.signinRedirect();
  };
  const logout = async () => {
    await userManager.signoutRedirect();
  };
  const getAccessToken = () => user?.access_token ?? null;

  return (
    <AuthContext.Provider
      value={{ user, isAuthenticated: !!user, login, logout, getAccessToken }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
