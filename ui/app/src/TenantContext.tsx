import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";

export type Tenant = {
  slug: string;
  label: string;
  jobs_root: string;
};

type TenantContextValue = {
  tenants: Tenant[];
  tenant: string | null;
  setTenant: (slug: string | null) => void;
  refresh: () => void;
};

const STORAGE_KEY = "image-pipeline:selected-tenant";

const TenantContext = createContext<TenantContextValue>({
  tenants: [],
  tenant: null,
  setTenant: () => {},
  refresh: () => {},
});

export function TenantProvider({ children }: { children: ReactNode }) {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenant, setTenantState] = useState<string | null>(() => localStorage.getItem(STORAGE_KEY));

  const refresh = () => {
    api<{ tenants: Tenant[] }>("/api/tenants")
      .then((d) => {
        const list = d.tenants || [];
        setTenants(list);
        setTenantState((current) => {
          if (current && list.some((t) => t.slug === current)) return current;
          return list[0]?.slug ?? null;
        });
      })
      .catch(() => {});
  };

  useEffect(() => {
    refresh();
  }, []);

  const setTenant = (slug: string | null) => {
    setTenantState(slug);
    if (slug) localStorage.setItem(STORAGE_KEY, slug);
    else localStorage.removeItem(STORAGE_KEY);
  };

  return (
    <TenantContext.Provider value={{ tenants, tenant, setTenant, refresh }}>
      {children}
    </TenantContext.Provider>
  );
}

export function useTenant() {
  return useContext(TenantContext);
}

/** Append the active tenant to a URLSearchParams instance, if one is selected. */
export function withTenant(params: URLSearchParams, tenant: string | null): URLSearchParams {
  if (tenant) params.set("tenant", tenant);
  return params;
}
