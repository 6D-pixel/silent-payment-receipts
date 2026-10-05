// Shared by the signet pages: where the services are, calling them, and small DOM helpers.

export const API = {
  wallet: import.meta.env.VITE_WALLET_URL ?? "http://127.0.0.1:8403",
  market: import.meta.env.VITE_MARKET_URL ?? "http://127.0.0.1:8402",
  sppay: import.meta.env.VITE_SPPAY_URL ?? "http://127.0.0.1:8401",
  chain: "https://mempool.space/signet/api",
};
export const EXPLORER_TX = "https://mempool.space/signet/tx/";

export interface Verdict { ok: boolean; code: string; reason: string; amount_sat: number }
export interface TxStatus { state: "mempool" | "confirmed" | "not_found"; block_height: number | null; confirmations: number }

export class ApiError extends Error {}

export async function api<T>(base: string, path: string, body?: object): Promise<T> {
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
    let detail: unknown = text;
    try { detail = JSON.parse(text).detail ?? text; } catch { /* plain text */ }
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg ?? JSON.stringify(d)).join("; ");  // pydantic
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return JSON.parse(text) as T;
}

export const $ = <T extends HTMLElement = HTMLElement>(sel: string) => document.querySelector(sel) as T;
export const sat = (n: number) => `${n.toLocaleString("en-US")} sat`;
export const short = (s: string, n = 10) => (s.length > 2 * n + 1 ? `${s.slice(0, n)}…${s.slice(-n)}` : s);
export const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function el(tag: string, attrs: Record<string, string> = {}, ...children: (Node | string | null | false)[]): HTMLElement {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  for (const c of children) if (c) node.append(c);
  return node;
}

/** The comic's stamp: a burst with one word, then the reason. */
export function stamp(tone: boolean | "wait", word: string, reason: string, extra?: string): HTMLElement {
  const cls = tone === "wait" ? "wait" : tone ? "ok" : "bad";
  return el("div", { class: `verdict ${cls}`, role: "status" },
    el("p", { class: "stamp" }, word), el("p", {}, reason), extra ? el("p", { class: "small muted" }, extra) : null);
}

export function showError(text: string | null): void {
  const box = $("#offline");
  box.hidden = !text;
  box.textContent = text ?? "";
}

/** Run a button's work with the busy look, and show any failure at the top of the page. */
export async function busy(button: HTMLButtonElement, work: () => Promise<void>, after: () => void): Promise<void> {
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await work();
    showError(null);
  } catch (e) {
    showError(message(e));
  } finally {
    button.removeAttribute("aria-busy");
    button.disabled = false;
    after();
  }
}

export function txLine(t: TxStatus | null): { s: string; text: string } {
  if (!t) return { s: "none", text: "Not paid yet" };
  if (t.state === "mempool") return { s: "mempool", text: "In the mempool, waiting for a block (about 10 minutes)" };
  if (t.state === "confirmed") {
    return { s: "confirmed", text: `Confirmed in block ${t.block_height?.toLocaleString("en-US")} (${t.confirmations} confirmation${t.confirmations === 1 ? "" : "s"})` };
  }
  return { s: "not_found", text: "Not found on the chain" };
}

export function download(name: string, data: object): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const a = el("a", { href: url, download: name }) as HTMLAnchorElement;
  a.click();
  URL.revokeObjectURL(url);
}

export async function showTip(): Promise<void> {
  try {
    const t = await fetch(`${API.chain}/blocks/tip/height`);
    if (t.ok) $("#tip").textContent = `Public signet: block ${Number(await t.text()).toLocaleString("en-US")}`;
  } catch { /* the tip is only shown */ }
}
