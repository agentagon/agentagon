import { useEffect, useRef } from "react";

import { operationId } from "./api";

export type OperationDraft<T extends object> = T & {
  operationId?: string;
  operationBinding?: string;
  draftOmitted?: boolean;
};

export function operationBinding(payload: unknown) {
  const value = typeof payload === "string" ? payload : JSON.stringify(payload);
  let left = 2166136261;
  let right = 2246822519;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    left = Math.imul(left ^ code, 16777619);
    right = Math.imul(right ^ code, 3266489917);
  }
  return `${value.length}:${(left >>> 0).toString(16)}:${(right >>> 0).toString(16)}`;
}

export function loadOperationDraft<T extends object>(key: string): OperationDraft<T> {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "{}");
    return value && typeof value === "object" ? value as OperationDraft<T> : {} as OperationDraft<T>;
  } catch {
    return {} as OperationDraft<T>;
  }
}

/**
 * Keep one operation identity bound to the exact request payload represented by
 * `binding`. Reloads and transport retries reuse it; editing the request rotates
 * it before the next submission. The service remains the final guard against an
 * operation identity being reused for different content.
 */
export function useSessionOperation<T extends object>(key: string, binding: string, draft: T, options: { deferSavedBinding?: boolean } = {}) {
  const loaded = useRef(loadOperationDraft<T>(key));
  const resumeDeferred = Boolean(options.deferSavedBinding && loaded.current.operationId && loaded.current.operationBinding);
  const current = useRef({
    key,
    binding: resumeDeferred ? loaded.current.operationBinding! : binding,
    operation: (resumeDeferred || loaded.current.operationBinding === binding) && loaded.current.operationId
      ? loaded.current.operationId
      : operationId(),
  });
  if (current.current.key !== key) {
    const saved = loadOperationDraft<T>(key);
    const defer = Boolean(options.deferSavedBinding && saved.operationId && saved.operationBinding);
    loaded.current = saved;
    current.current = {
      key,
      binding: defer ? saved.operationBinding! : binding,
      operation: (defer || saved.operationBinding === binding) && saved.operationId
        ? saved.operationId
        : operationId(),
    };
  } else if (!options.deferSavedBinding && current.current.binding !== binding) {
    current.current = { key, binding, operation: operationId() };
  }
  const operation = current.current.operation;
  useEffect(() => {
    if (options.deferSavedBinding) return;
    try {
      sessionStorage.setItem(key, JSON.stringify({ ...draft, operationId: operation, operationBinding: binding }));
    } catch {
      // A pasted trace can exceed the browser's storage quota. Keep the compact
      // idempotency binding even when its editable body cannot be retained.
      try {
        sessionStorage.setItem(key, JSON.stringify({ operationId: operation, operationBinding: binding, draftOmitted: true }));
      } catch {
        // Browsing with storage disabled still works, without reload recovery.
      }
    }
  }, [binding, draft, key, operation, options.deferSavedBinding]);
  return {
    operation,
    clear: () => sessionStorage.removeItem(key),
  };
}
