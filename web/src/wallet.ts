// Pay a shop: look up a shop link or address, pay from the demo wallet, keep the receipt,
// and have the marketplace check it.

import {
  $, API, EXPLORER_TX, api, busy, download, el, message, sat, short, showError, showTip, stamp, txLine,
  type TxStatus, type Verdict,
} from "./services";

const POLL_MS = 10_000;
const SAVED = "spr-wallet-payment";

interface Payee {
  kind: "shop" | "invoice" | "address";
  address: string;
  name: string | null;    // the shop's name, when we know which shop this is
  base: string | null;    // the shop's payment server, to tell it about the payment
  invoiceId?: string;
  amount?: number;        // fixed by an invoice
  memo?: string;
}
interface Paid { txid: string; receipt: object; amount: number; memo: string; payee: Payee; shopTold: string }
interface Check { kind: "order" | "direct"; verdict: Verdict }

let payee: Payee | null = null;
let paid: Paid | null = load();
let tx: TxStatus | null = null;
let check: Check | null = null;

function load(): Paid | null {
  try { return JSON.parse(localStorage.getItem(SAVED) ?? "null"); } catch { return null; }
}
function save(): void {
  try { paid ? localStorage.setItem(SAVED, JSON.stringify(paid)) : localStorage.removeItem(SAVED); } catch { /* private window */ }
}

// ---------------------------------------------------------------- finding out who we are paying

async function shopByAddress(address: string): Promise<{ name: string; url: string } | null> {
  const shops = await api<{ name: string; address: string; url: string }[]>(API.market, "/api/shops");
  return shops.find((s) => s.address.toLowerCase() === address.toLowerCase()) ?? null;
}

