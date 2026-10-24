"use strict";
(async () => {
  const permission = await chrome.runtime.sendMessage({type:"ready"});
  if (!permission?.ok) return;
  const started = Date.now();
  function collect() {
    const heading = document.title + " " + (document.querySelector("h1")?.innerText || "");
    if (/verify|captcha|just a moment|attention required|access denied|sign in/i.test(heading)) {
      void chrome.runtime.sendMessage({type:"capture",error:"HiBid needs sign-in or verification. Finish it in the helper tab, then reconnect HiBid."}); return;
    }
    const cards = Array.from(document.querySelectorAll(".app-auction-lot-card"));
    if (!cards.length && !/\bno lots found\b|\bno results found\b|\b0 lots\b|\b0 results\b/i.test(document.body.innerText)) {
      if (Date.now()-started < 25000) { setTimeout(collect,1000); return; }
      void chrome.runtime.sendMessage({type:"capture",error:"No readable HiBid search cards. Check the helper tab and reconnect HiBid."}); return;
    }
    const container = document.createElement("div");
    for (const card of cards.slice(0,250)) {
      const item = document.createElement("div"); item.className="app-auction-lot-card";
      const title = card.querySelector(".app-auction-lot-card__title");
      if (title) {
        const link = document.createElement("a"); link.className="app-auction-lot-card__title";
        link.textContent=title.innerText;
        try { const url=new URL(title.href); link.href=`${url.origin}${url.pathname}`; } catch {}
        item.append(link);
      }
      for (const className of ["app-auction-lot-card__meta-money","app-auction-lot-badge__value","app-auction-lot-card__shipping","app-auction-lot-card__cta"]) {
        for (const node of card.querySelectorAll(`.${className}`)) {
          const copy=document.createElement("span"); copy.className=className;
          copy.textContent=node.innerText; item.append(copy);
        }
      }
      const image=card.querySelector(".app-auction-lot-card__image");
      if (image?.src.startsWith("https://cdn.hibid.com/")) {
        const copy=document.createElement("img"); copy.className="app-auction-lot-card__image"; copy.src=image.src; item.append(copy);
      }
      container.append(item);
    }
    if (!cards.length) container.textContent="No lots found";
    void chrome.runtime.sendMessage({type:"capture",html:container.outerHTML,captured_at:new Date().toISOString()});
  }
  collect();
})();
