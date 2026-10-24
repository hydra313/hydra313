"use strict";
const status = document.getElementById("helper-connection");
const buttons = Array.from(document.querySelectorAll("[data-connect-site]"));
const legacy = document.getElementById("connect-ebay-helper");
if (legacy) { legacy.dataset.connectSite = "ebay"; buttons.push(legacy); }
if (status && buttons.length) {
  for (const button of buttons) {
    button.dataset.helperReady="true";
    button.addEventListener("click",async()=>{
      try {
        const response=await chrome.runtime.sendMessage({type:"pair",site:button.dataset.connectSite,token:button.dataset.helperToken});
        status.textContent=response?.ok ? "Connected. Automatic searches run every 30 seconds across enabled sites." : response?.error || "Could not connect.";
      } catch { status.textContent="Reload this page after updating the helper."; }
    });
  }
  const stops=Array.from(document.querySelectorAll("[data-stop-site]"));
  const legacyStop=document.getElementById("stop-ebay-helper");
  if (legacyStop) { legacyStop.dataset.stopSite="ebay"; stops.push(legacyStop); }
  for (const button of stops) button.addEventListener("click",async()=>{
    const site=button.dataset.stopSite;
    await chrome.runtime.sendMessage({type:"stop",site:site === "all" ? null : site});
    status.textContent=site === "all" ? "All browser searches stopped." : `${site === "ebay" ? "eBay" : "HiBid"} searches stopped.`;
  });
  chrome.runtime.sendMessage({type:"status"}).then(state=>{
    status.textContent=state?.enabled ? `Helper ${state.version} installed. Automatic search enabled for ${(state.sites || ["ebay"]).join(" and ")}.` : "Browser helper 1.3 installed. Ready to connect.";
  }).catch(()=>{ status.textContent="Reload this page after updating the helper."; });
}
