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
        runtime = getattr(service, 'function_runtime', 'nodejs18')
        code = getattr(service, 'function_code', '') or '// No code provided'

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
        with open(os.path.join(build_dir, 'index.js'), 'w') as f:
            f.write(code)

        # 2. Write package.json
        package_json = """
{
  "name": "function",
  "version": "1.0.0",
  "main": "server.js",
  "dependencies": {
    "express": "^4.18.2",
    "body-parser": "^1.20.2"
  }
}
"""
        with open(os.path.join(build_dir, 'package.json'), 'w') as f:
            f.write(package_json)

        # 3. Write wrapper server
        # This wrapper imports the user module and invokes the handler
        wrapper_code = """
const express = require('express');
const bodyParser = require('body-parser');
const app = express();

app.use(bodyParser.json());
app.use(bodyParser.urlencoded({ extended: true }));

// Load user code
let userHandler;
try {
    userHandler = require('./index.js');
} catch (e) {
    console.error("Failed to load user code:", e);
    process.exit(1);
}

app.all('/', async (req, res) => {
    try {
        // Support: exports.handler, exports.default, or module.exports
        const fn = userHandler.handler || userHandler.default || userHandler;

        if (typeof fn === 'function') {
            // Express-style handler (req, res) or async returning value?
            // Let's support (req, res) standard
            if (fn.length === 2) {
                await fn(req, res);
            } else {
                // Lambda-style (event, context)? Or just return value
                const result = await fn(req.body);
                if (!res.headersSent) {
                    res.json(result);
                }
            }
        } else {
            res.send(fn); // Return object/string directly
        }
    } catch (e) {
        console.error("Function execution error:", e);
        if (!res.headersSent) {
            res.status(500).json({ error: e.message });
        }
    }
});

const port = process.env.PORT || 8000;
app.listen(port, () => {
    console.log(`Function listening on port ${port}`);
});
"""
        with open(os.path.join(build_dir, 'server.js'), 'w') as f:
            f.write(wrapper_code)

        # 4. Write Dockerfile
        dockerfile = """
FROM node:18-alpine
WORKDIR /app
COPY package.json .
RUN npm install
COPY . .
ENV PORT=8000
CMD ["node", "server.js"]
"""
        with open(os.path.join(build_dir, 'Dockerfile'), 'w') as f:
            f.write(dockerfile)

    @staticmethod
    def _prepare_python(build_dir, code):
        # Python implementation (Flask wrapper)
        with open(os.path.join(build_dir, 'main.py'), 'w') as f:
            f.write(code)

        requirements = "flask==3.0.0\ngunicorn==21.2.0"
        with open(os.path.join(build_dir, 'requirements.txt'), 'w') as f:
            f.write(requirements)

        wrapper_code = """
from flask import Flask, request, jsonify
import main
import traceback

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def handle():
    try:
        # Expecting 'handler(data)' or 'handler(request)'
        if hasattr(main, 'handler'):
            # Check if it accepts 1 arg
            try:
                return jsonify(main.handler(request.json or {}))
            except TypeError:
                # Maybe it expects request object?
                return main.handler(request)
        else:
            return "No handler function found in main.py", 500
    except Exception as e:
        traceback.print_exc()
        return str(e), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
"""
        with open(os.path.join(build_dir, 'server.py'), 'w') as f:
            f.write(wrapper_code)

        dockerfile = """
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
ENV PORT=8000
CMD ["gunicorn", "-b", "0.0.0.0:8000", "server:app"]
"""
        with open(os.path.join(build_dir, 'Dockerfile'), 'w') as f:
            f.write(dockerfile)
