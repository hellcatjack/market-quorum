import { useQuery } from "@tanstack/react-query";
import { useMemo, type ReactNode } from "react";

import { apiRequest } from "../api/client";
import {
  CurrentUserContext,
  type CurrentUser,
  type CurrentUserContextValue,
} from "./CurrentUserContext";

export function CurrentUserProvider({ children }: { children: ReactNode }) {
  const identity = useQuery({
    queryKey: ["current-user"],
    queryFn: () => apiRequest<CurrentUser>("/api/v1/me"),
    staleTime: 60_000,
    retry: false,
  });
  const value = useMemo<CurrentUserContextValue>(
    () => ({
      user: identity.data,
      isLoading: identity.isLoading,
      isError: identity.isError,
      hasRole: (role) => identity.data?.roles.includes(role) ?? false,
      hasScope: (scope) => identity.data?.scopes.includes(scope) ?? false,
    }),
    [identity.data, identity.isError, identity.isLoading],
  );
  return (
    <CurrentUserContext.Provider value={value}>
      {children}
    </CurrentUserContext.Provider>
  );
}
