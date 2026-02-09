"use client";

import { useEffect, useRef } from "react";
import { Terminal } from "xterm";
import { FitAddon } from "xterm-addon-fit";
import "xterm/css/xterm.css";

interface LogsViewerProps {
  deploymentId: string;
}

export function LogsViewer({ deploymentId }: LogsViewerProps) {
  const terminalRef = useRef<HTMLDivElement>(null);
  const termInstance = useRef<Terminal | null>(null);

  useEffect(() => {
    if (!terminalRef.current) return;

    const term = new Terminal({
      cursorBlink: true,
      theme: {
        background: "#0f172a", // Slate-950
        foreground: "#f8fafc", // Slate-50
      },
      fontFamily: '"Menlo", "Monaco", "Courier New", monospace',
      fontSize: 14,
    });

    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);

    term.open(terminalRef.current);
    fitAddon.fit();
    termInstance.current = term;

    term.writeln("\x1b[34m[System]\x1b[0m Connecting to log stream...");

    // Simulate incoming logs
    let count = 0;
    const interval = setInterval(() => {
        count++;
        term.writeln(`\x1b[32m[INFO]\x1b[0m Application starting... (Step ${count})`);
        if (count > 5) {
             term.writeln(`\x1b[33m[WARN]\x1b[0m High memory usage detected.`);
        }
    }, 1000);

    const handleResize = () => fitAddon.fit();
    window.addEventListener("resize", handleResize);

    return () => {
      clearInterval(interval);
      window.removeEventListener("resize", handleResize);
      term.dispose();
    };
  }, [deploymentId]);

  return <div ref={terminalRef} className="h-full w-full min-h-[400px] rounded-md overflow-hidden" />;
}
