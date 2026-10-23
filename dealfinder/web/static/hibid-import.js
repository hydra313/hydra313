"use strict";
const pageInput = document.getElementById("hibid-page");
const pasteStatus = document.getElementById("hibid-paste-status");
pageInput.addEventListener("paste", (event) => {
  const html = event.clipboardData?.getData("text/html");
  if (!html) {
    pasteStatus.textContent = "This clipboard contains plain text. Save HiBid as Webpage, Complete and choose its HTML file so listing links are included.";
    return;
  }
  event.preventDefault();
  // Treat copied markup as inert text. Never insert it into the local page DOM.
  pageInput.value = html;
  pasteStatus.textContent = "Copied HTML captured. Check the page capture time, then import.";
});
