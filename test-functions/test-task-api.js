const http = require("http");
const crypto = require("crypto");
const SECRET = "sk_grid_test_2026";

const { handler } = require("./task-api");

function createRes() {
  let _status = 200;
  let _headers = {};
  let _body = "";
  let _ended = false;

  return {
    status(code) {
      _status = code;
      return this;
    },
    setHeader(name, value) {
      _headers[name] = value;
      return this;
    },
    json(data) {
      _body = JSON.stringify(data);
      _headers["Content-Type"] = "application/json";
      _ended = true;
      return this;
    },
    send(data) {
      _body = typeof data === "string" ? data : JSON.stringify(data);
      _ended = true;
      return this;
    },
    getStatus() {
      return _status;
    },
    getBody() {
      return _body;
    },
    getHeaders() {
      return _headers;
    },
    isEnded() {
      return _ended;
    },
  };
}

function createReq(method, path, body = null, extraHeaders = {}) {
  const parsedUrl = new URL(path, "http://localhost");
  const headers = {
    "content-type": "application/json",
    "x-client-id": "test-runner",
    ...extraHeaders,
  };

  const hasPreconfiguredSig = headers["x-signature"] || headers["x-grid-signature"];

  let rawBody = null;
  if (body && !hasPreconfiguredSig) {
    rawBody = JSON.stringify(body);
    const hmac = crypto.createHmac("sha256", SECRET);
    hmac.update(rawBody);
    headers["x-signature"] = hmac.digest("hex");
  } else if (body) {
    rawBody = JSON.stringify(body);
  }

  const req = {
    method,
    path: parsedUrl.pathname,
    query: Object.fromEntries(parsedUrl.searchParams.entries()),
    headers,
    body: rawBody,
    raw: {},
  };

  // Grid's wrapper parses JSON body and replaces req.body with the parsed object.
  // We replicate that behavior here so the handler sees parsed objects.
  if (rawBody) {
    try {
      req.body = JSON.parse(rawBody);
    } catch {}
  }

  return req;
}

async function runTest(name, fn) {
  process.stdout.write(`  ${name}... `);
  try {
    await fn();
    console.log("PASS");
  } catch (err) {
    console.log(`FAIL\n    ${err.message}`);
  }
}

