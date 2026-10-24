"use strict";
const connect = document.getElementById("connect-ebay-helper");
const stopButton = document.getElementById("stop-ebay-helper");
const status = document.getElementById("helper-connection");
if (connect && status) {
  connect.dataset.helperReady = "true";
  status.textContent = "Browser helper installed. Ready to connect.";
  connect.addEventListener("click", async () => {
    try {
      const response = await chrome.runtime.sendMessage({type:"pair", token:connect.dataset.helperToken});
      status.textContent = response?.ok ? "Connected. Checking one search every 30 seconds while this browser is open." : response?.error || "Could not connect.";
    } catch { status.textContent = "Reload this page after installing or updating the helper."; }
  });
  stopButton?.addEventListener("click", async () => {
    await chrome.runtime.sendMessage({type:"stop"});
    status.textContent = "Browser helper stopped.";
  });
}
