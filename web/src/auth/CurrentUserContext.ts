import { createContext, useContext } from "react";

export interface CurrentUser {
  subject: string;
  display_name: string;
  email: string | null;
  scopes: string[];
  roles: string[];
}

export interface CurrentUserContextValue {
  user?: CurrentUser;
  isLoading: boolean;
  isError: boolean;
  hasRole(role: "Admin" | "User"): boolean;
  hasScope(scope: string): boolean;
}

export const CurrentUserContext = createContext<CurrentUserContextValue | null>(null);

export function useCurrentUser(): CurrentUserContextValue {
  const value = useContext(CurrentUserContext);
  if (value === null) {
    throw new Error("useCurrentUser must be used within CurrentUserProvider");
  }
  return value;
}
