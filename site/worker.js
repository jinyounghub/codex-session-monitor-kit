const ID = /^[a-f0-9]{32}$/;
const MODEL = /^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}$/;
const STATES = new Set(["inProgress", "completed", "interrupted", "failed", "idle"]);
const INGEST_LIMIT = 700 * 1024;
const DEVICE_LIMIT = 25;
const DEVICE_TIMEOUT = 120_000;
const WEEK = 604_800_000;
const HEADERS = {
  "Cache-Control": "no-store",
  "Content-Security-Policy": "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
};

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status, headers: { ...HEADERS, "Content-Type": "application/json; charset=utf-8" },
  });
}

function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function match(value, pattern) {
  return typeof value === "string" && pattern.test(value) ? value : null;
}

function metric(value, max = Number.MAX_SAFE_INTEGER) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= max ? value : null;
}

function values(value, limit, clean) {
  return Array.isArray(value) ? value.slice(0, limit).map(clean).filter(Boolean) : [];
}

function turn(value) {
  if (!object(value) || !match(value.id, ID) || !STATES.has(value.status)) return null;
  const served = values(value.served, 12, (name) => match(name, MODEL));
  return {
    id: value.id, status: value.status,
    started: metric(value.started), completed: metric(value.completed), duration: metric(value.duration),
    model: match(value.model, MODEL), effort: match(value.effort, MODEL),
    served, servedSources: Object.fromEntries(served.map((name) => [
      name, value.servedSources?.[name] === "response" ? "response" : "codex_log",
    ])),
    ttft: metric(value.ttft), rate: metric(value.rate, 1_000_000),
    sample: object(value.sample) ? {
      tokens: metric(value.sample.tokens), count: metric(value.sample.count),
    } : null,
  };
}

function route(value) {
  if (!object(value) || !match(value.from, MODEL) || !match(value.to, MODEL) || !metric(value.at)) return null;
  return {
    from: value.from, to: value.to, at: value.at, turn: match(value.turn, ID),
    source: value.source === "response" ? "response" : "codex_log",
  };
}

function session(value) {
  if (!object(value) || !match(value.id, ID) || !metric(value.updated) || !STATES.has(value.status)) return null;
  const title = typeof value.title === "string" ? value.title.replace(/[\u0000-\u001f\u007f-\u009f]/gu, " ").trim() : "";
  return {
    id: value.id, title: Array.from(title).slice(0, 240).join("") || null,
    model: match(value.model, MODEL), effort: match(value.effort, MODEL),
    status: value.status, updated: value.updated, latest: turn(value.latest), timing: turn(value.timing),
    routes: values(value.routes, 24, route), turns: values(value.turns, 12, turn),
  };
}

async function validKey(request, env) {
  const given = request.headers.get("X-Codex-Monitor-Key");
  if (!given || !env.COLLECTOR_WRITE_KEY || given.length > 4096) return false;
  const encode = new TextEncoder();
  const digests = await Promise.all([given, env.COLLECTOR_WRITE_KEY].map((value) =>
    crypto.subtle.digest("SHA-256", encode.encode(value))));
  const left = new Uint8Array(digests[0]);
  const right = new Uint8Array(digests[1]);
  let difference = 0;
  for (let i = 0; i < left.length; i++) difference |= left[i] ^ right[i];
  return difference === 0;
}

