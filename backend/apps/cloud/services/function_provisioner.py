import json
import os
import logging

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
        max_code_bytes = int(os.environ.get("FUNCTION_MAX_CODE_BYTES", str(256 * 1024)))
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
        wrapper_code = """
const http = require('http');
const { URL } = require('url');

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
    const result = fn.length >= 2 ? await fn({ ...req, body, query: event.query }, out) : await fn(event);
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
        dockerfile = """
FROM node:18-alpine
WORKDIR /app
COPY . .
ENV PORT=8000
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
        wrapper_code = """
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
        print("%s - %s" % (self.address_string(), fmt % args))

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

        dockerfile = """
FROM python:3.9-slim
WORKDIR /app
# Create and run as non-root user
RUN useradd -m function_user
COPY . .
ENV PORT=8000
USER function_user
CMD ["python", "server.py"]
"""
        with open(os.path.join(build_dir, 'Dockerfile'), 'w', encoding='utf-8') as f:
            f.write(dockerfile)
