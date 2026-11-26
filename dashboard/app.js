"use strict";

const byId = (id) => document.getElementById(id);
const ui = {
  connection: byId("connection"),
  notice: byId("notice"),
  noticeTitle: byId("notice-title"),
  noticeBody: byId("notice-body"),
  sessions: byId("sessions"),
  empty: byId("empty"),
  count: byId("session-count"),
  last: byId("last-refresh"),
  search: byId("search"),
  detail: byId("detail"),
  detailHeading: byId("detail-heading"),
  routingHistory: byId("routing-history"),
  turnHistory: byId("turn-history"),
};
const timeFormat = new Intl.DateTimeFormat("ko-KR", { timeStyle: "medium", hour12: false });
const dateFormat = new Intl.DateTimeFormat("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
const oneDecimal = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 });
const twoDecimals = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const status = {
  inProgress: ["진행 중", "active"], completed: ["완료", "done"],
  interrupted: ["중단", "interrupted"], failed: ["실패", "failed"], idle: ["대기", "idle"],
};
let snapshot = null;
let filter = "all";
let deviceFilter = "";
let selected = null;
let lastRows = "";
let lastDetail = "";
let loading = false;
const hosted = document.body.dataset.hosted === "true";
const devicesPanel = byId("devices");
const deviceSelect = byId("device-filter");
if (hosted) byId("scope-label").textContent = "공통 대시보드";

