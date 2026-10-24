"use strict";
const APP = "http://127.0.0.1:8000";
const ALARM = "dealfinder-search";
let busy = false;

async function api(path, token, body = {}) {
  const response = await fetch(`${APP}/browser/${path}`, {
    method:"POST", headers:{"Content-Type":"application/json", "X-Dealfinder-Token":token},
    body:JSON.stringify(body), signal:AbortSignal.timeout(45000),
  });
  if (!response.ok) throw new Error(response.status === 403 ? "Reconnect the helper from Connections." : `ConsoleHawk returned HTTP ${response.status}`);
  return response.json();
}
function validJob(job) {
  try {
    const url = new URL(job.url);
    const source = job.source || "ebay";
    if (source === "hibid") return job.kind === "active" && url.origin === "https://hibid.com"
      && url.pathname === "/lots" && url.searchParams.get("q") === job.query;
    return source === "ebay" && ["active","sold"].includes(job.kind || "active")
      && url.origin === "https://www.ebay.com" && url.pathname === "/sch/i.html"
      && url.searchParams.get("_nkw") === job.query
      && url.searchParams.get(job.kind === "sold" ? "LH_Sold" : "LH_BIN") === "1";
  } catch { return false; }
}
async function stop(reason, report = true, onlySite = null) {
  const state = await chrome.storage.local.get(["token","pending","sites"]);
  const source = onlySite || state.pending?.source || "ebay";
  const sites = onlySite ? (state.sites || ["ebay"]).filter(s=>s !== onlySite) : [];
  const affected = !onlySite || !state.pending || (state.pending.source || "ebay") === onlySite;
  await chrome.storage.local.set({enabled:sites.length > 0, sites, pending:affected ? null : state.pending, lastError:reason});
  if (affected && !reason && state.pending?.tabId) {
    try { await chrome.tabs.remove(state.pending.tabId); } catch {}
  }
  if (!sites.length) await chrome.alarms.clear(ALARM);
  await chrome.action.setBadgeText({text:reason ? "!" : ""});
  if (reason && report && state.token) {
    try { await api(`${source}/results`, state.token, {error:reason,query:state.pending?.query || "",captured_at:new Date().toISOString()}); } catch {}
  }
  // A failed site's tab stays open for the user; other enabled sites keep running.
}
async function tick() {
  if (busy) return;
  busy = true;
  try {
    const state = await chrome.storage.local.get(["token","enabled","pending","sites"]);
    if (!state.enabled || !state.token) return;
    if (state.pending) {
      if (Date.now()-state.pending.started > 90000) await stop("Search did not finish. Check the helper tab, then reconnect.",true,state.pending.source || "ebay");
      return;
    }
    const job = await api("next",state.token,{sites:state.sites || ["ebay"]});
    if (job.paused) return;
    if (!validJob(job)) { await stop("Unexpected search URL from ConsoleHawk."); return; }
    const latest = await chrome.storage.local.get(["enabled","sites","token"]);
    if (!latest.enabled || latest.token !== state.token || !(latest.sites || ["ebay"]).includes(job.source || "ebay")) return;
    const tab = await chrome.tabs.create({url:"about:blank",active:false});
    await chrome.storage.local.set({pending:{tabId:tab.id,query:job.query,kind:job.kind || "active",
      source:job.source || "ebay",url:job.url,started:Date.now()}});
    await chrome.tabs.update(tab.id,{url:job.url});
    await chrome.action.setBadgeText({text:"ON"});
  } catch (error) {
    await chrome.storage.local.set({lastError:String(error.message).slice(0,200)});
    await chrome.action.setBadgeText({text:"!"});
  } finally { busy = false; }
}
async function handle(message,sender) {
  const url = new URL(sender.url || "https://invalid.local/");
  if (["pair","stop","status"].includes(message.type)) {
    if (url.origin !== APP || !["/sources/ebay","/sources/hibid","/connections"].includes(url.pathname)) return {error:"Untrusted setup page"};
    if (message.type === "status") {
      const state = await chrome.storage.local.get(["enabled","sites","lastError"]);
      return {ok:true,version:chrome.runtime.getManifest?.().version || "1.2.1",...state};
    }
    if (message.type === "stop") { await stop("",false,message.site || null); return {ok:true}; }
    if (!/^[A-Za-z0-9_-]{40,100}$/.test(message.token || "")) return {error:"Invalid connection token"};
    const previous = await chrome.storage.local.get(["pending","sites","token"]);
    if (previous.pending?.tabId) { try { await chrome.tabs.remove(previous.pending.tabId); } catch {} }
    const add = message.site === "all" ? ["ebay","hibid"] : [message.site || "ebay"];
    if (add.some(s=>!["ebay","hibid"].includes(s))) return {error:"Unknown site"};
    const sites = Array.from(new Set([...(previous.token === message.token ? previous.sites || [] : []),...add]));
    await chrome.storage.local.set({token:message.token,sites,enabled:true,pending:null,lastError:""});
    await chrome.alarms.create(ALARM,{periodInMinutes:0.5});
    await tick(); return {ok:true};
  }
  if (["capture","ready"].includes(message.type)) {
    const state = await chrome.storage.local.get(["pending","token","enabled"]);
    const pending = state.pending;
    if (!state.enabled || !pending || sender.tab?.id !== pending.tabId
      || !validJob({source:pending.source,kind:pending.kind,query:pending.query,url:sender.url})) return {error:"Not the active helper search"};
    if (message.type === "ready") return {ok:true};
    if (message.error) { await stop(String(message.error).slice(0,200),true,pending.source || "ebay"); return {ok:false}; }
    try {
      await api(`${pending.source || "ebay"}/results`,state.token,{query:pending.query,kind:pending.kind,
        html:message.html,captured_at:message.captured_at});
      await chrome.storage.local.set({pending:null,lastError:""});
      try { await chrome.tabs.remove(pending.tabId); } catch {}
      return {ok:true};
    } catch (error) { await stop(error.message,true,pending.source || "ebay"); return {error:error.message}; }
  }
  return {error:"Unknown helper message"};
}
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  handle(message,sender).then(respond,error=>respond({error:error.message})); return true;
});
chrome.alarms.onAlarm.addListener(alarm=>{ if (alarm.name === ALARM) void tick(); });
chrome.action.onClicked.addListener(()=>chrome.tabs.create({url:`${APP}/connections`}));
async function restore() {
  const {enabled} = await chrome.storage.local.get("enabled");
  if (enabled) await chrome.alarms.create(ALARM,{periodInMinutes:0.5});
}
chrome.runtime.onStartup.addListener(()=>{ void restore(); });
void restore();