async function resolve(input: string): Promise<Payee> {
  const text = input.trim();
  if (/^t?sp1/i.test(text)) {
    if (!/^tsp1/i.test(text)) throw new Error("That is a mainnet address. The demo wallet only pays on signet (tsp1…).");
    const shop = await shopByAddress(text);
    return { kind: "address", address: text, name: shop?.name ?? null, base: shop?.url ?? null };
  }
  let url: URL;
  try { url = new URL(text); } catch {
    throw new Error("Paste a shop link (http://…) or a silent-payment address (tsp1…).");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("A shop link starts with http:// or https://.");
  const info = await api<{ address: string; shop: string; network: string }>(url.origin, "/api/info");
  if (info.network !== "signet") throw new Error(`This shop is on ${info.network}; the demo wallet is on signet.`);
  const invoice = url.pathname.match(/\/pay\/([\w-]+)/);
  if (invoice) {
    const inv = await api<{ id: string; address: string; amount_sat: number; memo: string; status: string }>(
      url.origin, `/api/invoices/${invoice[1]}`);
    if (inv.status !== "open") throw new Error(`This invoice is ${inv.status === "seen" ? "already being paid" : inv.status}.`);
    return { kind: "invoice", address: inv.address, name: info.shop, base: url.origin, invoiceId: inv.id,
      amount: inv.amount_sat, memo: inv.memo };
  }
  return { kind: "shop", address: info.address, name: info.shop, base: url.origin };
}

// ---------------------------------------------------------------- rendering

function render(): void {
  const box = $("#payee");
  box.hidden = !payee;
  if (payee) {
    const who = payee.name ?? "A silent-payment address";
    box.replaceChildren(...[
      el("p", {}, el("strong", {}, who)),
      el("p", { class: "small" }, el("code", { title: payee.address }, short(payee.address, 14))),
      payee.kind === "invoice" ? el("p", { class: "small" }, `Invoice: ${sat(payee.amount ?? 0)} for ${payee.memo}`) : null,
      payee.kind === "address" && !payee.name ? el("p", { class: "small muted" }, "Not a shop the marketplace lists, so it can't check this payment.") : null,
    ].filter((n): n is HTMLElement => n !== null));
  }
  const amount = $<HTMLInputElement>("#amount"), memo = $<HTMLInputElement>("#memo");
  amount.disabled = memo.disabled = payee?.kind === "invoice";
  if (payee?.kind === "invoice") {
    amount.value = String(payee.amount);
    memo.value = payee.memo ?? "";
  }
  const pay = $<HTMLButtonElement>("#pay");
  if (pay.getAttribute("aria-busy") !== "true") {
    pay.disabled = !payee;
    pay.textContent = payee ? `Pay ${sat(Number(amount.value) || 0)}${payee.name ? ` to ${payee.name}` : ""}` : "Pay";
  }

  const line = txLine(paid ? tx : null);
  $("#tx-state").dataset.s = paid ? line.s : "none";
  $("#tx-state").textContent = paid ? line.text : "Nothing paid yet";
  $("#paid").hidden = $("#receipt-actions").hidden = $("#raw").hidden = !paid;
  if (paid) {
    $("#r-amount").textContent = sat(paid.amount);
    $("#r-to").textContent = paid.payee.name ?? short(paid.payee.address, 12);
    $("#r-memo").textContent = paid.memo;
    $("#txid").textContent = short(paid.txid, 12);
    $<HTMLAnchorElement>("#tx-link").href = EXPLORER_TX + paid.txid;
    $("#r-shop").textContent = paid.shopTold;
    $("#receipt-json").textContent = JSON.stringify(paid.receipt, null, 2);
  }

  const v = check?.verdict;
  $("#verdict").replaceChildren(...(!v ? [] : v.ok ? [stamp(true, "Valid", `The marketplace checked it: ${v.reason}.`)]
    : v.code === "unconfirmed" ? [stamp("wait", "Wait", "The receipt is good, but the payment isn't in a block yet. This page asks again once it is.")]
    : [stamp(false, "Rejected", v.reason, `Code: ${v.code}`)]));
  $("#check").textContent = check ? "Ask the marketplace again" : "Ask the marketplace to check it";
}

// ---------------------------------------------------------------- actions

async function refreshWallet(): Promise<void> {
  const w = await api<{ balance_sat: number }>(API.wallet, "/api/wallet");
  $("#w-balance").textContent = sat(w.balance_sat);
  $("#w-empty").hidden = w.balance_sat > 1000;
}

async function askMarketplace(): Promise<void> {
  if (!paid) return;
  check = await api<Check>(API.market, "/api/check", { receipt: paid.receipt });
}

async function tellShop(p: Payee, txid: string, receipt: object): Promise<string> {
  if (!p.base) return "No: this address isn't a shop the marketplace lists. A shop's scanner still finds the payment in the next block.";
  try {
    if (p.kind === "invoice") {
      await api(p.base, `/api/invoices/${p.invoiceId}/tx`, { txid });
      return `Yes: ${p.name} saw the payment for the invoice`;
    }
    await api(p.base, "/api/payments/report", { txid, receipt });
    return `Yes: ${p.name} got the transaction and the receipt`;
  } catch (e) {
    return `Not yet (${message(e)}). The shop's scanner will still find it in the next block.`;
  }
}

async function lookUp(): Promise<void> {
  const input = $<HTMLInputElement>("#to");
  input.removeAttribute("aria-invalid");
  try {
    payee = await resolve(input.value);
  } catch (e) {
    payee = null;
    input.setAttribute("aria-invalid", "true");
    throw e;
  }
}

$<HTMLButtonElement>("#lookup").addEventListener("click", (e) => busy(e.currentTarget as HTMLButtonElement, lookUp, render));
$<HTMLButtonElement>("#use-dana").addEventListener("click", (e) => {
  $<HTMLInputElement>("#to").value = API.sppay;
  busy(e.currentTarget as HTMLButtonElement, lookUp, render);
});
$("#to").addEventListener("keydown", (e) => {
  if ((e as KeyboardEvent).key === "Enter") { e.preventDefault(); $<HTMLButtonElement>("#lookup").click(); }
});
$("#amount").addEventListener("input", render);

$<HTMLFormElement>("#pay-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const button = $<HTMLButtonElement>("#pay");
  busy(button, async () => {
    if (!payee) return;
    const amount = Number($<HTMLInputElement>("#amount").value);
    const memo = $<HTMLInputElement>("#memo").value.trim();
    if (!Number.isInteger(amount) || amount < 330) throw new Error("Pay at least 330 sat, the smallest taproot output the network relays.");
    if (!memo) throw new Error("Say what the payment is for: it goes into the receipt.");
    const to = payee;
    const r = await api<{ txid: string; receipt: object }>(API.wallet, "/api/send", { address: to.address, amount_sat: amount, memo });
    paid = { txid: r.txid, receipt: r.receipt, amount, memo, payee: to, shopTold: "…" };
    tx = { state: "mempool", block_height: null, confirmations: 0 };
    check = null;
    save();
    render();
    if (to.kind === "invoice") payee = null;  // an invoice is paid once; look up the next one
    paid.shopTold = await tellShop(to, r.txid, r.receipt);
    save();
    await askMarketplace();
    await refreshWallet();
  }, render);
});

$<HTMLButtonElement>("#check").addEventListener("click", (e) => busy(e.currentTarget as HTMLButtonElement, askMarketplace, render));
$("#download").addEventListener("click", () => paid && download(`receipt-${paid.txid.slice(0, 12)}.json`, paid.receipt));

async function poll(): Promise<void> {
  try {
    if (paid) {
      const before = tx?.state;
      tx = await api<TxStatus>(API.wallet, `/api/tx/${paid.txid}`);
      // Asked while it was in the mempool: ask again now that it is in a block.
      if (before !== "confirmed" && tx.state === "confirmed" && (!check || check.verdict.code === "unconfirmed")) await askMarketplace();
    }
    render();
  } catch (e) {
    showError(message(e));
  }
  setTimeout(poll, POLL_MS);
}

refreshWallet().catch((e) => showError(message(e)));
showTip();
render();
poll();
