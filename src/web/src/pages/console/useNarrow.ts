// 窄屏判定：窗口宽度 ≤ maxWidth 时为 true（默认 767，对应 768 折叠点）。
import { useEffect, useState } from "react";

export function useNarrow(maxWidth = 767): boolean {
  const [narrow, setNarrow] = useState(
    () => window.matchMedia(`(max-width: ${maxWidth}px)`).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${maxWidth}px)`);
    const onChange = () => setNarrow(mq.matches);
    mq.addEventListener("change", onChange);
    onChange();
    return () => mq.removeEventListener("change", onChange);
  }, [maxWidth]);
  return narrow;
}
