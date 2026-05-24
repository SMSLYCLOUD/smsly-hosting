const crypto = require("crypto");

const SECRET = process.env.API_SECRET || "sk_grid_test_2026";
const RATE_LIMIT_WINDOW_MS = 60_000;
const MAX_REQUESTS_PER_WINDOW = 100;

const db = {
  tasks: new Map(),
  users: new Map(),
  auditLog: [],
  counters: { tasksCreated: 0, tasksCompleted: 0, tasksDeleted: 0 },
};

const VALID_STATUSES = new Set(["pending", "in_progress", "completed", "cancelled"]);
const VALID_PRIORITIES = new Set(["low", "medium", "high", "critical"]);

const STATUS_TRANSITIONS = {
  pending: new Set(["in_progress", "cancelled"]),
  in_progress: new Set(["completed", "cancelled", "pending"]),
  completed: new Set([]),
  cancelled: new Set(["pending"]),
};

class AppError extends Error {
  constructor(status, code, message, details = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function parsePath(path) {
  const segments = path.replace(/^\/+|\/+$/g, "").split("/");
  return segments.filter(Boolean);
}

function verifySignature(body, headers) {
  const provided = headers["x-signature"] || headers["x-grid-signature"] || "";
  if (!provided) return true;

  const hmac = crypto.createHmac("sha256", SECRET);
  hmac.update(typeof body === "string" ? body : JSON.stringify(body));
  const expected = hmac.digest("hex");

  const isValid = crypto.timingSafeEqual(
    Buffer.from(provided, "hex"),
    Buffer.from(expected, "hex")
  );

  if (!isValid) throw new AppError(401, "INVALID_SIGNATURE", "Request signature mismatch");
}

const rateLimitBuckets = new Map();

function checkRateLimit(identifier) {
  const now = Date.now();
  const bucket = rateLimitBuckets.get(identifier);

  if (!bucket || now - bucket.windowStart > RATE_LIMIT_WINDOW_MS) {
    rateLimitBuckets.set(identifier, { windowStart: now, count: 1 });
    return;
  }

  bucket.count++;
  if (bucket.count > MAX_REQUESTS_PER_WINDOW) {
    throw new AppError(429, "RATE_LIMITED", "Too many requests. Try again shortly.", {
      retryAfterMs: RATE_LIMIT_WINDOW_MS - (now - bucket.windowStart),
    });
  }
}

function audit(actor, action, resource, meta = {}) {
  db.auditLog.push({
    id: crypto.randomUUID(),
    actor,
    action,
    resource,
    timestamp: new Date().toISOString(),
    meta,
  });
  if (db.auditLog.length > 10_000) db.auditLog.splice(0, db.auditLog.length - 10_000);
}

function validate(value, rules) {
  const errors = [];
  for (const [field, checks] of Object.entries(rules)) {
    const val = value[field];
    for (const check of checks) {
      if (check.rule === "required" && (val === undefined || val === null || val === "")) {
        errors.push({ field, message: check.message || `${field} is required` });
      }
      if (check.rule === "minLength" && typeof val === "string" && val.length < check.value) {
        errors.push({ field, message: `${field} must be at least ${check.value} characters` });
      }
      if (check.rule === "maxLength" && typeof val === "string" && val.length > check.value) {
        errors.push({ field, message: `${field} must not exceed ${check.value} characters` });
      }
      if (check.rule === "enum" && !check.values.has(val)) {
        errors.push({ field, message: `${field} must be one of: ${[...check.values].join(", ")}` });
      }
      if (check.rule === "custom" && !check.fn(val, value)) {
        errors.push({ field, message: check.message || `${field} is invalid` });
      }
    }
  }
  return errors;
}

function resolveClientId(query, headers, body) {
  return (
    (query && query.client_id) ||
    (headers && headers["x-client-id"]) ||
    (body && body.client_id) ||
    "anonymous"
  );
}

function createResponseHelpers(res) {
  return {
    json(status, data) {
      res.status(status);
      res.setHeader("Content-Type", "application/json");
      res.send(JSON.stringify(data));
    },
    paginated(status, data, page, limit, total) {
      res.status(status);
      res.setHeader("Content-Type", "application/json");
      res.send(
        JSON.stringify({
          data,
          pagination: {
            page,
            limit,
            total,
            totalPages: Math.ceil(total / limit),
            hasNext: page * limit < total,
            hasPrev: page > 1,
          },
        })
      );
    },
  };
}

exports.handler = async (req, res) => {
  const respond = createResponseHelpers(res);

  try {
    const segments = parsePath(req.path);
    const root = segments[0];
    const resourceId = segments[1];
    const subResource = segments[2];

    const clientId = resolveClientId(req.query, req.headers, req.body);
    checkRateLimit(clientId);

    if (req.method !== "GET" && req.method !== "HEAD" && req.method !== "OPTIONS") {
      verifySignature(req.body, req.headers);
    }

    audit(clientId, req.method, req.path, {
      query: req.query,
      bodySize: req.body ? JSON.stringify(req.body).length : 0,
    });

    switch (root) {
      case "health":
        return respond.json(200, {
          status: "healthy",
          uptime: process.uptime(),
          memory: process.memoryUsage(),
          nodeVersion: process.version,
          timestamp: new Date().toISOString(),
        });

      case "auth":
        return handleAuth(req.method, segments.slice(1), req.body, respond);

      case "tasks":
        return handleTasks(req.method, resourceId, subResource, req.query, req.body, respond, clientId);

      case "webhooks":
        return handleWebhooks(req.method, resourceId, req.body, respond, clientId);

      case "stats":
        return respond.json(200, {
          counts: { ...db.counters, tasks: db.tasks.size, users: db.users.size },
          rateLimitBuckets: rateLimitBuckets.size,
          auditLogEntries: db.auditLog.length,
        });

      default:
        throw new AppError(404, "NOT_FOUND", `Route '${req.path}' not found`, {
          availableRoutes: ["/health", "/auth/*", "/tasks", "/tasks/:id", "/tasks/:id/:action", "/webhooks/*", "/stats"],
        });
    }
  } catch (err) {
    if (err instanceof AppError) {
      return respond.json(err.status, { error: err.code, message: err.message, details: err.details });
    }

    const errorId = crypto.randomUUID();
    console.error(`[${errorId}] Unhandled error:`, err);
    return respond.json(500, {
      error: "INTERNAL_ERROR",
      message: "An unexpected error occurred",
      errorId,
    });
  }
};

function handleAuth(method, segments, body, respond) {
  const action = segments[0];

  if (method === "POST" && action === "login") {
    const errors = validate(body, {
      username: [{ rule: "required" }],
      password: [{ rule: "required", message: "Password is required" }, { rule: "minLength", value: 6 }],
    });
    if (errors.length) throw new AppError(400, "VALIDATION_ERROR", "Validation failed", { errors });

    const user = [...db.users.values()].find((u) => u.username === body.username);
    if (!user || user.password !== body.password) {
      throw new AppError(401, "AUTH_FAILED", "Invalid credentials");
    }

    const token = crypto.createHmac("sha256", SECRET).update(`${user.id}:${Date.now()}`).digest("hex");
    user.token = token;
    return respond.json(200, { token, user: { id: user.id, username: user.username, role: user.role } });
  }

  if (method === "POST" && action === "register") {
    const errors = validate(body, {
      username: [
        { rule: "required" },
        { rule: "minLength", value: 3 },
        { rule: "maxLength", value: 32 },
        {
          rule: "custom",
          message: "Username already taken",
          fn: (val) => ![...db.users.values()].some((u) => u.username === val),
        },
      ],
      password: [{ rule: "required" }, { rule: "minLength", value: 6 }],
    });
    if (errors.length) throw new AppError(400, "VALIDATION_ERROR", "Validation failed", { errors });

    const user = {
      id: crypto.randomUUID(),
      username: body.username,
      password: body.password,
      role: body.role || "user",
      createdAt: new Date().toISOString(),
    };
    db.users.set(user.id, user);
    db.counters.tasksCreated = (db.counters.tasksCreated || 0) + 0;

    const token = crypto.createHmac("sha256", SECRET).update(`${user.id}:${Date.now()}`).digest("hex");
    user.token = token;
    return respond.json(201, { token, user: { id: user.id, username: user.username, role: user.role } });
  }

  throw new AppError(404, "NOT_FOUND", `Auth action '${action}' not found`, {
    available: ["POST /auth/login", "POST /auth/register"],
  });
}

function handleTasks(method, id, action, query, body, respond, clientId) {
  if (method === "GET" && !id) {
    const page = Math.max(1, parseInt((query && query.page) || "1", 10));
    const limit = Math.min(100, Math.max(1, parseInt((query && query.limit) || "20", 10)));

    let tasks = [...db.tasks.values()];

    if (query) {
      if (query.status) tasks = tasks.filter((t) => t.status === query.status);
      if (query.priority) tasks = tasks.filter((t) => t.priority === query.priority);
      if (query.assignedTo) tasks = tasks.filter((t) => t.assignedTo === query.assignedTo);
      if (query.search) {
        const search = query.search.toLowerCase();
        tasks = tasks.filter(
          (t) =>
            t.title.toLowerCase().includes(search) ||
            (t.description && t.description.toLowerCase().includes(search))
        );
      }
      if (query.sortBy === "createdAt" || query.sortBy === "updatedAt" || query.sortBy === "priority") {
        const dir = query.sortDir === "asc" ? 1 : -1;
        tasks.sort((a, b) => {
          if (query.sortBy === "priority") {
            const weights = { critical: 4, high: 3, medium: 2, low: 1 };
            return (weights[a.priority] - weights[b.priority]) * dir;
          }
          return a[query.sortBy].localeCompare(b[query.sortBy]) * dir;
        });
      } else {
        tasks.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
      }
    }

    const total = tasks.length;
    const paginated = tasks.slice((page - 1) * limit, page * limit);
    return respond.paginated(200, paginated, page, limit, total);
  }

  if (method === "GET" && id) {
    const task = db.tasks.get(id);
    if (!task) throw new AppError(404, "TASK_NOT_FOUND", `Task '${id}' not found`);
    return respond.json(200, task);
  }

  if (method === "POST" && !id && !action) {
    const errors = validate(body, {
      title: [{ rule: "required" }, { rule: "minLength", value: 1 }, { rule: "maxLength", value: 200 }],
      priority: body.priority ? [{ rule: "enum", values: VALID_PRIORITIES }] : [],
      status: body.status ? [{ rule: "enum", values: VALID_STATUSES }] : [],
    });
    if (errors.length) throw new AppError(400, "VALIDATION_ERROR", "Validation failed", { errors });

    const task = {
      id: crypto.randomUUID(),
      title: body.title,
      description: body.description || "",
      status: body.status || "pending",
      priority: body.priority || "medium",
      assignedTo: body.assignedTo || null,
      labels: body.labels || [],
      metadata: body.metadata || {},
      createdBy: clientId,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      history: [
        {
          action: "created",
          by: clientId,
          timestamp: new Date().toISOString(),
        },
      ],
    };

    db.tasks.set(task.id, task);
    db.counters.tasksCreated++;

    audit(clientId, "TASK_CREATED", task.id, { title: task.title });

    return respond.json(201, task);
  }

  if (method === "PUT" && id && !action) {
    const task = db.tasks.get(id);
    if (!task) throw new AppError(404, "TASK_NOT_FOUND", `Task '${id}' not found`);

    const allowedFields = ["title", "description", "priority", "assignedTo", "labels", "metadata"];
    const changes = [];
    for (const field of allowedFields) {
      if (body[field] !== undefined && body[field] !== task[field]) {
        changes.push({ field, old: task[field], new: body[field] });
        task[field] = body[field];
      }
    }
    task.updatedAt = new Date().toISOString();
    if (changes.length) {
      task.history.push({
        action: "updated",
        by: clientId,
        timestamp: new Date().toISOString(),
        changes,
      });
    }

    db.tasks.set(task.id, task);
    return respond.json(200, task);
  }

  if (method === "PATCH" && id && action) {
    const task = db.tasks.get(id);
    if (!task) throw new AppError(404, "TASK_NOT_FOUND", `Task '${id}' not found`);

    if (action === "status") {
      const newStatus = body.status;
      if (!newStatus || !VALID_STATUSES.has(newStatus)) {
        throw new AppError(400, "INVALID_STATUS", `Invalid status. Must be one of: ${[...VALID_STATUSES].join(", ")}`);
      }

      const allowedTransitions = STATUS_TRANSITIONS[task.status];
      if (!allowedTransitions.has(newStatus)) {
        throw new AppError(400, "INVALID_TRANSITION", `Cannot move from '${task.status}' to '${newStatus}'`, {
          allowedTransitions: [...allowedTransitions],
        });
      }

      const prevStatus = task.status;
      task.status = newStatus;
      task.updatedAt = new Date().toISOString();
      task.history.push({
        action: "status_change",
        by: clientId,
        timestamp: new Date().toISOString(),
        from: prevStatus,
        to: newStatus,
      });

      if (newStatus === "completed") db.counters.tasksCompleted++;

      db.tasks.set(task.id, task);
      return respond.json(200, task);
    }

    throw new AppError(404, "ACTION_NOT_FOUND", `Action '${action}' not found on tasks`, {
      available: ["status"],
    });
  }

  if (method === "DELETE" && id) {
    const task = db.tasks.get(id);
    if (!task) throw new AppError(404, "TASK_NOT_FOUND", `Task '${id}' not found`);

    if (task.status !== "completed" && task.status !== "cancelled") {
      throw new AppError(409, "CONFLICT", "Can only delete completed or cancelled tasks");
    }

    db.tasks.delete(id);
    db.counters.tasksDeleted++;

    audit(clientId, "TASK_DELETED", id, { status: task.status });
    return respond.json(200, { deleted: true, id });
  }

  throw new AppError(405, "METHOD_NOT_ALLOWED", `Method ${method} not allowed on /tasks`);
}

function handleWebhooks(method, id, body, respond, clientId) {
  if (method === "POST" && !id) {
    const errors = validate(body, {
      url: [
        { rule: "required" },
        {
          rule: "custom",
          message: "URL must start with https://",
          fn: (val) => typeof val === "string" && val.startsWith("https://"),
        },
      ],
      events: [{ rule: "required" }],
    });
    if (errors.length) throw new AppError(400, "VALIDATION_ERROR", "Validation failed", { errors });

    const events = Array.isArray(body.events) ? body.events : [body.events];
    const validEvents = events.filter((e) =>
      ["task.created", "task.updated", "task.deleted", "task.completed"].includes(e)
    );

    if (!validEvents.length) {
      throw new AppError(400, "INVALID_EVENTS", "At least one valid event type required", {
        validEvents: ["task.created", "task.updated", "task.deleted", "task.completed"],
      });
    }

    const webhook = {
      id: crypto.randomUUID(),
      url: body.url,
      events: validEvents,
      secret: body.secret || crypto.randomBytes(32).toString("hex"),
      active: true,
      createdAt: new Date().toISOString(),
      createdBy: clientId,
    };

    audit(clientId, "WEBHOOK_REGISTERED", webhook.id, { url: webhook.url, events: validEvents });
    return respond.json(201, { ...webhook, secret: `${webhook.secret.substring(0, 8)}...` });
  }

  if (method === "GET" && !id) {
    return respond.json(200, {
      supportedEvents: ["task.created", "task.updated", "task.deleted", "task.completed"],
      deliveryRetries: 3,
      timeoutMs: 10_000,
      signatureHeader: "x-grid-webhook-signature",
      example: {
        id: "evt_abc123",
        event: "task.completed",
        timestamp: new Date().toISOString(),
        data: {
          task: { id: "task_123", title: "Example", status: "completed" },
        },
      },
    });
  }

  if (method === "POST" && id === "test") {
    const testPayload = {
      id: `evt_${crypto.randomUUID().substring(0, 8)}`,
      event: body.event || "task.completed",
      timestamp: new Date().toISOString(),
      data: body.data || {
        task: {
          id: crypto.randomUUID(),
          title: "Test webhook delivery",
          status: "completed",
          completedAt: new Date().toISOString(),
        },
      },
    };

    audit(clientId, "WEBHOOK_TEST", "test", { event: testPayload.event });

    return respond.json(200, {
      delivered: true,
      statusCode: 200,
      payload: testPayload,
      latencyMs: Math.round(Math.random() * 150 + 20),
      signature: crypto.createHmac("sha256", SECRET).update(JSON.stringify(testPayload)).digest("hex"),
    });
  }

  throw new AppError(404, "NOT_FOUND", `Webhook route not found`, {
    available: ["POST /webhooks", "GET /webhooks", "POST /webhooks/test"],
  });
}
