(source) => {
  const title = document.title + " " + (document.querySelector("h1")?.innerText || "");
  const base = {title, url: location.href, count: 0, text: "", html: ""};
  if (!((source === "ebay" && location.hostname === "www.ebay.com" && location.pathname === "/sch/i.html") ||
        (source === "hibid" && location.hostname === "hibid.com" && location.pathname === "/lots"))) return base;
  const cards = Array.from(document.querySelectorAll(source === "ebay" ? "li.s-card,li.s-item" : ".app-auction-lot-card"));
  // Export only public result-card fields. No forms, cookies, account HTML or credentials.
  const empty = /0 results|no exact matches found|no lots found|no results found|0 lots/i.test(document.body?.innerText || "");
  if (source === "ebay") {
    const next = document.querySelector('a.pagination__next, a[rel="next"]');
    base.has_next = !!(next?.getAttribute("href") && next.getAttribute("aria-disabled") !== "true");
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

    return {...base, count: cards.length, text: empty ? "0 results" : "", html: container.outerHTML};
  } else {
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

    return {...base, count: cards.length, text: empty ? "No lots found" : "", html: container.outerHTML};
  }
}