function escapeText(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function time(value) {
  return value ? dateFormat.format(value) : "—";
}

function median(numbers) {
  if (!numbers.length) return null;
  numbers.sort((a, b) => a - b);
  const half = Math.floor(numbers.length / 2);
  return numbers.length % 2 ? numbers[half] : (numbers[half - 1] + numbers[half]) / 2;
}

function metric(value, unit, isDelay = false) {
  if (value == null) return '<span class="unknown" title="아직 수신된 측정값이 없습니다">—</span>';
  const formatted = isDelay ? twoDecimals.format(value / 1000) : oneDecimal.format(value);
  return `<span class="numeric">${formatted}${unit ? `<span class="unit">${unit}</span>` : ""}</span>`;
}

function model(slug, effort) {
  if (!slug) return '<span class="unknown">—</span>';
  return `<span class="model">${escapeText(slug)}</span>${effort ? `<span class="subline">${escapeText(effort)}</span>` : ""}`;
}

function served(turn, routes) {
  if (!turn?.served.length) return '<span class="unknown" title="이 턴에 연결된 서버 모델 ID가 없습니다">미보고</span>';
  const routed = new Set(routes.filter((route) => route.turn === turn.id).map((route) => route.to));
  return turn.served.map((slug) => {
    const source = turn.servedSources?.[slug] === "response" ? "서버 응답 본문에서 확인" : "Codex 모델 로그에서 확인";
    return `<span class="model${routed.has(slug) ? " routed" : ""}" title="${source}">${escapeText(slug)}</span>`;
  }).join("") + (routed.size ? '<span class="subline">재라우팅</span>' : "");
}

function stateBadge(value) {
  const [label, kind] = status[value] ?? ["기록됨", "idle"];
  return `<span class="state ${kind}"><i aria-hidden="true"></i>${label}</span>`;
}

function setConnection(label, state) {
  ui.connection.dataset.state = state;
  ui.connection.lastElementChild.textContent = label;
}

function render() {
  if (!snapshot) return;
  const devices = snapshot.devices ?? [];
  if (hosted) {
    const options = '<option value="">모든 장비</option>' + devices.map((device) =>
      `<option value="${escapeText(device.id)}">${escapeText(device.label)}</option>`).join("");
    if (deviceSelect.innerHTML !== options) deviceSelect.innerHTML = options;
    if (!devices.some((device) => device.id === deviceFilter)) deviceFilter = "";
    deviceSelect.value = deviceFilter;
    deviceSelect.hidden = devices.length < 2;
    devicesPanel.hidden = !devices.length;
    devicesPanel.innerHTML = devices.map((device) => `<div class="device-card">
      <strong>${escapeText(device.label)}</strong>
      <span class="collector-state" data-online="${device.online}">${device.online ? "수집기 온라인" : "수집기 오프라인"}</span>
      <small>마지막 수신 ${escapeText(time(device.receivedAt))}</small>
    </div>`).join("");
  }
  const sessions = snapshot.sessions.filter((session) => !deviceFilter || session.deviceId === deviceFilter);
  const measurements = sessions.map((session) => session.timing).filter(Boolean);
  const middleDelay = median(measurements.map((item) => item.ttft).filter((value) => value != null));
  const middleRate = median(measurements.map((item) => item.rate).filter((value) => value != null));
  const active = sessions.filter((session) => session.status === "inProgress").length;
  byId("active-count").textContent = active;
  byId("route-count").textContent = sessions.reduce((total, session) => total + session.routes.length, 0);
  byId("ttft-summary").textContent = middleDelay == null ? "—" : twoDecimals.format(middleDelay / 1000);
  byId("rate-summary").textContent = middleRate == null ? "—" : oneDecimal.format(middleRate);
  ui.last.textContent = snapshot.updated ? `${timeFormat.format(snapshot.updated)} 갱신` : "";
  if (snapshot.error) {
    setConnection("재연결 중", "error");
    ui.notice.hidden = false;
    ui.noticeTitle.textContent = "Codex 기록에 다시 연결하는 중";
    ui.noticeBody.textContent = "마지막으로 읽은 세션을 표시합니다.";
  } else {
    setConnection("실시간 갱신", "live");
    ui.notice.hidden = hosted ? Boolean(devices.length) : Boolean(snapshot.telemetryAt);
    ui.noticeTitle.textContent = hosted ? "연결된 장비가 없습니다" : "첫 측정 수신 대기";
    ui.noticeBody.textContent = hosted
      ? "장비에서 수집기를 연결하면 측정값이 여기에 표시됩니다."
      : "이미 열려 있던 Codex 앱은 재시작 후 새 요청부터 첫 토큰과 생성 속도를 기록합니다.";
  }

  const word = ui.search.value.toLowerCase().trim();
  const shown = sessions.filter((session) => {
    if (filter === "active" && session.status !== "inProgress") return false;
    if (filter === "routed" && !session.routes.length) return false;
    const routeSlugs = session.routes.flatMap(({ from, to }) => [from, to]);
    const fields = [session.title, session.project, session.model, ...routeSlugs, ...(session.latest?.served ?? [])];
    return !word || fields.filter(Boolean).join(" ").toLowerCase().includes(word);
  });
  ui.count.textContent = shown.length === sessions.length ? String(sessions.length) : `${shown.length} / ${sessions.length}`;
  ui.empty.hidden = Boolean(shown.length);
  ui.empty.textContent = word ? "검색과 일치하는 세션이 없습니다."
    : filter === "routed" ? "최근 7일에 기록된 재라우팅이 없습니다."
      : filter === "active" ? "진행 중인 세션이 없습니다." : "표시할 Codex 세션이 없습니다.";
  const rows = shown.map((session) => {
    const turn = session.latest;
    const subtitles = [session.project, time(session.updated)].filter(Boolean).join(" · ");
    return `<tr data-session="${escapeText(session.id)}" data-selected="${session.id === selected}">
      <td><button class="session-button" type="button" data-open="${escapeText(session.id)}"
        aria-expanded="${session.id === selected}" aria-controls="detail">
        <span class="title">${escapeText(session.title)}</span><span class="subline">${escapeText(subtitles)}</span></button></td>
      <td>${model(session.model, session.effort)}</td>
      <td>${served(turn, session.routes)}</td>
      <td>${metric(turn?.ttft, "초", true)}</td>
      <td>${metric(turn?.rate, "")}</td>
      <td>${stateBadge(session.status)}</td>
    </tr>`;
  }).join("");
  if (rows !== lastRows) {
    const focused = document.activeElement?.dataset.open;
    ui.sessions.innerHTML = rows;
    if (focused) ui.sessions.querySelector(`[data-open="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
    lastRows = rows;
  }
}

function renderDetail(data) {
  const routeHTML = data.routes.length
    ? `<ol class="route-list">${data.routes.map((route) => `<li>
        <div class="route-line mono">${escapeText(route.from)} → ${escapeText(route.to)}</div>
        <span class="subline">${escapeText(time(route.at))}${route.source === "response" ? " · 서버 응답" : ""}${route.turn ? "" : " · 턴 미확인"}</span>
      </li>`).join("")}</ol>`
    : "<p>기록된 재라우팅이 없습니다.</p>";
  const turnHTML = data.turns.length ? `<table aria-label="최근 턴의 모델과 성능">
    <thead><tr><th scope="col">시작</th><th scope="col">요청 모델</th><th scope="col">응답 모델</th>
    <th scope="col">첫 토큰</th><th scope="col">토큰/초</th></tr></thead>
    <tbody>${data.turns.map((turn) => `<tr>
      <td><span>${escapeText(time(turn.started))}</span><span class="subline">${stateBadge(turn.status)}</span></td>
      <td>${model(turn.model, turn.effort)}</td><td>${served(turn, data.routes)}</td>
      <td>${metric(turn.ttft, "초", true)}</td><td>${metric(turn.rate, "")}
        ${turn.sample ? `<span class="subline">${oneDecimal.format(turn.sample.tokens)}토큰 · ${turn.sample.count}스트림</span>` : ""}</td>
    </tr>`).join("")}</tbody></table>` : "<p>읽을 수 있는 턴 기록이 없습니다.</p>";
  const signature = routeHTML + turnHTML;
  if (signature !== lastDetail) {
    ui.routingHistory.innerHTML = routeHTML;
    ui.turnHistory.innerHTML = turnHTML;
    lastDetail = signature;
  }
}

async function loadDetail(id) {
  try {
    const response = await fetch(`/api/session/${encodeURIComponent(id)}`, { signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw Error(String(response.status));
    const data = await response.json();
    if (id === selected) renderDetail(data);
  } catch {
    if (id === selected && !lastDetail) ui.turnHistory.textContent = "턴 기록 연결을 다시 시도합니다.";
  }
}

async function refresh() {
  if (loading) return;
  loading = true;
  try {
    const response = await fetch("/api/sessions", { signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw Error(String(response.status));
    snapshot = await response.json();
    render();
    if (selected) await loadDetail(selected);
  } catch {
    setConnection("연결 끊김", "error");
    ui.notice.hidden = false;
    ui.noticeTitle.textContent = "로컬 대시보드 서버에 연결할 수 없습니다";
    ui.noticeBody.textContent = "연결이 복구되면 자동으로 갱신합니다.";
  } finally {
    loading = false;
  }
}

ui.search.addEventListener("input", render);
deviceSelect.addEventListener("change", () => {
  deviceFilter = deviceSelect.value;
  render();
});
document.querySelector(".filters").addEventListener("click", (event) => {
  const button = event.target.closest("[data-filter]");
  if (!button) return;
  filter = button.dataset.filter;
  for (const sibling of document.querySelectorAll("[data-filter]")) {
    sibling.setAttribute("aria-pressed", String(sibling === button));
  }
  render();
});

ui.sessions.addEventListener("click", (event) => {
  const row = event.target.closest("[data-session]");
  const session = snapshot?.sessions.find(({ id }) => id === row?.dataset.session);
  if (!session) return;
  selected = session.id;
  lastDetail = "";
  ui.detail.hidden = false;
  ui.detailHeading.textContent = session.title;
  ui.routingHistory.textContent = "내역을 불러오는 중";
  ui.turnHistory.textContent = "";
  render();
  ui.detailHeading.focus({ preventScroll: true });
  ui.detail.scrollIntoView({ block: "nearest", behavior: "instant" });
  void loadDetail(selected);
});

byId("close-detail").addEventListener("click", () => {
  const previous = selected;
  selected = null;
  ui.detail.hidden = true;
  render();
  ui.sessions.querySelector(`[data-open="${CSS.escape(previous)}"]`)?.focus({ preventScroll: true });
});

void refresh();
setInterval(refresh, hosted ? 6_000 : 1500);
