import qrcode from "qrcode-generator";
import { call, loadPython, PyError } from "./py";

// ---------------------------------------------------------------- types from webdemo/story.py

interface Invoice {
  order: number; memo: string; item: string; amount_sat: number; address: string; txid: string | null;
  math: { B_scan: string; B_m: string };
}
interface TxOutView { vout: number; amount_sat: number; address: string; type: string; key: string }
interface TxView {
  txid: string; height: number | null; confirmations: number;
  inputs: { outpoint: string; amount_sat: number; address: string }[];
  outputs: TxOutView[]; raw_hex: string;
}
interface Check { key: string; label: string; status: "pass" | "fail" | "skip" | "unchecked" }
interface VerifyResult {
  valid: boolean; reason: string; role: string; memo: string; txid: string; address: string;
  confirmations: number | null; checks: Check[]; total_sat: number; warnings: string[];
  paid_outputs: { vout: number; k: number; amount_sat: number; key: string }[];
}
type Receipt = { memo: string; txid: string; address: string; shares: { share: string; proof: string }[]; bundle?: unknown };
interface State { shop_name: string; height: number }

// ---------------------------------------------------------------- small helpers

const $ = <T extends HTMLElement = HTMLElement>(sel: string) => document.querySelector(sel) as T;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

type Child = Node | string | null | undefined | false;
function h(tag: string, attrs: Record<string, string> = {}, ...children: Child[]): HTMLElement {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  for (const c of children) if (c) el.append(c);
  return el;
}

function btc(sat: number): string {
  const s = (sat / 1e8).toFixed(8).replace(/0+$/, "");
  const [whole, frac = ""] = s.split(".");
  return `${whole}.${frac.padEnd(2, "0")} BTC`;
}

function short(s: string, keep = 8): string {
  return s.length <= keep * 2 + 1 ? s : `${s.slice(0, keep)}…${s.slice(-keep)}`;
}

function copyButton(text: string, label = "Copy"): HTMLElement {
  const b = h("button", { type: "button", class: "copy" }, label);
  b.addEventListener("click", async () => {
    await navigator.clipboard.writeText(text);
    b.textContent = "Copied";
    setTimeout(() => (b.textContent = label), 1500);
  });
  return b;
}

function math(title: string, ...rows: [string, string][]): HTMLElement {
  return h("div", { class: "math" },
    h("p", { class: "math-title" }, title),
    h("dl", {}, ...rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, h("code", {}, v))])));
}

function announce(text: string) {
  $("#announce").textContent = text;
}

/** Plain-language version of a verify failure; the exact reason stays in the math view. */
function plain(res: VerifyResult): string {
  const r = res.reason;
  if (res.valid) return "";
  if (r.includes("DLEQ")) return "The proof doesn't match this order, this address or the coins that paid.";
  if (r.includes("not on this chain")) return "That transaction isn't on this chain.";
  if (r.includes("does not pay")) return "The transaction doesn't pay this address.";
  if (r.includes("confirmations")) return "The payment isn't confirmed yet.";
  if (r.startsWith("not a valid receipt")) return "This file isn't a valid receipt.";
  return r.charAt(0).toUpperCase() + r.slice(1) + ".";
}

// ---------------------------------------------------------------- state

let state: State = { shop_name: "Dana's Phone Shop", height: 0 };
let invoice: Invoice | null = null;
let payment: TxView | null = null;
let receipt: Receipt | null = null;

const STEPS = ["invoice", "pay", "chain", "receipt", "verify", "cheat"];

function setStep(name: string, s: "locked" | "active" | "done") {
  const el = $(`#step-${name}`);
  el.dataset.state = s;
  el.querySelectorAll<HTMLButtonElement | HTMLInputElement>("button.act, input[type=file]")
    .forEach((b) => (b.disabled = s === "locked" || (s === "done" && b.dataset.once === "1")));
}

function advance(done: string) {
  const i = STEPS.indexOf(done);
  setStep(done, "done");
  if (i + 1 < STEPS.length && $(`#step-${STEPS[i + 1]}`).dataset.state === "locked") setStep(STEPS[i + 1], "active");
}

