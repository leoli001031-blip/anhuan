import { useCallback, useLayoutEffect, useMemo, useRef } from "react";

// A callback retains the identity of the render that created it. Returning to
// the same route later creates a new identity, so A → B → A cannot revive it.
export function useAsyncContext(key: string): () => boolean {
  const identity = useMemo(() => ({ key, active: false }), [key]);
  const current = useRef(identity);
  useLayoutEffect(() => {
    current.current = identity;
    identity.active = true;
    return () => { identity.active = false; };
  }, [identity]);
  return useCallback(() => current.current === identity && identity.active, [identity]);
}