async function main() {
  console.log("\nGrid Serverless Function Tests");
  console.log("==============================\n");

  const tests = [];

  tests.push(async () => {
    const res = createRes();
    const req = createReq("GET", "/health");
    await handler(req, res);
    const body = JSON.parse(res.getBody());
    if (res.getStatus() !== 200) throw new Error(`Expected 200, got ${res.getStatus()}`);
    if (body.status !== "healthy") throw new Error("Expected healthy status");
    if (!body.nodeVersion) throw new Error("Missing nodeVersion");
  });

  tests.push(async () => {
    const res = createRes();
    const req = createReq("GET", "/stats");
    await handler(req, res);
    const body = JSON.parse(res.getBody());
    if (res.getStatus() !== 200) throw new Error(`Expected 200, got ${res.getStatus()}`);
    if (typeof body.counts.tasks !== "number") throw new Error("Missing task count");
  });

  tests.push(async () => {
    const res = createRes();
    const req = createReq("POST", "/auth/register", {
      username: "testuser",
      password: "testpass123",
    });
    await handler(req, res);
    const body = JSON.parse(res.getBody());
    if (res.getStatus() !== 201) throw new Error(`Expected 201, got ${res.getStatus()}`);
    if (!body.token) throw new Error("Missing token");
    if (body.user.username !== "testuser") throw new Error("Wrong username");
  });

  tests.push(async () => {
    const res = createRes();
    const req = createReq("POST", "/auth/login", {
      username: "testuser",
      password: "testpass123",
    });
    await handler(req, res);
    const body = JSON.parse(res.getBody());
    if (res.getStatus() !== 200) throw new Error(`Expected 200, got ${res.getStatus()}`);
  });

  tests.push(async () => {
    const res = createRes();
    const req = createReq("POST", "/auth/login", {
      username: "testuser",
      password: "wrongpass",
    });
    await handler(req, res);
    if (res.getStatus() !== 401) throw new Error(`Expected 401, got ${res.getStatus()}`);
  });

  tests.push(async () => {
    const res = createRes();
    const req = createReq("POST", "/tasks", {
      title: "Deploy to production",
      priority: "high",
      labels: ["urgent", "infra"],
    });
    await handler(req, res);
    const body = JSON.parse(res.getBody());
    if (res.getStatus() !== 201) throw new Error(`Expected 201, got ${res.getStatus()}`);
    if (body.status !== "pending") throw new Error("Expected pending status");
    if (body.priority !== "high") throw new Error("Expected high priority");
    if (!body.id) throw new Error("Missing task id");
  });

  tests.push(async () => {
    const res1 = createRes();
    await handler(createReq("POST", "/tasks", { title: "Task A", priority: "high" }), res1);
    const res2 = createRes();
    await handler(createReq("POST", "/tasks", { title: "Task B", priority: "low" }), res2);
    const res3 = createRes();
    await handler(createReq("POST", "/tasks", { title: "Setup CI/CD", priority: "critical" }), res3);
    const res = createRes();
    await handler(createReq("GET", "/tasks?limit=3&sortBy=priority&sortDir=desc"), res);
    const body = JSON.parse(res.getBody());
    if (res.getStatus() !== 200) throw new Error(`Expected 200, got ${res.getStatus()}`);
    if (body.pagination.total < 3) throw new Error(`Expected >=3 tasks, got ${body.pagination.total}`);
    if (body.data[0].priority !== "critical") throw new Error("Sorting failed");
  });

  tests.push(async () => {
    const res = createRes();
    await handler(createReq("POST", "/tasks", { title: "Test status flow" }), res);
    const task = JSON.parse(res.getBody());

    const resStatus = createRes();
    await handler(createReq("PATCH", `/tasks/${task.id}/status`, { status: "completed" }), resStatus);
    const body = JSON.parse(resStatus.getBody());
    if (resStatus.getStatus() !== 400) throw new Error(`Expected 400 for invalid transition, got ${resStatus.getStatus()}`);

    const resOk = createRes();
    await handler(createReq("PATCH", `/tasks/${task.id}/status`, { status: "in_progress" }), resOk);
    const taskInProgress = JSON.parse(resOk.getBody());
    if (taskInProgress.status !== "in_progress") throw new Error("Status transition failed");
    if (taskInProgress.history.length < 2) throw new Error("Missing history entries");
  });

  tests.push(async () => {
    const res1 = createRes();
    await handler(createReq("POST", "/tasks", { title: "Done task", status: "completed" }), res1);
    const task = JSON.parse(res1.getBody());

    const res = createRes();
    await handler(createReq("DELETE", `/tasks/${task.id}`), res);
    const body = JSON.parse(res.getBody());
    if (res.getStatus() !== 200) throw new Error(`Expected 200, got ${res.getStatus()}`);
    if (!body.deleted) throw new Error("Expected deleted: true");
  });

  tests.push(async () => {
    const res1 = createRes();
    await handler(createReq("POST", "/tasks", { title: "Active task" }), res1);
    const task = JSON.parse(res1.getBody());

    const res = createRes();
    await handler(createReq("DELETE", `/tasks/${task.id}`), res);
    if (res.getStatus() !== 409) throw new Error(`Expected 409 conflict, got ${res.getStatus()}`);
  });

  tests.push(async () => {
    const res1 = createRes();
    await handler(createReq("PUT", "/tasks/nonexistent-id", { title: "Updated" }), res1);
    if (res1.getStatus() !== 404) throw new Error(`Expected 404, got ${res1.getStatus()}`);
  });

  tests.push(async () => {
    const res = createRes();
    await handler(createReq("POST", "/webhooks", {
      url: "https://hooks.example.com/callback",
      events: ["task.created", "task.completed"],
    }), res);
    if (res.getStatus() !== 201) throw new Error(`Expected 201, got ${res.getStatus()}`);
  });

  tests.push(async () => {
    const res = createRes();
    await handler(createReq("POST", "/webhooks", {
      url: "http://insecure.example.com/callback",
      events: ["task.created"],
    }), res);
    if (res.getStatus() !== 400) throw new Error(`Expected 400 for non-https, got ${res.getStatus()}`);
  });

  tests.push(async () => {
    const res = createRes();
    await handler(createReq("POST", "/webhooks/test", { event: "task.completed" }), res);
    const body = JSON.parse(res.getBody());
    if (res.getStatus() !== 200) throw new Error(`Expected 200, got ${res.getStatus()}`);
    if (!body.delivered) throw new Error("Expected delivered");
    if (!body.signature) throw new Error("Missing webhook signature");
  });

  tests.push(async () => {
    const res = createRes();
    await handler(createReq("GET", "/webhooks"), res);
    const body = JSON.parse(res.getBody());
    if (res.getStatus() !== 200) throw new Error(`Expected 200, got ${res.getStatus()}`);
    if (!body.supportedEvents) throw new Error("Missing supportedEvents");
  });

  tests.push(async () => {
    const res = createRes();
    const req = createReq("POST", "/tasks", { title: "Query test", description: "searchable content here" });
    await handler(req, res);
    const task = JSON.parse(res.getBody());

    const resSearch = createRes();
    await handler(createReq("GET", "/tasks?search=searchable"), resSearch);
    const body = JSON.parse(resSearch.getBody());
    if (!body.data.some((t) => t.id === task.id)) throw new Error("Search failed");
  });

  tests.push(async () => {
    const res = createRes();
    await handler(createReq("POST", "/tasks", {
      title: "",
    }), res);
    if (res.getStatus() !== 400) throw new Error(`Expected 400 validation error, got ${res.getStatus()}`);
  });

  tests.push(async () => {
    const res = createRes();
    const req = createReq("POST", "/tasks", { title: "Signed request test" },
      { "x-signature": "0000000000000000000000000000000000000000000000000000000000000000" });
    await handler(req, res);
    if (res.getStatus() !== 401) throw new Error(`Expected 401, got ${res.getStatus()}`);
  });

  tests.push(async () => {
    const res = createRes();
    const req = createReq("GET", "/nonexistent-route");
    await handler(req, res);
    if (res.getStatus() !== 404) throw new Error(`Expected 404, got ${res.getStatus()}`);
  });

  console.log(`Running ${tests.length} tests...\n`);
  for (const test of tests) {
    await test();
  }

  console.log(`\nAll ${tests.length} tests completed.\n`);
}

main().catch(console.error);
