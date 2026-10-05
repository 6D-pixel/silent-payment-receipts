// One page, four sections. The nav switches between them by the URL hash (#live, #pay, #shop),
// and each section's script loads the first time it is shown.

import { showFilm } from "./film";

const sections: Record<string, () => Promise<unknown>> = {
  comic: () => import("./main"),
  live: () => import("./live"),
  pay: () => import("./wallet"),
  shop: () => import("./shop"),
};
const started = new Set<string>();
let current = "";

function show(): void {
  const wanted = location.hash.slice(1);
  const name = wanted in sections ? wanted : "comic";
  for (const key of Object.keys(sections)) document.getElementById(`view-${key}`)!.hidden = key !== name;
  showFilm(name === "comic");
  document.querySelectorAll<HTMLAnchorElement>(".tabs [data-view]").forEach((a) => {
    if (a.dataset.view === name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  if (current && current !== name) window.scrollTo(0, 0);
  current = name;
  document.title = { comic: "Silent Payment Receipts", live: "Live Order", pay: "Pay a Shop", shop: "Dana's Shop" }[name]!;
  if (!started.has(name)) {
    started.add(name);
    sections[name]().catch((e) => console.error(`could not start the ${name} section`, e));
  }
}

window.addEventListener("hashchange", show);
show();
