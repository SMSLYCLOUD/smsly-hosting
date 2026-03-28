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
    let stabilityTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;
    const maxReconnectAttempts = 10;

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
      let heartbeatInterval: ReturnType<typeof setInterval> | null = null;

      socket.onopen = () => {
        terminal.writeln('\x1b[32m[connected]\x1b[0m');

        // Complete the backend handshake
        if (socket?.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'ready' }));
        }

        // Don't reset reconnect counter immediately — wait for a stable
        // connection (5s without disconnect) to prevent rapid connect/
        // disconnect loops from resetting the counter every cycle.
        if (stabilityTimer) clearTimeout(stabilityTimer);
        stabilityTimer = setTimeout(() => {
          reconnectAttemptsRef.current = 0;
        }, 5000);

        // Start heartbeat to keep connection alive through proxies
        heartbeatInterval = setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'ping' }));
          }
        }, 15000);
      };

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data && typeof data.message === 'string') {
            // Decode Base64 string back to raw terminal output (UTF-8 safe)
            const binString = atob(data.message);
            const bytes = new Uint8Array(binString.length);
            for (let i = 0; i < binString.length; i++) {
                bytes[i] = binString.charCodeAt(i);
            }
            const decoded = new TextDecoder().decode(bytes);
            terminal.write(decoded);
          }
        } catch {
          // Ignore non-JSON payloads
        }
      };

      socket.onerror = () => {
        terminal.writeln('\r\n\x1b[31m[error] websocket connection failed\x1b[0m');
      };

      socket.onclose = () => {
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        if (disposed) return;
        // Cancel stability timer — connection wasn't stable
        if (stabilityTimer) {
          clearTimeout(stabilityTimer);
          stabilityTimer = null;
        }
        terminal.writeln('\r\n\x1b[31m[disconnected]\x1b[0m');
        if (reconnectAttemptsRef.current >= maxReconnectAttempts) {
          terminal.writeln('\x1b[31m[reconnect limit reached — refresh page to retry]\x1b[0m');
          return;
        }
        reconnectAttemptsRef.current += 1;
        // Exponential backoff with jitter: 2s base, max 10s
        const baseDelay = Math.min(2000 * Math.pow(1.5, reconnectAttemptsRef.current - 1), 10000);
        const jitter = Math.random() * 1000;
        const delayMs = baseDelay + jitter;
        terminal.writeln(
          `\x1b[33m[reconnecting in ${Math.round(delayMs / 1000)}s `
          + `${reconnectAttemptsRef.current}/${maxReconnectAttempts}]\x1b[0m`,
        );
        reconnectTimer = setTimeout(connectWebSocket, delayMs);
      };
    };

    connectWebSocket();

    // Forward raw data immediately to support interactive terminal
    const onDataDisposable: IDisposable = terminal.onData((data) => {
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      // Base64 encode the input (UTF-8 safe) to bypass frontend proxy WAFs (like Cloudflare)
      try {
        const encoder = new TextEncoder();
        const encodedBytes = encoder.encode(data);
        const binString = Array.from(encodedBytes).map(b => String.fromCharCode(b)).join('');
        const base64Payload = btoa(binString);
        socket.send(JSON.stringify({ type: 'input', payload: base64Payload }));
      } catch (err) {
        // Fallback for extremely long paste payloads avoiding call-stack limits
        const encodedUri = encodeURIComponent(data).replace(/%([0-9A-F]{2})/g, (match, p1) => String.fromCharCode(parseInt(p1, 16)));
        socket.send(JSON.stringify({ type: 'input', payload: btoa(encodedUri) }));
      }
    });

    return () => {
      disposed = true;
      reconnectAttemptsRef.current = 0;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      if (stabilityTimer) {
        clearTimeout(stabilityTimer);
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
