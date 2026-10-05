// Dana's side: her one address, and the payments her scanner found, with the receipts buyers sent.

import qrcode from "qrcode-generator";
import { $, API, EXPLORER_TX, api, el, message, sat, short, showError, showTip, type Verdict } from "./services";

const POLL_MS = 10_000;

interface Info { address: string; network: string; shop: string; scanned_height: number }
interface PaymentRow {
  txid: string; vout: number; amount_sat: number; block_height: number | null; invoice_id: string | null;
  receipt: { memo: string } | null; receipt_check: Verdict | null;
}
interface Invoice { memo: string; receipt_check: Verdict | null }

let info: Info | null = null;

function showInfo(i: Info): void {
  $("#s-name").textContent = i.shop;
  $("#s-network").textContent = i.network === "signet" ? "Public signet" : i.network;
  $("#s-address").textContent = short(i.address, 16);
  $("#s-address").title = i.address;
  $("#s-link").textContent = API.sppay;
  const qr = qrcode(0, "M");
  qr.addData(i.address.toUpperCase(), "Alphanumeric");
  qr.make();
  $("#s-qr").innerHTML = qr.createSvgTag({ cellSize: 3, margin: 0, scalable: true });
}

function receiptLine(check: Verdict | null): HTMLElement {
  if (!check) return el("span", { class: "meta" }, "No receipt sent");
  if (check.ok) return el("span", { class: "ok" }, "Receipt checks out");
  if (check.code === "unconfirmed") return el("span", { class: "meta" }, "Receipt is good, waiting for a block");
  return el("span", { class: "bad" }, `Receipt rejected: ${check.reason}`);
}

async function refresh(): Promise<void> {
  if (!info) {
    info = await api<Info>(API.sppay, "/api/info");
    showInfo(info);
  }
  const [rows, now] = await Promise.all([
    api<PaymentRow[]>(API.sppay, "/api/payments"), api<Info>(API.sppay, "/api/info"),
  ]);
  $("#p-scan").textContent = `Scanned up to block ${now.scanned_height.toLocaleString("en-US")}. Checks every 10 seconds.`;

  // One transaction can pay the shop in more than one output: show it once.
  const byTx = new Map<string, PaymentRow[]>();
  for (const r of rows) byTx.set(r.txid, [...(byTx.get(r.txid) ?? []), r]);
  const items = await Promise.all([...byTx.values()].map(async (outs) => {
    const p = outs[0];
    let memo = p.receipt?.memo ?? null, check = p.receipt_check;
    if (p.invoice_id) {
      const inv = await api<Invoice>(API.sppay, `/api/invoices/${p.invoice_id}`);
      memo = inv.memo;
      check = inv.receipt_check;
    }
    const total = outs.reduce((n, o) => n + o.amount_sat, 0);
    const where = p.block_height === null ? "In the mempool" : `In block ${p.block_height.toLocaleString("en-US")}`;
    return el("li", {},
      el("span", { class: "amount" }, sat(total)),
      el("span", {}, memo ? `For: ${memo}` : "For: unknown (no receipt or invoice)"),
      el("span", { class: "meta" }, `${where}${p.invoice_id ? ", paid an invoice" : ", paid to the address directly"}`),
      receiptLine(check),
      el("a", { class: "small", href: EXPLORER_TX + p.txid, target: "_blank", rel: "noopener" }, short(p.txid, 10)));
  }));
  $("#payments").replaceChildren(...items);
  $("#p-empty").hidden = items.length > 0;
}

document.querySelectorAll<HTMLButtonElement>("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
  if (!info) return;
  const text = b.dataset.copy === "address" ? info.address : API.sppay;
  try {
    await navigator.clipboard.writeText(text);
    b.textContent = "Copied";
  } catch {
    b.textContent = "Can't copy";
  }
  setTimeout(() => { b.textContent = "Copy"; }, 1500);
}));

async function poll(): Promise<void> {
  try {
    await refresh();
    showError(null);
  } catch (e) {
    showError(message(e));
  }
  setTimeout(poll, POLL_MS);
}

showTip();
poll();
