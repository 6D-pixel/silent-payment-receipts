// The live page: one order on public signet, through the demo wallet, Dana's payment
// method (sppay) and the marketplace. Everything here is a call to those three services.

const API = {
  wallet: import.meta.env.VITE_WALLET_URL ?? "http://127.0.0.1:8403",
  market: import.meta.env.VITE_MARKET_URL ?? "http://127.0.0.1:8402",
  sppay: import.meta.env.VITE_SPPAY_URL ?? "http://127.0.0.1:8401",
  chain: "https://mempool.space/signet/api",
};
const PRICE_SAT = 30_000;
const POLL_MS = 10_000;
const SAVED = "spr-live-order";

// ---------------------------------------------------------------- what the services return

interface Verdict { ok: boolean; code: string; reason: string; amount_sat: number }
interface Payment { txid: string; state: "mempool" | "confirmed" | "not_found"; block_height: number | null; confirmations: number; explorer_url: string }
interface Order {
  id: string; shop: string; item: string; address: string; amount_sat: number; memo: string; invoice_id: string;
  pay_url: string; shop_status: string; has_receipt: boolean; verdict: Verdict | null; shop_check: Verdict | null;
  payment: Payment | null;
}
interface Invoice { status: "open" | "seen" | "paid" | "expired"; paid_height: number | null; receipt_check: Verdict | null }
interface PayResult { txid: string; receipt: object; order: Order; verdict: Verdict }
interface Saved { orderId: string; txid?: string; receipt?: object; paidAt?: number; ruling?: Ruling; cheat?: Verdict }
interface Ruling { ruling: "paid" | "not_paid" | "waiting"; verdict: Verdict }

class ApiError extends Error {}

