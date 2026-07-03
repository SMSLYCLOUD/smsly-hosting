"use client";

import { useEffect, useRef } from "react";
import "@xterm/xterm/css/xterm.css";

interface LogsViewerProps {
  deploymentId: string;
}

export function LogsViewer({ deploymentId }: LogsViewerProps) {
  const terminalRef = useRef<HTMLDivElement>(null);
  const termInstance = useRef<{ dispose: () => void } | null>(null);

  useEffect(() => {
    if (!terminalRef.current) return;

    let disposed = false;
    let term: import('@xterm/xterm').Terminal | null = null;
    let fitAddon: import('@xterm/addon-fit').FitAddon | null = null;
    let interval: ReturnType<typeof setInterval> | null = null;
    let handleResize: (() => void) | null = null;

    (async () => {
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import('@xterm/xterm'),
        import('@xterm/addon-fit'),
      ]);
      if (disposed || !terminalRef.current) return;

      const t = new Terminal({
        cursorBlink: true,
        theme: {
          background: "#0f172a", // Slate-950
          foreground: "#f8fafc", // Slate-50
        },
        fontFamily: '"Menlo", "Monaco", "Courier New", monospace',
        fontSize: 14,
      });

      const f = new FitAddon();
      t.loadAddon(f);

      t.open(terminalRef.current);
      f.fit();
      term = t;
      fitAddon = f;
      termInstance.current = t;

      t.writeln("\x1b[34m[System]\x1b[0m Connecting to log stream...");

      // Simulate incoming logs
      let count = 0;
      interval = setInterval(() => {
          count++;
          t.writeln(`\x1b[32m[INFO]\x1b[0m Application starting... (Step ${count})`);
          if (count > 5) {
               t.writeln(`\x1b[33m[WARN]\x1b[0m High memory usage detected.`);
          }
      }, 1000);

      handleResize = () => f.fit();
      window.addEventListener("resize", handleResize);
    })();

    return () => {
      disposed = true;
      if (interval) clearInterval(interval);
      if (handleResize) window.removeEventListener("resize", handleResize);
      term?.dispose();
      fitAddon = null;
      termInstance.current = null;
    };
  }, [deploymentId]);

  return <div ref={terminalRef} className="h-full w-full min-h-[400px] rounded-md overflow-hidden" />;
}
