"use strict";
const APP = "http://127.0.0.1:8000";
const ALARM = "dealfinder-search";
let busy = false;

async function api(path, token, body = {}) {
  const response = await fetch(`${APP}/browser/ebay/${path}`, {
    method: "POST", headers: {"Content-Type": "application/json", "X-Dealfinder-Token": token},
    body: JSON.stringify(body), signal: AbortSignal.timeout(45000),
  });
  if (!response.ok) throw new Error(response.status === 403 ? "Reconnect the helper from Dealfinder." : `Dealfinder returned HTTP ${response.status}`);
  return response.json();
}

async function stop(reason, report = true) {
  const state = await chrome.storage.local.get(["token", "pending"]);
  await chrome.storage.local.set({enabled: false, pending: null, lastError: reason});
  await chrome.alarms.clear(ALARM);
  await chrome.action.setBadgeText({text: reason ? "!" : ""});
  if (reason && report && state.token) {
    try { await api("results", state.token, {error: reason, query: state.pending?.query || "", captured_at: new Date().toISOString()}); } catch {}
  }
  // Keep the helper's tab open on failure so the user can complete verification.
}

async function tick() {
  if (busy) return;
  busy = true;
  try {
    const state = await chrome.storage.local.get(["token", "enabled", "pending"]);
    if (!state.enabled || !state.token) return;
    if (state.pending) {
      if (Date.now() - state.pending.started > 90000) await stop("Search did not finish. Check the eBay helper tab, then reconnect.");
      return;
    }
    const job = await api("next", state.token);
    if (job.paused) return;
    const url = new URL(job.url);
    const kind = job.kind || "active";
    const parameter = kind === "sold" ? "LH_Sold" : "LH_BIN";
    if (!["active","sold"].includes(kind) || url.origin !== "https://www.ebay.com"
        || url.pathname !== "/sch/i.html" || url.searchParams.get(parameter) !== "1") {
      await stop("Unexpected search URL from Dealfinder."); return;
    }
    const tab = await chrome.tabs.create({url: "about:blank", active: false});
    await chrome.storage.local.set({pending: {tabId: tab.id, query: job.query, kind, url: job.url, started: Date.now()}});
    await chrome.tabs.update(tab.id, {url: job.url});
    await chrome.action.setBadgeText({text: "ON"});
  } catch (error) {
    // An offline local app is retried on the next alarm, without opening eBay tabs.
    await chrome.storage.local.set({lastError: String(error.message).slice(0,200)});
    await chrome.action.setBadgeText({text: "!"});
  } finally { busy = false; }
}

async function handle(message, sender) {
  const url = new URL(sender.url || "https://invalid.local/");
  if (message.type === "pair" || message.type === "stop") {
    if (url.origin !== APP || url.pathname !== "/sources/ebay") return {error: "Untrusted setup page"};
    if (message.type === "stop") { await stop(""); return {ok:true}; }
    if (!/^[A-Za-z0-9_-]{40,100}$/.test(message.token || "")) return {error:"Invalid connection token"};
    const previous = await chrome.storage.local.get("pending");
    if (previous.pending?.tabId) { try { await chrome.tabs.remove(previous.pending.tabId); } catch {} }
    await chrome.storage.local.set({token: message.token, enabled: true, pending: null, lastError: ""});
    await chrome.alarms.create(ALARM, {periodInMinutes: 0.5});
    await tick();
    return {ok:true};
  }
  if (message.type === "capture" || message.type === "ready") {
    const state = await chrome.storage.local.get(["pending", "token", "enabled"]);
    if (!state.enabled || !state.pending || sender.tab?.id !== state.pending.tabId
        || url.origin !== "https://www.ebay.com" || url.pathname !== "/sch/i.html"
        || url.searchParams.get("_nkw") !== state.pending.query
        || url.searchParams.get(state.pending.kind === "sold" ? "LH_Sold" : "LH_BIN") !== "1") {
      return {error:"Not the active helper search"};
    }
    if (message.type === "ready") return {ok:true};
    if (message.error) { await stop(String(message.error).slice(0,200)); return {ok:false}; }
    try {
      await api("results", state.token, {query: state.pending.query, kind:state.pending.kind, html: message.html,
                                       captured_at: message.captured_at});
      await chrome.storage.local.set({pending: null, lastError: ""});
      try { await chrome.tabs.remove(state.pending.tabId); } catch {}
      return {ok:true};
    } catch (error) { await stop(error.message); return {error:error.message}; }
  }
  return {error:"Unknown helper message"};
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  handle(message, sender).then(respond, error => respond({error:error.message}));
  return true;
});
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === ALARM) void tick(); });
chrome.action.onClicked.addListener(() => chrome.tabs.create({url: `${APP}/sources/ebay`}));
async function restore() {
  const {enabled} = await chrome.storage.local.get("enabled");
  if (enabled) await chrome.alarms.create(ALARM, {periodInMinutes:0.5});
}
chrome.runtime.onStartup.addListener(() => { void restore(); });
void restore();
