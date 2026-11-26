import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import worker from "../site/worker.js";

const db = new DatabaseSync(":memory:");
db.exec(readFileSync(new URL("../drizzle/0000_device_snapshots.sql", import.meta.url), "utf8"));
const env = {
  COLLECTOR_WRITE_KEY: "test",
  DB: { prepare(sql) {
    const query = db.prepare(sql);
    return {
      bind: (...args) => ({ run: () => query.run(...args), first: () => query.get(...args) }),
      all: () => ({ results: query.all() }),
    };
  } },
};
const now = Date.now();
const id = "a".repeat(32);
const turn = {
  id: "b".repeat(32), status: "completed", started: now - 2200, completed: now,
  model: "selected-model", effort: "high", ttft: 415, rate: 21.5,
  served: ["retired-model"], servedSources: { "retired-model": "response" }, response_id: "discard-me",
};
const old = {
  id, title: "Example chat", status: "completed", model: turn.model, updated: now,
  latest: turn, timing: turn, turns: [turn], routes: [{ from: turn.model, to: "retired-model" }],
};
const report = { version: 1, device: { id, label: "Test device" }, observedAt: now, telemetryAt: now, sessions: [old] };
const url = "https://example.invalid";

function checkFields(value) {
  assert.doesNotMatch(JSON.stringify(value), /"routes"|"served"|"servedSources"|retired-model|discard-me|response_id/);
}
async function read() {
  const headers = { "oai-authenticated-user-id": "owner" };
  const get = (path) => worker.fetch(new Request(url + path, { headers }), env);
  const overview = await (await get("/api/sessions")).json();
  const detail = await (await get("/api/session/" + id)).json();
  checkFields(overview);
  checkFields(detail);
  assert.equal(overview.sessions[0].latest.model, turn.model);
  assert.equal(overview.devices[0].online, true);
  assert.equal(detail.turns[0].ttft, turn.ttft);
  assert.equal(detail.turns[0].rate, turn.rate);
}

// A device that was offline during the upgrade still has its original snapshot.
db.prepare("INSERT INTO device_snapshots VALUES (?,?,?,?,?)").run(id, "Test device", now, now,
  JSON.stringify({ sessions: [old], telemetryAt: now }));
await read();

// An outdated collector can keep reporting timing data to the new Site.
const submit = new Request(url + "/api/report", {
  method: "POST", headers: { "X-Codex-Monitor-Key": "test", "Content-Type": "application/json" },
  body: JSON.stringify(report),
});
assert.equal((await worker.fetch(submit, env)).status, 200);
checkFields(JSON.parse(db.prepare("SELECT payload FROM device_snapshots").get().payload));
await read();
assert.equal((await worker.fetch(new Request(url + "/api/sessions"), env)).status, 401);
db.close();
