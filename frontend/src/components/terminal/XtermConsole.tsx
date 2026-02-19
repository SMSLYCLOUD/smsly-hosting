'use client';

import React, { useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import type { IDisposable } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

interface XtermConsoleProps {
  wsUrl: string;
}

export default function XtermConsole({ wsUrl }: XtermConsoleProps) {
  const terminalRef = useRef<HTMLDivElement>(null);
  const term = useRef<Terminal | null>(null);
  const ws = useRef<WebSocket | null>(null);
  const inputBuffer = useRef<string>('');

  useEffect(() => {
    if (!terminalRef.current) return;

    const terminal = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      theme: {
        background: '#09090b',
        foreground: '#f4f4f5',
        cursor: '#10b981',
      },
    });

    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(terminalRef.current);
    fitAddon.fit();

    term.current = terminal;

    const handleResize = () => fitAddon.fit();
    window.addEventListener('resize', handleResize);

    if (!wsUrl) {
      terminal.writeln(
        '\x1b[31mConsole unavailable: missing WebSocket URL.\x1b[0m',
      );
      return () => {
        terminal.dispose();
        window.removeEventListener('resize', handleResize);
      };
    }

    let socket: WebSocket;
    try {
      socket = new WebSocket(wsUrl);
    } catch {
      terminal.writeln(
        '\x1b[31mConsole unavailable: invalid WebSocket URL.\x1b[0m',
      );
      return () => {
        terminal.dispose();
        window.removeEventListener('resize', handleResize);
      };
    }

    ws.current = socket;
    inputBuffer.current = '';

    socket.onopen = () => {
      terminal.writeln('\x1b[32m[connected]\x1b[0m');
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data && typeof data.message === 'string') {
          terminal.write(data.message);
        }
      } catch {
        // Ignore non-JSON payloads
      }
    };

    socket.onerror = () => {
      terminal.writeln(
        '\r\n\x1b[31m[error] websocket connection failed\x1b[0m',
      );
    };

    socket.onclose = () => {
      terminal.writeln('\r\n\x1b[31m[disconnected]\x1b[0m');
    };

    // Buffer full command and send on Enter. (Server expects full command lines.)
    const onDataDisposable: IDisposable = terminal.onData((data) => {
      if (socket.readyState !== WebSocket.OPEN) return;

      if (data === '\r') {
        terminal.write('\r\n');
        const cmd = inputBuffer.current;
        inputBuffer.current = '';
        socket.send(cmd);
        return;
      }

      if (data === '\u007F') {
        if (inputBuffer.current.length > 0) {
          inputBuffer.current = inputBuffer.current.slice(0, -1);
          terminal.write('\b \b');
        }
        return;
      }

      inputBuffer.current += data;
      terminal.write(data);
    });

    return () => {
      onDataDisposable.dispose();
      terminal.dispose();
      try {
        socket.close();
      } catch {
        // ignore
      }
      window.removeEventListener('resize', handleResize);
    };
  }, [wsUrl]);

  return (
    <div
      ref={terminalRef}
      className="h-full w-full overflow-hidden rounded-lg bg-zinc-950 p-2"
    />
  );
}
