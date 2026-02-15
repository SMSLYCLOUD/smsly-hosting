/* eslint-disable no-console */
'use strict';

// Next.js standalone occasionally bubbles client-abort/network socket errors up
// as uncaught exceptions. These should not crash the whole frontend container.

function isIgnorableNetworkError(err) {
  if (!err || typeof err !== 'object') return false;
  const code = err.code;
  return (
    code === 'ECONNRESET' ||
    code === 'EPIPE' ||
    code === 'ERR_STREAM_PREMATURE_CLOSE'
  );
}

let ignoreExitUntil = 0;
const realExit = process.exit.bind(process);

process.exit = (code) => {
  const now = Date.now();
  if (now < ignoreExitUntil) {
    console.warn(`Ignored process.exit(${code}) after ignorable network error`);
    return;
  }
  realExit(code);
};

process.on('uncaughtException', (err) => {
  if (isIgnorableNetworkError(err)) {
    // Short window to intercept any immediate process.exit() calls from other handlers.
    ignoreExitUntil = Date.now() + 250;
  }
});

process.on('unhandledRejection', (reason) => {
  const err = reason instanceof Error ? reason : null;
  if (isIgnorableNetworkError(err)) {
    ignoreExitUntil = Date.now() + 250;
  }
});

require('./server.js');

