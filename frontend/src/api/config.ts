import { useQuery } from "@tanstack/react-query";
import { jget } from "./client";

export interface PublicConfig {
  invite_only: boolean;
  support_email: string;
  copyright_email: string;
  legal: {
    name: string;
    address: string;
    jurisdiction: string;
    eu_representative: string;
    reviewed: boolean;
    version: string;
  };
  processors: { name: string; gets: string }[];
  retention: { warn_days: number; delete_days: number; unverified_days: number; backup_days: number };
}

/** What this deployment says about itself (app/launch.py). It only changes with a restart. */
export function usePublicConfigQuery() {
  return useQuery({
    queryKey: ["public-config"],
    queryFn: () => jget<PublicConfig>("/api/config"),
    staleTime: Infinity,
  });
}

export function usePublicConfig(): PublicConfig | undefined {
  return usePublicConfigQuery().data;
}
