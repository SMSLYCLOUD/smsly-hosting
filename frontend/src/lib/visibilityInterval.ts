/**
 * Schedule an interval that pauses automatically while the browser tab is hidden
 * and fires an immediate catch-up tick when the tab becomes visible again.
 */
export function startVisibilityInterval(
  callback: () => void,
  intervalMs: number,
): () => void {
  const tick = () => {
    if (typeof document !== "undefined" && document.hidden) {
      return;
    }
    callback();
  };

  const timer = setInterval(tick, intervalMs);
  const onVisibilityChange = () => {
    if (typeof document !== "undefined" && !document.hidden) {
      callback();
    }
  };

  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", onVisibilityChange);
  }

  return () => {
    clearInterval(timer);
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", onVisibilityChange);
    }
  };
}
