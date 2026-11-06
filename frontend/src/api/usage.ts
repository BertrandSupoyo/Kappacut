import { useQuery } from "@tanstack/react-query";
import { jget } from "./client";
import type { Usage } from "./types";

export function useUsage() {
  return useQuery({ queryKey: ["usage"], queryFn: () => jget<Usage>("/api/users/me/usage"), staleTime: 15_000 });
}
