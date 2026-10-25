"use strict";
// Capture only the helper-owned search, never ordinary user tabs or account pages.
(async () => {
  const permission = await chrome.runtime.sendMessage({type:"ready"});
  if (!permission?.ok) return;
  const started = Date.now();
  function collect() {
    const cards = Array.from(document.querySelectorAll("li.s-card, li.s-item"));
    const heading = document.title + " " + (document.querySelector("h1")?.innerText || "");
    if (/pardon our interruption|verify|captcha|security measure/i.test(heading)) {
      void chrome.runtime.sendMessage({type:"capture", error:"eBay needs browser verification. Complete it in the helper tab, then reconnect."}); return;
    }
    if (!cards.length && !/\b0 results\b|no exact matches found/i.test(document.body.innerText)) {
      if (Date.now() - started < 25000) { setTimeout(collect, 1000); return; }
      void chrome.runtime.sendMessage({type:"capture", error:"No readable eBay search cards. Check the helper tab, then reconnect."}); return;
    }
    const container = document.createElement("ul");
    for (const card of cards.slice(0,250)) {
      const item = document.createElement("li"); item.className = "s-card";
      const fields = [
        [".s-card__title, .s-item__title", "s-card__title"],
        [".s-card__price, .s-item__price", "s-card__price"],
        [".s-card__time-left, .s-item__time-left, .s-card__timeleft, .s-item__time-end", "s-card__time-left"],
        [".s-card__subtitle, .SECONDARY_INFO", "s-card__subtitle"],
        [".s-card__caption, .s-item__caption--signal, .POSITIVE", "s-card__caption"],
        [".s-card__attribute-row, .s-item__shipping, .s-item__logisticsCost, .s-item__purchase-options-with-icon, .s-item__bids, .s-item__bidCount", "s-card__attribute-row"],
      ];
      for (const [selector, className] of fields) {
        for (const node of card.querySelectorAll(selector)) {
          if (className === "s-card__attribute-row" && !/shipping|delivery|buy it now|best offer|\bbids?\b|pickup|\bleft\b/i.test(node.innerText)) continue;
          const field = document.createElement("div"); field.className = className;
          field.textContent = node.innerText; item.append(field);
        }
      }
      // Legacy layout may keep the purchasing label elsewhere in the visible card.
      if (/buy it now/i.test(card.innerText)) {
        const buy = document.createElement("div"); buy.textContent = "Buy It Now"; item.append(buy);
      }
      if (/or best offer/i.test(card.innerText)) {
        const offer = document.createElement("div"); offer.textContent = "or Best Offer"; item.append(offer);
      }
      if (/best offer accepted/i.test(card.innerText)) {
        const accepted = document.createElement("div"); accepted.textContent = "Best offer accepted"; item.append(accepted);
      }
      const sourceLink = card.querySelector('a[href*="/itm/"]');
      if (sourceLink) {
        const link = document.createElement("a");
        link.className = "s-card__link su-link";
        try { const url = new URL(sourceLink.href); link.href = `${url.origin}${url.pathname}`; } catch {}
        item.append(link);
      }
      const image = card.querySelector("img");
      if (image?.src.startsWith("https://i.ebayimg.com/")) {
        const copy = document.createElement("img"); copy.src = image.src; item.append(copy);
      }
      container.append(item);
    }
    if (!cards.length) container.textContent = "0 results";
    void chrome.runtime.sendMessage({type:"capture", html:container.outerHTML, captured_at:new Date().toISOString()});
  }
  collect();
})();