async function report(request, env) {
  if (!await validKey(request, env)) return json({ error: "Collector authentication required" }, 401);
  if (!request.headers.get("Content-Type")?.toLowerCase().startsWith("application/json")) {
    return json({ error: "Expected JSON" }, 415);
  }
  const length = Number(request.headers.get("Content-Length"));
  if (length > INGEST_LIMIT) return json({ error: "Report too large" }, 413);
  const reader = request.body?.getReader();
  if (!reader) return json({ error: "Missing report" }, 400);
  let lengthRead = 0;
  const decoder = new TextDecoder();
  let body = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    lengthRead += value.byteLength;
    if (lengthRead > INGEST_LIMIT) {
      await reader.cancel();
      return json({ error: "Report too large" }, 413);
    }
    body += decoder.decode(value, { stream: true });
  }
  body += decoder.decode();
  let value;
  try { value = JSON.parse(body); } catch { return json({ error: "Invalid JSON" }, 400); }
  const now = Date.now();
  if (!object(value) || value.version !== 1 || !object(value.device) ||
      !match(value.device.id, ID) || typeof value.device.label !== "string" ||
      !/^[^\u0000-\u001f\u007f]{1,48}$/u.test(value.device.label) || !value.device.label.trim() ||
      !Number.isSafeInteger(value.observedAt) || Math.abs(now - value.observedAt) > 300_000 ||
      (value.sessions !== null && (!Array.isArray(value.sessions) || value.sessions.length > 80))) {
    return json({ error: "Invalid device report" }, 400);
  }
  const sessions = value.sessions?.map(session);
  if (sessions && (sessions.includes(null) || new Set(sessions.map((s) => s.id)).size !== sessions.length)) {
    return json({ error: "Invalid sessions" }, 400);
  }
  const serialized = sessions ? JSON.stringify({ sessions, telemetryAt: metric(value.telemetryAt) }) : null;
  await env.DB.prepare(`
    INSERT INTO device_snapshots (device_id, label, observed_at, received_at, payload)
    SELECT ?, ?, ?, ?, COALESCE(?, (SELECT payload FROM device_snapshots WHERE device_id = ?), '{"sessions":[],"telemetryAt":null}')
    WHERE EXISTS (SELECT 1 FROM device_snapshots WHERE device_id = ?)
       OR (SELECT count(*) FROM device_snapshots) < ?
    ON CONFLICT(device_id) DO UPDATE SET
      label = excluded.label, observed_at = excluded.observed_at,
      received_at = excluded.received_at, payload = excluded.payload
    WHERE excluded.observed_at >= device_snapshots.observed_at
  `).bind(value.device.id, value.device.label.trim(), value.observedAt, now, serialized,
    value.device.id, value.device.id, DEVICE_LIMIT).run();
  const stored = await env.DB.prepare("SELECT received_at FROM device_snapshots WHERE device_id = ?")
    .bind(value.device.id).first();
  if (!stored) return json({ error: "Device limit reached" }, 409);
  return json({ accepted: true, receivedAt: stored.received_at });
}

async function getDevices(env) {
  const { results } = await env.DB.prepare(
    "SELECT device_id, label, received_at, payload FROM device_snapshots ORDER BY label, device_id LIMIT 25",
  ).all();
  return results.map((row) => {
    const payload = JSON.parse(row.payload);
    const device = {
      id: row.device_id, label: row.label, receivedAt: row.received_at,
      online: Date.now() - row.received_at < DEVICE_TIMEOUT, sessions: payload.sessions.length,
    };
    return { device, ...payload };
  });
}

async function overview(env) {
  const devices = await getDevices(env);
  const cutoff = Date.now() - WEEK;
  const sessions = devices.flatMap(({ device, sessions }) => sessions.map(({ turns, ...s }) => ({
    ...s, title: s.title || "제목 없는 세션", project: device.label, deviceId: device.id,
    status: !device.online && s.status === "inProgress" ? "idle" : s.status,
    routes: s.routes.filter((item) => item.at >= cutoff),
  }))).sort((a, b) => b.updated - a.updated);
  return json({
    sessions, devices: devices.map(({ device }) => device), updated: Date.now(),
    telemetryAt: Math.max(0, ...devices.map((item) => item.telemetryAt ?? 0)) || null, error: null,
  });
}

async function detail(env, id) {
  if (!match(id, ID)) return json({ error: "Unknown session" }, 404);
  for (const { sessions } of await getDevices(env)) {
    const result = sessions.find((item) => item.id === id);
    if (result) return json({ turns: result.turns, routes: result.routes });
  }
  return json({ error: "Unknown session" }, 404);
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (!env.DB) return json({ error: "Database unavailable" }, 503);
    try {
      if (request.method === "POST" && pathname === "/api/report") return await report(request, env);
      if (request.method !== "GET") return json({ error: "Method not allowed" }, 405);
      if (pathname === "/health") {
        if (!await validKey(request, env)) return json({ error: "Collector authentication required" }, 401);
        return json({ service: "codex-session-monitor" });
      }
      if (!request.headers.get("oai-authenticated-user-id")) return json({ error: "Sign-in required" }, 401);
      if (pathname === "/api/sessions") return await overview(env);
      if (pathname.startsWith("/api/session/")) return await detail(env, pathname.slice(13));
      if (Object.hasOwn(ASSETS, pathname)) {
        const [type, asset] = ASSETS[pathname];
        return new Response(asset, { headers: { ...HEADERS, "Content-Type": type } });
      }
      return json({ error: "Not found" }, 404);
    } catch {
      return json({ error: "Dashboard temporarily unavailable" }, 503);
    }
  },
};
