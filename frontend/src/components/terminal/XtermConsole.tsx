'use client';

import React, { useEffect, useRef } from 'react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import 'xterm/css/xterm.css';

interface XtermConsoleProps {
  wsUrl: string;
}

export default function XtermConsole({ wsUrl }: XtermConsoleProps) {
  const terminalRef = useRef<HTMLDivElement>(null);
  const term = useRef<Terminal | null>(null);
  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!terminalRef.current) return;

    // Initialize xterm.js
    const terminal = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      theme: {
        background: '#09090b', // Zinc 950
        foreground: '#f4f4f5', // Zinc 100
        cursor: '#10b981', // Emerald 500
      },
    });

    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(terminalRef.current);
    fitAddon.fit();

    term.current = terminal;

    // Connect WebSocket
    const socket = new WebSocket(wsUrl);
    ws.current = socket;

    socket.onopen = () => {
      terminal.writeln('\x1b[32m✔ Connected to container terminal.\x1b[0m');
      terminal.writeln('Type \x1b[1mhelp\x1b[0m for commands.\r\n$ ');
    };

    socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        terminal.write(data.message);
    };

    socket.onclose = () => {
      terminal.writeln('\r\n\x1b[31m✘ Disconnected.\x1b[0m');
    };

    // Handle Input
    terminal.onData((data) => {
        if (socket.readyState === WebSocket.OPEN) {
            // For MVP simulation, we send character by character or line.
            // Our backend `TerminalConsumer` expects full strings for commands if simplistic,
            // but real xterm sends keystrokes.
            // Let's implement a simple local echo buffer for this "Simulated Shell"
            // In a real PTY pipe, the backend echoes.

            // Assuming the backend is "Simulated Shell" that expects "command strings", we might need to buffer locally?
            // Actually, let's assume the backend handles echo if we send chars?
            // The `TerminalConsumer` we wrote earlier does NOT handle char-by-char echo well.
            // It expects `receive(text_data)`.

            // Let's send raw input and let backend handle logic, or simplify for MVP:
            // Local echo + Send on Enter.

            if (data === '\r') { // Enter
                terminal.write('\r\n');
                // Send buffer?
                // Let's just send the CR to backend and let it respond?
                socket.send(data);
            } else if (data === '\u007F') { // Backspace
                terminal.write('\b \b');
            } else {
                terminal.write(data);
                socket.send(data);
            }
        }
    });

    // Handle Resize
    const handleResize = () => fitAddon.fit();
    window.addEventListener('resize', handleResize);

    return () => {
      terminal.dispose();
      socket.close();
      window.removeEventListener('resize', handleResize);
    };
  }, [wsUrl]);

  return <div ref={terminalRef} className="h-full w-full overflow-hidden rounded-lg bg-zinc-950 p-2" />;
}