function refreshChain() {
  state = call<State>("state");
  $("#chain-label").textContent = `Simulated chain · block ${state.height}`;
}

// ---------------------------------------------------------------- renderers

function slip(kind: "invoice" | "receipt", title: string, rows: [string, Child][], ...extra: Child[]): HTMLElement {
  return h("div", { class: `slip slip-${kind}` },
    h("p", { class: "slip-title" }, title),
    h("dl", {}, ...rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
    ...extra);
}

function renderInvoice(inv: Invoice) {
  const qr = qrcode(0, "M");
  qr.addData(inv.address.toUpperCase(), "Alphanumeric");
  qr.make();
  const qrBox = h("div", { class: "qr", role: "img", "aria-label": "QR code of the payment address" });
  qrBox.innerHTML = qr.createSvgTag({ cellSize: 3, margin: 0, scalable: true });
  const out = $("#out-invoice");
  out.replaceChildren(slip("invoice", `Invoice from ${state.shop_name}`, [
    ["Order", inv.memo],
    ["Item", inv.item],
    ["Amount", h("strong", {}, btc(inv.amount_sat))],
    ["Pay to", h("span", { class: "addr" }, short(inv.address, 12), " ", copyButton(inv.address))],
  ], qrBox, h("p", { class: "slip-note" }, "Dana uses this one address for every order.")),
  math("Inside the address (BIP-352)",
    ["Scan key B_scan", inv.math.B_scan],
    ["Spend key B_spend", inv.math.B_m]));
  $<HTMLButtonElement>("[data-action=pay]").textContent = `Pay ${btc(inv.amount_sat)}`;
}

function renderPay(tx: TxView, inv: Invoice) {
  $("#out-pay").replaceChildren(
    h("p", { class: "done-line" }, h("strong", {}, `Paid ${btc(inv.amount_sat)}.`),
      ` Confirmed in block ${tx.height}.`),
    h("p", { class: "muted" }, "Transaction ", h("code", {}, short(tx.txid, 10)), " ", copyButton(tx.txid, "Copy ID")),
    math("The transaction", ["txid", tx.txid], ["Raw transaction (hex)", tx.raw_hex]));
}

function renderChain(tx: TxView) {
  const outRow = (o: TxOutView) => {
    const mine = o.type !== "taproot";
    return h("li", { class: `ledger-row ${mine ? "change" : "payee"}`, id: `ledger-out-${o.vout}` },
      h("span", { class: "amt" }, btc(o.amount_sat)),
      h("span", { class: "who" }, h("code", {}, short(o.address, 10)),
        h("span", { class: "tag" }, mine ? "Change back to you. Only you know that." : "Who is this? Nobody can tell.")));
  };
  $("#out-chain").replaceChildren(
    h("div", { class: "ledger", "aria-label": "The transaction as anyone sees it" },
      h("div", { class: "ledger-side" }, h("p", { class: "ledger-head" }, "Coins spent"),
        h("ul", {}, ...tx.inputs.map((i) => h("li", { class: "ledger-row" },
          h("span", { class: "amt" }, btc(i.amount_sat)), h("span", { class: "who" }, h("code", {}, short(i.address, 10))))))),
      h("div", { class: "ledger-arrow", "aria-hidden": "true" }),
      h("div", { class: "ledger-side" }, h("p", { class: "ledger-head" }, "Paid to"),
        h("ul", {}, ...tx.outputs.map(outRow)))),
    math("Why nobody can tell", ["Output key P₀ = B_m + hash(S ‖ 0)·G", tx.outputs[0].key],
      ["S, the shared secret", "Known only to you (from your input keys) and the shop (from its scan key)"]));
}

function renderDispute(inv: Invoice) {
  const scan = call<{ found: { vout: number; amount_sat: number }[] }>("shop_scan", { order: inv.order });
  $("#out-chain").append(
    h("blockquote", { class: "bubble" },
      h("p", {}, `“We never received payment for ${inv.memo}.”`),
      h("cite", {}, state.shop_name)),
    h("p", { class: "muted small" }, "In this story the shop is disputing the payment. Its own wallet can find it, but you can't see that wallet, and pointing at a random key on chain proves nothing."),
    math("The shop's wallet scan (private to the shop)",
      ["Found", scan.found.map((f) => `output ${f.vout}: ${btc(f.amount_sat)}`).join(", ") || "nothing"]));
}

function renderReceipt(r: Receipt, jsonBytes: number) {
  const blob = new Blob([JSON.stringify(r, null, 2)], { type: "application/json" });
  const download = h("a", { class: "button", href: URL.createObjectURL(blob), download: `receipt-${r.memo.replace(/\W+/g, "-").toLowerCase()}.json` }, "Download receipt.json");
  const { bundle: _bundle, ...core } = r;
  $("#out-receipt").replaceChildren(
    slip("receipt", "Proof of payment", [
      ["For", r.memo],
      ["Shop", state.shop_name],
      ["Amount", h("strong", {}, btc(invoice!.amount_sat))],
      ["Transaction", h("code", {}, short(r.txid, 8))],
      ["Proof", "97 bytes, no private keys"],
    ], h("div", { class: "slip-actions" }, download)),
    h("p", { class: "muted small" }, "Share it only with whoever needs to check it: it reveals your shared secret with this shop."),
    math("What's in the receipt",
      ["Share a·B_scan (33 bytes)", r.shares[0].share],
      ["BIP-374 proof (64 bytes)", r.shares[0].proof],
      ["Signed message", `hash(role ‖ txid ‖ address ‖ sha256("${r.memo}"))`],
      [`Receipt JSON without the bundled transactions (${jsonBytes} bytes)`, JSON.stringify(core)]));
}

function checklist(res: VerifyResult): HTMLElement {
  const icon = { pass: "✓", fail: "✗", skip: "–", unchecked: "?" } as const;
  return h("ol", { class: "checks" }, ...res.checks.map((c, i) => {
    const li = h("li", { class: `check ${c.status}` }, h("span", { class: "check-icon", "aria-hidden": "true" }, icon[c.status]),
      h("span", {}, c.label, c.status === "unchecked" ? " (not checked: offline)" : ""));
    if (!reduceMotion) li.style.animationDelay = `${i * 110}ms`;
    return li;
  }));
}

function verdict(res: VerifyResult): HTMLElement {
  const body = res.valid
    ? h("p", {}, h("strong", {}, `${btc(res.total_sat)} ${res.role === "receiver" ? "received by the shop" : "paid"}`),
        res.role === "receiver" ? `. Statement: ${res.memo}` : ` for ${res.memo}`,
        res.confirmations === null ? "" : `, ${res.confirmations} confirmation${res.confirmations === 1 ? "" : "s"}`, ".")
    : h("p", {}, plain(res));
  return h("div", { class: `verdict ${res.valid ? "ok" : "bad"}`, role: "status" },
    h("p", { class: "stamp" }, res.valid ? "Valid" : "Rejected"), body,
    ...res.warnings.map((w) => h("p", { class: "muted small" }, w.charAt(0).toUpperCase() + w.slice(1) + (w.endsWith(".") ? "" : "."))),
    math("Verifier output", ["Reason", res.reason]));
}

function renderVerify(res: VerifyResult, target: HTMLElement, compact = false) {
  if (compact) {
    // Cheat cards are narrow: show where the check stopped, and the full list only with the math.
    const failed = res.checks.find((c) => c.status === "fail");
    const full = h("div", { class: "math" }, h("p", { class: "math-title" }, "Every check"), checklist(res));
    target.replaceChildren(verdict(res), failed ? h("p", { class: "stopped" }, "Stopped at: ", h("strong", {}, failed.label)) : "", full);
  } else {
    target.replaceChildren(verdict(res), checklist(res));
  }
  announce(res.valid ? `Valid: ${res.memo}` : `Rejected: ${plain(res)}`);
}

// ---------------------------------------------------------------- actions

const actions: Record<string, () => void | Promise<void>> = {
  invoice() {
    invoice = call<Invoice>("create_invoice", { item: "Phone", amount_sat: 30_000_000 });
    renderInvoice(invoice);
    advance("invoice");
  },
  pay() {
    const res = call<{ invoice: Invoice; tx: TxView }>("pay", { order: invoice!.order });
    invoice = res.invoice;
    payment = res.tx;
    refreshChain();
    renderPay(payment, invoice);
    renderChain(payment);
    advance("pay");
  },
  dispute() {
    renderDispute(invoice!);
    $<HTMLButtonElement>("[data-action=dispute]").disabled = true;
    advance("chain");
  },
  receipt() {
    const res = call<{ receipt: Receipt; json_bytes_without_bundle: number }>("make_receipt", { order: invoice!.order });
    receipt = res.receipt;
    renderReceipt(receipt, res.json_bytes_without_bundle);
    advance("receipt");
  },
  verify() {
    const res = call<VerifyResult>("verify", { receipt });
    renderVerify(res, $("#out-verify"));
    if (res.valid) {
      for (const o of res.paid_outputs) {
        const tag = document.querySelector(`#ledger-out-${o.vout} .tag`);
        if (tag) tag.textContent = `${state.shop_name}, ${res.memo}. Proven by the receipt.`;
        document.querySelector(`#ledger-out-${o.vout}`)?.classList.add("proven");
      }
    }
    advance("verify");
  },
  "cheat-reuse"() {
    const res = call<{ changed: string; result: VerifyResult }>("cheat_reuse", { receipt });
    renderVerify(res.result, $("#cheat-reuse .cheat-out"), true);
  },
  "cheat-tamper"() {
    const res = call<{ changed: string; result: VerifyResult }>("cheat_tamper", { receipt });
    renderVerify(res.result, $("#cheat-tamper .cheat-out"), true);
  },
  "cheat-backdoor"() {
    const res = call<{ tweak_check_passes: boolean; shop_wallet_finds_it: boolean; receipt_made: boolean; receipt_error: string | null; tweak: string; tx: TxView }>(
      "cheat_backdoor", { order: invoice!.order });
    refreshChain();
    const row = (label: string, value: string, tone: "good" | "bad") => [h("dt", {}, label), h("dd", { class: tone }, value)];
    $("#cheat-backdoor .cheat-out").replaceChildren(
      h("div", { class: "verdict bad", role: "status" }, h("p", { class: "stamp" }, "Rejected"),
        h("p", {}, "No receipt can be made: the output isn't really the shop's.")),
      h("dl", { class: "compare" },
        ...row("Usual tweak check", res.tweak_check_passes ? "Passes: fooled" : "Fails", res.tweak_check_passes ? "bad" : "good"),
        ...row("Shop's wallet sees the money", res.shop_wallet_finds_it ? "Yes" : "No", res.shop_wallet_finds_it ? "good" : "bad"),
        ...row("Receipt", res.receipt_made ? "Made" : "Can't be made", res.receipt_made ? "bad" : "good")),
      math("The trick", ["Tweak t = r + hash(Q ‖ script)", res.tweak], ["Fake transaction", res.tx.txid],
        ["Receipt code said", res.receipt_error ?? "nothing"]));
    announce("Rejected: no receipt can be made for the hidden-way-back payment.");
  },
  income() {
    const res = call<{ result: VerifyResult }>("receiver_proof", { order: invoice!.order });
    renderVerify(res.result, $("#out-income"));
  },
  sample() {
    const res = call<{ result: VerifyResult }>("verify_sample", { name: "sender-receipt" });
    renderVerify(res.result, $("#out-sample"));
  },
};

async function run(name: string) {
  const button = document.querySelector<HTMLButtonElement>(`[data-action="${name}"]`);
  const out = button?.closest(".step, .cheat, article")?.querySelector<HTMLElement>(".step-out, .cheat-out, .more-out");
  if (button) { button.disabled = true; button.setAttribute("aria-busy", "true"); }
  // Let the busy state paint before Python blocks the main thread (setTimeout, not rAF: rAF stalls in background tabs).
  await new Promise((r) => setTimeout(r, 30));
  try {
    await actions[name]();
  } catch (e) {
    const message = e instanceof PyError ? e.message : String(e);
    out?.append(h("p", { class: "error", role: "alert" }, `Something went wrong: ${message}`));
  } finally {
    if (button) {
      button.removeAttribute("aria-busy");
      const once = ["invoice", "pay", "dispute", "receipt"].includes(name);
      button.dataset.once = once ? "1" : "";
      button.disabled = once;
    }
  }
}

async function verifyFile(file: File) {
  const out = $("#out-verify");
  try {
    const parsed = JSON.parse(await file.text());
    let res = call<VerifyResult>("verify", { receipt: parsed });
    if (!res.valid && res.reason.includes("not on this chain") && parsed.bundle) {
      res = call<VerifyResult>("verify", { receipt: parsed, offline: true });
      res.warnings.unshift("This transaction isn't on this demo chain, so it was checked against the transactions inside the receipt.");
    }
    renderVerify(res, out);
  } catch (e) {
    out.replaceChildren(h("p", { class: "error", role: "alert" }, `Couldn't read that file: ${e instanceof Error ? e.message : e}`));
  }
}

async function autoplay() {
  const steps = ["invoice", "pay", "dispute", "receipt", "verify", "cheat-reuse", "cheat-tamper", "cheat-backdoor"];
  $<HTMLButtonElement>("#autoplay").disabled = true;
  for (const name of steps) {
    const button = document.querySelector<HTMLElement>(`[data-action="${name}"]`);
    if (button && !(button as HTMLButtonElement).disabled) {
      button.closest(".step, .cheat")?.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
      await new Promise((r) => setTimeout(r, 700));
      await run(name);
      await new Promise((r) => setTimeout(r, 1500));
    }
  }
  $<HTMLButtonElement>("#autoplay").disabled = false;
}

function reset() {
  call("reset");
  invoice = payment = null;
  receipt = null;
  refreshChain();
  $("#view-comic").querySelectorAll(".step-out, .cheat-out, .more-out").forEach((el) => el.replaceChildren());
  $("#view-comic").querySelectorAll<HTMLButtonElement>("button.act").forEach((b) => (b.dataset.once = ""));
  STEPS.forEach((s, i) => setStep(s, i === 0 ? "active" : "locked"));
  $<HTMLButtonElement>("[data-action=pay]").textContent = "Pay the invoice";
  $("#more-title").closest("section")!.querySelectorAll<HTMLButtonElement>("button.act")
    .forEach((b) => (b.disabled = b.dataset.action === "income"));
  window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
}

// ---------------------------------------------------------------- start

document.addEventListener("click", (e) => {
  const button = (e.target as HTMLElement).closest<HTMLButtonElement>("button.act");
  if (button?.dataset.action) run(button.dataset.action);
});
$<HTMLInputElement>("#math-toggle").addEventListener("change", (e) =>
  document.body.classList.toggle("math-on", (e.target as HTMLInputElement).checked));
$<HTMLInputElement>("#receipt-file").addEventListener("change", (e) => {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (file) verifyFile(file);
});
$("#autoplay").addEventListener("click", autoplay);
$("#reset").addEventListener("click", reset);

loadPython((m) => ($("#loading").textContent = m))
  .then(() => {
    $("#loading").remove();
    refreshChain();
    reset();
    for (const id of ["#autoplay", "#reset"]) $<HTMLButtonElement>(id).disabled = false;
    // The income proof needs a paid order; enable it once the payment exists.
    new MutationObserver(() => {
      $<HTMLButtonElement>("[data-action=income]").disabled = !invoice?.txid;
    }).observe($("#out-pay"), { childList: true });
    if (new URLSearchParams(location.search).has("autoplay")) autoplay();
  })
  .catch((e) => {
    $("#loading").textContent = `The receipt code didn't load: ${e instanceof Error ? e.message : e}. Check your connection and reload.`;
    $("#loading").classList.add("error");
  });
