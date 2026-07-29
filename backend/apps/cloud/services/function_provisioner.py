from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

class FunctionProvisioner:
    """
    Handles preparation of serverless function build contexts.
    Wraps raw code in a lightweight container with a HTTP trigger.
    """

    @staticmethod
    def prepare_context(service, build_dir):
        """
        Generates Dockerfile, package.json, and wrapper code for the function.
        """
        runtime = str(getattr(service, 'function_runtime', 'nodejs18') or 'nodejs18').strip()
        code = getattr(service, 'function_code', '') or '// No code provided'
        try:
            max_code_bytes = int(os.environ.get("FUNCTION_MAX_CODE_BYTES", str(256 * 1024)))
        except (TypeError, ValueError):
            max_code_bytes = 256 * 1024
        if len(code.encode("utf-8")) > max_code_bytes:
            raise ValueError(f"Function code exceeds {max_code_bytes} bytes")

        if 'node' in runtime:
            FunctionProvisioner._prepare_node(build_dir, code)
        elif 'python' in runtime:
            FunctionProvisioner._prepare_python(build_dir, code)
        else:
            raise ValueError(f"Unsupported runtime: {runtime}")

    @staticmethod
    def _prepare_node(build_dir, code):
        # 1. Write user code
        # We assume user code exports a handler function
        with open(os.path.join(build_dir, 'index.js'), 'w', encoding='utf-8') as f:
            f.write(code)

        package_json = {
            "name": "smsly-function",
            "version": "1.0.0",
            "private": True,
            "main": "server.js",
        }
        with open(os.path.join(build_dir, 'package.json'), 'w', encoding='utf-8') as f:
            json.dump(package_json, f, indent=2)

        # 2. Write wrapper server with only Node standard-library modules.
        # Avoiding npm install makes function builds deterministic offline.
        # SECURITY: SSRF guard is installed before any user code is required,
        # so user handler code that calls globalThis.fetch, http.request, or
        # https.request is forced through safeFetch / safeRequest. This is
        # defense-in-depth; the primary mitigation is the isolated docker
        # network (smsly-func-net) the container is meant to run on.
        wrapper_code = """
const http = require('http');
const https = require('https');
const { URL } = require('url');
const dns = require('dns').promises;

// ---- SSRF guard (defense in depth) ----
function _isBlockedIpv4(addr) {
  const parts = addr.split('.').map(Number);
  if (parts.length !== 4 || parts.some(p => isNaN(p) || p < 0 || p > 255)) return false;
  if (parts[0] === 0) return true;              // 0.0.0.0/8 (unspecified)
  if (parts[0] === 10) return true;             // RFC1918
  if (parts[0] === 100 && parts[1] >= 64 && parts[1] <= 127) return true; // CGN
  if (parts[0] === 127) return true;            // loopback
  if (parts[0] === 169 && parts[1] === 254) return true; // link-local incl. cloud metadata
  if (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) return true; // RFC1918
  if (parts[0] === 192 && parts[1] === 168) return true; // RFC1918
  if (parts[0] === 192 && parts[1] === 0 && parts[2] === 0) return true; // IETF
  if (parts[0] === 198 && parts[1] === 18) return true;  // benchmarking
  if (parts[0] >= 224) return true;             // multicast/reserved
  return false;
}

function _isBlockedIpv6(addr) {
  const lower = addr.toLowerCase();
  if (lower === '::' || lower === '::1' || lower === '0:0:0:0:0:0:0:1' || lower === '0:0:0:0:0:0:0:0') return true;
  if (lower.startsWith('fc') || lower.startsWith('fd')) return true;          // unique local
  if (/^fe[89ab][0-9a-f]:/i.test(lower)) return true;                         // link-local
  if (lower.startsWith('ff')) return true;                                     // multicast
  return false;
}

function _isBlockedAddr(addr) {
  if (!addr) return true;
  return addr.includes(':') ? _isBlockedIpv6(addr) : _isBlockedIpv4(addr);
}

const _BLOCKED_HOSTNAMES = new Set([
  'localhost',
  'metadata.google.internal',
  '169.254.169.254',
]);

async function _validateUrl(target) {
  const url = new URL(target);
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(`Blocked: protocol ${url.protocol} not allowed`);
  }
  const host = url.hostname.toLowerCase();
  if (_BLOCKED_HOSTNAMES.has(host)) {
    throw new Error(`Blocked: hostname ${host} is reserved`);
  }
  if (_isBlockedAddr(host)) {
    throw new Error(`Blocked: ${host} is a private/reserved IP literal`);
  }
  // Resolve the hostname once to catch records that point at internal IPs
  // (e.g. an attacker-controlled DNS for "api.example.com" -> 10.0.0.5).
  // Note: full DNS-rebinding mitigation requires the HTTP client to use the
  // resolved IP and preserve the original Host header; the host-level network
  // policy (separate docker network) is the real defense.
  const addrs = await dns.lookup(host, { all: true });
  for (const { address } of addrs) {
    if (_isBlockedAddr(address)) {
      throw new Error(`Blocked: ${host} resolves to private/reserved IP ${address}`);
    }
  }
  return url;
}

// Override global fetch (Node 18+ built-in). User handler code calling
// `fetch(...)` or `globalThis.fetch(...)` goes through safeFetch.
const _origFetch = globalThis.fetch;
async function safeFetch(input, init) {
  const target = typeof input === 'string' ? input : (input && input.url) || String(input);
  await _validateUrl(target);
  return _origFetch(input, init);
}
globalThis.fetch = safeFetch;

// Monkey-patch http.request / https.request. The createServer listener is
// untouched. We do a synchronous check for known-bad hostnames / IP literals
// and start the request, then asynchronously re-validate via DNS lookup and
// destroy the request if the resolved address is in a denied range.
const _origHttpRequest = http.request;
function _safeHttpRequest(...args) {
  return _safeRequest(_origHttpRequest, http, args);
}
http.request = _safeHttpRequest;
const _origHttpsRequest = https.request;
function _safeHttpsRequest(...args) {
  return _safeRequest(_origHttpsRequest, https, args);
}
https.request = _safeHttpsRequest;

function _safeRequest(orig, mod, args) {
  let urlArg = null;
  let opts = {};
  let cb = null;
  if (typeof args[0] === 'string' || args[0] instanceof URL) {
    urlArg = args[0];
    opts = args[1] || {};
    cb = args[2];
  } else {
    opts = args[0] || {};
    cb = args[1];
  }
  let target;
  if (urlArg) {
    target = urlArg;
  } else {
    const scheme = (mod === https) ? 'https' : 'http';
    const host = opts.hostname || opts.host || 'localhost';
    target = `${scheme}://${host}${opts.path || '/'}`;
  }
  const url = new URL(target);
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(`Blocked: protocol ${url.protocol} not allowed`);
  }
  const host = url.hostname.toLowerCase();
  if (_BLOCKED_HOSTNAMES.has(host) || _isBlockedAddr(host)) {
    throw new Error(`Blocked: ${host} is in the SSRF deny list`);
  }
  const req = urlArg
    ? orig.call(mod, urlArg, opts, cb)
    : orig.call(mod, opts, cb);
  dns.lookup(host, { all: true }).then((addrs) => {
    for (const { address } of addrs) {
      if (_isBlockedAddr(address)) {
        try { req.destroy(new Error(`Blocked: ${host} resolves to private/reserved IP ${address}`)); } catch (_) {}
        return;
      }
    }
  }).catch(() => { /* DNS failure will surface as a normal request error */ });
  return req;
}

let userHandler;
try {
  userHandler = require('./index.js');
} catch (e) {
  console.error("Failed to load user code:", e);
  process.exit(1);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    const limit = Number(process.env.FUNCTION_MAX_BODY_BYTES || 1048576);
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(new Error('Request body too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      if (!raw) {
        resolve(null);
        return;
      }
      const contentType = String(req.headers['content-type'] || '');
      if (contentType.includes('application/json')) {
        try {
          resolve(JSON.parse(raw));
        } catch (e) {
          reject(new Error('Invalid JSON request body'));
        }
        return;
      }
      resolve(raw);
    });
    req.on('error', reject);
  });
}

function createResponse(res) {
  return {
    headersSent: false,
    statusCode: 200,
    status(code) {
      this.statusCode = Number(code) || 200;
      return this;
    },
    setHeader(name, value) {
      if (!res.headersSent) res.setHeader(name, value);
      return this;
    },
    json(value) {
      if (!res.headersSent) {
        res.statusCode = this.statusCode;
        res.setHeader('content-type', 'application/json; charset=utf-8');
        res.end(JSON.stringify(value));
      }
      this.headersSent = true;
    },
    send(value) {
      if (res.headersSent) {
        this.headersSent = true;
        return;
      }
      res.statusCode = this.statusCode;
      if (value === undefined || value === null) {
        res.end('');
      } else if (Buffer.isBuffer(value) || typeof value === 'string') {
        res.end(value);
      } else {
        res.setHeader('content-type', 'application/json; charset=utf-8');
        res.end(JSON.stringify(value));
      }
      this.headersSent = true;
    }
  };
}

const server = http.createServer(async (req, res) => {
  const parsedUrl = new URL(req.url || '/', 'http://127.0.0.1');
  if (parsedUrl.pathname === '/health' || parsedUrl.pathname === '/healthz') {
    res.statusCode = 200;
    res.setHeader('content-type', 'application/json; charset=utf-8');
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  try {
    const body = await readBody(req);
    const fn = userHandler.handler || userHandler.default || userHandler;
    if (typeof fn !== 'function') {
      createResponse(res).send(fn);
      return;
    }

    const event = {
      method: req.method,
      path: parsedUrl.pathname,
      query: Object.fromEntries(parsedUrl.searchParams.entries()),
      headers: req.headers,
      body,
    };
    const out = createResponse(res);
    const requestLike = {
      method: req.method,
      path: parsedUrl.pathname,
      query: event.query,
      headers: req.headers,
      body,
      raw: req,
    };
    const result = fn.length >= 2 ? await fn(requestLike, out) : await fn(event);
    if (!out.headersSent && !res.headersSent) {
      out.json(result === undefined ? null : result);
    }
  } catch (e) {
    console.error("Function execution error:", e);
    if (!res.headersSent) {
      res.statusCode = 500;
      res.setHeader('content-type', 'application/json; charset=utf-8');
      res.end(JSON.stringify({ error: e.message || 'Function execution failed' }));
    }
  }
});

const port = process.env.PORT || 8000;
server.listen(port, '0.0.0.0', () => {
  console.log(`Function listening on port ${port}`);
});
"""
        with open(os.path.join(build_dir, 'server.js'), 'w', encoding='utf-8') as f:
            f.write(wrapper_code)

        with open(os.path.join(build_dir, '.dockerignore'), 'w', encoding='utf-8') as f:
            f.write("node_modules\nnpm-debug.log\n")

        # 3. Write Dockerfile
        # SECURITY: This container is meant to run on an isolated docker
        # network (e.g. smsly-func-net) that does NOT route to internal
        # services. The SSRF guard in server.js is defense-in-depth. The
        # LABELs below document the intent for orchestrators / image
        # scanners; they do not themselves enforce network policy.
        dockerfile = """
FROM node:18-alpine
LABEL smsly.function.security="ssrf-blocked-required"
LABEL smsly.function.policy="no-metadata,no-internal-services"
# SECURITY: This container is meant to run on an isolated docker network
# (e.g. smsly-func-net) that does NOT route to internal services.
# The SSRF guard in server.js/server.py is defense-in-depth.
WORKDIR /app
COPY . .
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK CMD node -e "require('http').get('http://127.0.0.1:8000/healthz',(r)=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"
# Run as non-root user
USER node
CMD ["node", "server.js"]
"""
        with open(os.path.join(build_dir, 'Dockerfile'), 'w', encoding='utf-8') as f:
            f.write(dockerfile)

    @staticmethod
    def _prepare_python(build_dir, code):
        # Python implementation (Flask wrapper)
        with open(os.path.join(build_dir, 'main.py'), 'w', encoding='utf-8') as f:
            f.write(code)

        # Standard-library wrapper avoids network-dependent pip installs.
        # SECURITY: SSRF guard is installed before the user module is
        # imported, so any user code that calls urllib.request.urlopen (or
        # anything in urllib.request that resolves to it) is forced
        # through safe_url. The BaseHTTPRequestHandler listener is not
        # affected because it lives in http.server, not urllib.request.
        wrapper_code = """
import ipaddress
import socket
from urllib.parse import urlparse
import urllib.request
import urllib.error

BLOCKED_RANGES = [
    ipaddress.ip_network('0.0.0.0/8'),
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('100.64.0.0/10'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.0.0.0/24'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('198.18.0.0/15'),
    ipaddress.ip_network('224.0.0.0/4'),
    ipaddress.ip_network('240.0.0.0/4'),
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fc00::/7'),
    ipaddress.ip_network('fe80::/10'),
]

_BLOCKED_HOSTNAMES = {
    'localhost',
    'metadata.google.internal',
    '169.254.169.254',
}


def _is_blocked_addr(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    for net in BLOCKED_RANGES:
        if ip in net:
            return True
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    return False


def safe_url(target: str) -> str:
    url = urlparse(target)
    if url.scheme not in ('http', 'https'):
        raise ValueError(f'Blocked: protocol {url.scheme} not allowed')
    if not url.hostname:
        raise ValueError(f'Blocked: no hostname in {target}')
    host = url.hostname.lower()
    if host in _BLOCKED_HOSTNAMES or _is_blocked_addr(host):
        raise ValueError(f'Blocked: {host} is a private/reserved host')
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError(f'Blocked: cannot resolve {host}')
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        for net in BLOCKED_RANGES:
            if ip in net:
                raise ValueError(f'Blocked: {host} resolves to {ip} ({net})')
        if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise ValueError(f'Blocked: {host} resolves to {ip} (reserved)')
    return target


# Monkey-patch urllib.request.urlopen so any user handler that calls it
# goes through safe_url first. This catches the common case of
# `urllib.request.urlopen(...)`. Note: code that does
# `from urllib.request import urlopen` keeps the original reference and
# would bypass this patch; full coverage would require a module finder
# hook, which is out of scope.
_orig_urlopen = urllib.request.urlopen


def _safe_urlopen(url, *args, **kwargs):
    target = None
    if isinstance(url, str):
        target = url
    elif isinstance(url, urllib.request.Request):
        target = url.full_url
    if target is not None:
        safe_url(target)
    return _orig_urlopen(url, *args, **kwargs)


urllib.request.urlopen = _safe_urlopen


from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import main
import json
import os
import traceback

MAX_BODY_BYTES = int(os.environ.get("FUNCTION_MAX_BODY_BYTES", "1048576"))

class FunctionRequest:
    def __init__(self, handler, body):
        parsed = urlparse(handler.path)
        self.method = handler.command
        self.path = parsed.path
        self.query = {k: v[-1] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()}
        self.headers = {k: v for k, v in handler.headers.items()}
        self.body = body

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        raw_length = self.headers.get("content-length") or "0"
        try:
            length = int(raw_length)
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            raise ValueError("Request body too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return None
        content_type = self.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("Invalid JSON request body") from exc
        return raw.decode("utf-8", errors="replace")

    def _handle(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/health", "/healthz"}:
            self._send_json(200, {"ok": True})
            return

        try:
            if not hasattr(main, "handler"):
                self._send_json(500, {"error": "No handler function found in main.py"})
                return

            body = self._read_body()
            event = {
                "method": self.command,
                "path": parsed.path,
                "query": {k: v[-1] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()},
                "headers": {k: v for k, v in self.headers.items()},
                "body": body,
            }
            try:
                result = main.handler(event)
            except TypeError:
                result = main.handler(FunctionRequest(self, body))
            if isinstance(result, tuple) and len(result) == 2:
                payload, status = result
                self._send_json(int(status), payload)
            elif isinstance(result, (dict, list, int, float, bool)) or result is None:
                self._send_json(200, result)
            else:
                data = str(result).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/plain; charset=utf-8")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except Exception as exc:
            traceback.print_exc()
            self._send_json(500, {"error": str(exc) or "Function execution failed"})

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_PATCH(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", "8000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
"""
        with open(os.path.join(build_dir, 'server.py'), 'w', encoding='utf-8') as f:
            f.write(wrapper_code)

        with open(os.path.join(build_dir, '.dockerignore'), 'w', encoding='utf-8') as f:
            f.write("__pycache__\n*.pyc\n.venv\n")

        # SECURITY: This container is meant to run on an isolated docker
        # network (e.g. smsly-func-net) that does NOT route to internal
        # services. The SSRF guard in server.py is defense-in-depth. The
        # LABELs below document the intent for orchestrators / image
        # scanners; they do not themselves enforce network policy.
        dockerfile = """
FROM python:3.9-slim
LABEL smsly.function.security="ssrf-blocked-required"
LABEL smsly.function.policy="no-metadata,no-internal-services"
# SECURITY: This container is meant to run on an isolated docker network
# (e.g. smsly-func-net) that does NOT route to internal services.
# The SSRF guard in server.js/server.py is defense-in-depth.
WORKDIR /app
# Create and run as non-root user
RUN useradd -m function_user
COPY . .
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).read()" || exit 1
USER function_user
CMD ["python", "server.py"]
"""
        with open(os.path.join(build_dir, 'Dockerfile'), 'w', encoding='utf-8') as f:
            f.write(dockerfile)
