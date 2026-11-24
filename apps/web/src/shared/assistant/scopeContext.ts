import { createContext } from "react";
import type { AssistantScope } from "./contracts";

export const RegisterScope = createContext<((scope: AssistantScope | null) => void) | null>(null);
export const AnalyzeScope = createContext<((scope: Omit<AssistantScope, "page">) => void) | null>(
  null,
);
