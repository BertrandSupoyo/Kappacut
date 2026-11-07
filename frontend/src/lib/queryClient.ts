import { QueryClient } from "@tanstack/react-query";

/** The app's one client. Here so code outside React can refresh a list, as the upload store does. */
export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 10_000 } },
});