async function api<T>(base: string, path: string, body?: object): Promise<T> {
  let res: Response;
  try {
    res = await fetch(base + path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(`Can't reach ${base}. Is the service running?`);
  }
  const text = await res.text();
  if (!res.ok) {
    let detail = text;
    try { detail = JSON.parse(text).detail ?? text; } catch { /* plain text */ }
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return JSON.parse(text) as T;
}

// ---------------------------------------------------------------- small helpers

const $ = <T extends HTMLElement = HTMLElement>(sel: string) => document.querySelector(sel) as T;
const sat = (n: number) => `${n.toLocaleString("en-US")} sat`;
const short = (s: string, n = 10) => (s.length > 2 * n + 1 ? `${s.slice(0, n)}…${s.slice(-n)}` : s);

function load(): Saved | null {
  try { return JSON.parse(localStorage.getItem(SAVED) ?? "null"); } catch { return null; }
}
function save(s: Saved | null): void {
  try { s ? localStorage.setItem(SAVED, JSON.stringify(s)) : localStorage.removeItem(SAVED); } catch { /* private window */ }
}

function stamp(ok: boolean, word: string, reason: string, extra?: string): HTMLElement {
  const box = document.createElement("div");
  box.className = `verdict ${ok ? "ok" : "bad"}`;
  box.setAttribute("role", "status");
  const s = document.createElement("p");
  s.className = "stamp";
  s.textContent = word;
  const r = document.createElement("p");
  r.textContent = reason;
  box.append(s, r);
  if (extra) {
    const e = document.createElement("p");
    e.className = "small muted";
    e.textContent = extra;
    box.append(e);
  }
  return box;
}

function showError(message: string | null): void {
  const el = $("#offline");
  el.hidden = !message;
  el.textContent = message ?? "";
}

async function busy(button: HTMLButtonElement, work: () => Promise<void>): Promise<void> {
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await work();
    showError(null);
  } catch (e) {
    showError(e instanceof Error ? e.message : String(e));
  } finally {
    button.removeAttribute("aria-busy");
    render();
  }
}

// ---------------------------------------------------------------- state and rendering

let saved: Saved | null = load();
let order: Order | null = null;
let invoice: Invoice | null = null;
let tip: number | null = null;

function stage(): number {
  if (!order) return 0;
  if (saved?.ruling && saved.ruling.ruling !== "waiting") return 5;
  if (saved?.ruling) return 4;
  if (order.payment?.state === "confirmed") return 3;
  if (order.payment) return 2;
  return 1;
}

function render(): void {
  const now = stage();
  document.querySelectorAll<HTMLElement>(".track li").forEach((li, i) => {
    li.dataset.on = i + 1 < now || now === 5 ? "done" : i + 1 === now ? "now" : "";
  });

  const buy = $<HTMLButtonElement>("#buy"), pay = $<HTMLButtonElement>("#pay");
  const working = (b: HTMLButtonElement) => b.getAttribute("aria-busy") === "true";
  buy.hidden = !!order;
  buy.disabled = working(buy);
  pay.hidden = !order || !!order.has_receipt;
  pay.disabled = working(pay) || invoice?.status === "expired";
  pay.textContent = order ? `Pay ${sat(order.amount_sat)} from the demo wallet` : "Pay from the demo wallet";

  $("#paid").hidden = !saved?.txid;
  if (saved?.txid) {
    $("#txid").textContent = short(saved.txid, 12);
    $<HTMLAnchorElement>("#tx-link").href = `https://mempool.space/signet/tx/${saved.txid}`;
    $("#receipt-json").textContent = JSON.stringify(saved.receipt, null, 2);
  }

  // Dana's side: what her payment method says about the invoice
  const shopState = $("#shop-state");
  $("#invoice").hidden = !order;
  $("#pay-page").hidden = !order;
  if (!order) {
    shopState.dataset.s = "none";
    shopState.textContent = "No order yet";
  } else {
    const s = invoice?.status ?? "open";
    shopState.dataset.s = s;
    shopState.textContent = {
      open: "Waiting for payment",
      seen: "Payment seen in the mempool",
      paid: `Paid in block ${invoice?.paid_height?.toLocaleString("en-US") ?? ""}`,
      expired: "Expired: no payment in time",
    }[s];
    $("#i-address").textContent = short(order.address, 14);
    $("#i-address").title = order.address;
    $("#i-amount").textContent = sat(order.amount_sat);
    $("#i-memo").textContent = order.memo;
    const check = invoice?.receipt_check ?? order.shop_check;
    $("#i-check").textContent = !check ? "Waiting for the receipt"
      : check.ok ? "Receipt checks out" : check.code === "unconfirmed" ? "Receipt is good, waiting for a block" : `Receipt rejected: ${check.reason}`;
    $<HTMLAnchorElement>("#pay-page").href = order.pay_url;
  }
  const dispute = $<HTMLButtonElement>("#dispute");
  dispute.disabled = !order || working(dispute) || (!!saved?.ruling && saved.ruling.ruling !== "waiting");

  // The marketplace: where the payment is, from its own view of the chain
  const mp = $("#m-payment"), wait = $("#m-wait");
  const p = order?.payment;
  wait.hidden = true;
  if (!order) {
    mp.dataset.s = "none";
    mp.textContent = "Waiting for an order";
  } else if (!p) {
    mp.dataset.s = "open";
    mp.textContent = `Order ${order.id}: not paid yet`;
  } else if (p.state === "mempool") {
    mp.dataset.s = "mempool";
    mp.textContent = "Payment is in the mempool, waiting for a block";
    wait.hidden = false;
    const mins = saved?.paidAt ? Math.floor((Date.now() - saved.paidAt) / 60000) : null;
    wait.textContent = `Signet makes a block about every 10 minutes${mins !== null ? `; waiting ${mins} min so far` : ""}. This page checks every 10 seconds.`;
  } else if (p.state === "confirmed") {
    mp.dataset.s = "confirmed";
    mp.textContent = `Confirmed in block ${p.block_height?.toLocaleString("en-US")} (${p.confirmations} confirmation${p.confirmations === 1 ? "" : "s"})`;
  } else {
    mp.dataset.s = "not_found";
    mp.textContent = "The marketplace can't find the payment on chain";
  }

  const ruling = $("#ruling");
  const r = saved?.ruling;
  if (!r) ruling.replaceChildren();
  else if (r.ruling === "paid") ruling.replaceChildren(stamp(true, "Paid", "The receipt proves you paid this order. Dana's claim is rejected.",
    `Checked: the payment to Dana's address, the order ${order?.memo ?? ""}, ${sat(r.verdict.amount_sat)}, confirmed.`));
  else if (r.ruling === "waiting") ruling.replaceChildren(stamp(false, "Wait", "The receipt is good, but the payment isn't in a block yet. Ask again after the next block."));
  else ruling.replaceChildren(stamp(false, "Not paid", r.verdict.reason));

  const cheat = $<HTMLButtonElement>("#cheat");
  cheat.disabled = !saved?.txid || working(cheat);
  const c = saved?.cheat;
  $("#cheat-out").replaceChildren(...(c ? [c.ok
    ? stamp(true, "Accepted", "The second claim was accepted. That should not happen.")
    : stamp(false, "Rejected", c.reason, `Code: ${c.code}`)] : []));

  $("#tip").textContent = tip ? `Public signet: block ${tip.toLocaleString("en-US")}` : "Public signet";
}

// ---------------------------------------------------------------- talking to the services

async function refreshWallet(): Promise<void> {
  const w = await api<{ address: string; balance_sat: number }>(API.wallet, "/api/wallet");
  $("#w-balance").textContent = sat(w.balance_sat);
  $("#w-empty").hidden = w.balance_sat > PRICE_SAT;
  $("#w-address").textContent = short(w.address, 12);
  $("#w-address").title = w.address;
}

async function refresh(): Promise<void> {
  try {
    const t = await fetch(`${API.chain}/blocks/tip/height`);
    if (t.ok) tip = Number(await t.text());
  } catch { /* the tip is only shown, never used to decide anything */ }
  if (saved) {
    order = await api<Order>(API.market, `/api/orders/${saved.orderId}`);
    invoice = await api<Invoice>(API.sppay, `/api/invoices/${order.invoice_id}`);
  }
  render();
}

async function poll(): Promise<void> {
  try {
    await refresh();
    showError(null);
  } catch (e) {
    showError(e instanceof Error ? e.message : String(e));
  }
  setTimeout(poll, POLL_MS);
}

$<HTMLButtonElement>("#buy").addEventListener("click", (e) => busy(e.currentTarget as HTMLButtonElement, async () => {
  order = await api<Order>(API.market, "/api/orders", { shop_id: "dana", item: "Phone", price_sat: PRICE_SAT });
  saved = { orderId: order.id };
  save(saved);
  await refresh();
}));

$<HTMLButtonElement>("#pay").addEventListener("click", (e) => busy(e.currentTarget as HTMLButtonElement, async () => {
  if (!saved) return;
  const r = await api<PayResult>(API.wallet, "/api/pay", { order_id: saved.orderId });
  saved = { ...saved, txid: r.txid, receipt: r.receipt, paidAt: Date.now() };
  save(saved);
  order = r.order;
  await Promise.all([refresh(), refreshWallet()]);
}));

$<HTMLButtonElement>("#dispute").addEventListener("click", (e) => busy(e.currentTarget as HTMLButtonElement, async () => {
  if (!saved) return;
  const r = await api<Ruling & { order: Order }>(API.market, `/api/orders/${saved.orderId}/dispute`, {});
  saved = { ...saved, ruling: { ruling: r.ruling, verdict: r.verdict } };
  save(saved);
  order = r.order;
}));

$<HTMLButtonElement>("#cheat").addEventListener("click", (e) => busy(e.currentTarget as HTMLButtonElement, async () => {
  if (!saved?.txid) return;
  const second = await api<Order>(API.market, "/api/orders", { shop_id: "dana", item: "Second phone", price_sat: PRICE_SAT });
  const r = await api<PayResult>(API.wallet, "/api/reuse", { txid: saved.txid, order_id: second.id });
  saved = { ...saved, cheat: r.verdict };
  save(saved);
}));

$<HTMLButtonElement>("#download").addEventListener("click", () => {
  if (!saved?.receipt) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(saved.receipt, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `receipt-${saved.orderId}.json`;
  a.click();
  URL.revokeObjectURL(url);
});

$<HTMLButtonElement>("#restart").addEventListener("click", () => {
  saved = order = invoice = null;
  save(null);
  render();
});

refreshWallet().catch((e) => showError(e instanceof Error ? e.message : String(e)));
poll();
