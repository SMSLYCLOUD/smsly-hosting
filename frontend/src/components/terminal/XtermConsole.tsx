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
  const reconnectAttemptsRef = useRef(0);

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

    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;
    const maxReconnectAttempts = 5;

    const connectWebSocket = () => {
      if (disposed) return;
      try {
        socket = new WebSocket(wsUrl);
      } catch {
        terminal.writeln(
          '\x1b[31mConsole unavailable: invalid WebSocket URL.\x1b[0m',
        );
        return;
      }

      ws.current = socket;
      inputBuffer.current = '';

      socket.onopen = () => {
        reconnectAttemptsRef.current = 0;
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
        terminal.writeln('\r\n\x1b[31m[error] websocket connection failed\x1b[0m');
      };

      socket.onclose = () => {
        if (disposed) return;
        terminal.writeln('\r\n\x1b[31m[disconnected]\x1b[0m');
        if (reconnectAttemptsRef.current >= maxReconnectAttempts) {
          terminal.writeln('\x1b[31m[reconnect limit reached]\x1b[0m');
          return;
        }
        reconnectAttemptsRef.current += 1;
        const delayMs = Math.min(1500 * reconnectAttemptsRef.current, 5000);
        terminal.writeln(
          `\x1b[33m[reconnecting in ${Math.round(delayMs / 1000)}s `
          + `${reconnectAttemptsRef.current}/${maxReconnectAttempts}]\x1b[0m`,
        );
        reconnectTimer = setTimeout(connectWebSocket, delayMs);
      };
    };

    connectWebSocket();

    // Forward raw data immediately to support interactive terminal (arrows, shortcuts, etc.)
    const onDataDisposable: IDisposable = terminal.onData((data) => {
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      socket.send(data);
    });

    return () => {
      disposed = true;
      reconnectAttemptsRef.current = 0;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      onDataDisposable.dispose();
      terminal.dispose();
      try {
        socket?.close();
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
