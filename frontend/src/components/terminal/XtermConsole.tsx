'use client';

import React, { useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import type { IDisposable } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

interface XtermConsoleProps {
  wsUrl: string;
  wsToken?: string | null;
}

export default function XtermConsole({ wsUrl, wsToken }: XtermConsoleProps) {
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
    const maxReconnectAttempts = 15;

    const connectWebSocket = () => {
      if (disposed) return;
      const protocols = wsToken ? ['token', wsToken] : undefined;

      // Validate the URL BEFORE handing it to the browser so we can show a
      // useful diagnostic. `new WebSocket()` throws SyntaxError for any
      // malformed URL but the error object carries no detail, so we
      // pre-flight with the URL constructor.
      if (!wsUrl) {
        terminal.writeln(
          '\x1b[31mConsole unavailable: missing WebSocket URL.\x1b[0m',
        );
        return;
      }
      let parsed: URL;
      try {
        parsed = new URL(wsUrl);
      } catch (parseErr) {
        terminal.writeln(
          '\x1b[31mConsole unavailable: invalid WebSocket URL.\x1b[0m',
        );
        terminal.writeln(
          `\x1b[31m[reason] URL parse failed: ${(parseErr as Error).message}\x1b[0m`,
        );
        console.error('[terminal] invalid wsUrl', { wsUrl, parseErr });
        return;
      }
      if (parsed.protocol !== 'ws:' && parsed.protocol !== 'wss:') {
        terminal.writeln(
          '\x1b[31mConsole unavailable: invalid WebSocket URL.\x1b[0m',
        );
        terminal.writeln(
          `\x1b[31m[reason] expected ws:// or wss:// scheme, got ${parsed.protocol}\x1b[0m`,
        );
        return;
      }
      if (!parsed.host) {
        terminal.writeln(
          '\x1b[31mConsole unavailable: invalid WebSocket URL.\x1b[0m',
        );
        terminal.writeln(
          '\x1b[31m[reason] URL has no host (window.location.host empty?)\x1b[0m',
        );
        return;
      }

      try {
        socket = new WebSocket(wsUrl, protocols);
      } catch (err) {
        terminal.writeln(
          '\x1b[31mConsole unavailable: invalid WebSocket URL.\x1b[0m',
        );
        terminal.writeln(
          `\x1b[31m[reason] ${(err as Error).message}\x1b[0m`,
        );
        console.error('[terminal] WebSocket construct threw', { wsUrl, err });
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
        }, 30000);
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

      socket.onclose = (event) => {
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        if (disposed) return;
        // Cancel stability timer — connection wasn't stable
        if (stabilityTimer) {
          clearTimeout(stabilityTimer);
          stabilityTimer = null;
        }
        console.warn('[terminal] websocket closed', {
          code: event.code,
          reason: event.reason,
          wasClean: event.wasClean,
        });
        terminal.writeln(
          `\r\n\x1b[31m[disconnected code=${event.code}]\x1b[0m`,
        );
        if (event.reason) {
          terminal.writeln(`\x1b[31m[reason] ${event.reason}\x1b[0m`);
        }
        if (reconnectAttemptsRef.current >= maxReconnectAttempts) {
          terminal.writeln('\x1b[31m[reconnect limit reached — refresh page to retry]\x1b[0m');
          return;
        }
        reconnectAttemptsRef.current += 1;
        // Exponential backoff with jitter: 2s base, max 30s
        const baseDelay = Math.min(2000 * Math.pow(1.5, reconnectAttemptsRef.current - 1), 30000);
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
  }, [wsUrl, wsToken]);

  return (
    <div
      ref={terminalRef}
      className="h-full w-full overflow-hidden rounded-lg bg-zinc-950 p-2"
    />
  );
}
