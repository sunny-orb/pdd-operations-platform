import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { App } from "./app/App";
import "./styles.css";
import { AssistantWorkspace } from "./shared/assistant/AssistantWorkspace";

import { startPanelSession } from "./shared/layouts/panelLayout";

const endPanelSession = startPanelSession();
if (import.meta.hot) import.meta.hot.dispose(endPanelSession);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AssistantWorkspace>
          <App />
        </AssistantWorkspace>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
