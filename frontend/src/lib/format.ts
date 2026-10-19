const num = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });

export const fmtPrice = (v: number | null | undefined) => (v == null ? "—" : num.format(v));
const byDigits: Record<number, Intl.NumberFormat> = { 2: num };
/** A price with `dp` decimals (4 for currency pairs, which move in steps of 0.0025). */
export function fmtPx(v: number | null | undefined, dp = 2) {
  if (v == null) return "—";
  byDigits[dp] ??= new Intl.NumberFormat("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  return byDigits[dp].format(v);
}
export const fmtCompact = (v: number | null | undefined) => (v == null ? "—" : compact.format(v));
export const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
export const fmtSigned = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${num.format(v)}`);
export const fmtNum = (v: number | null | undefined, digits = 1) => (v == null ? "—" : v.toFixed(digits));

export function fmtTime(iso: string | null | undefined, withDate = false) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: withDate ? undefined : "2-digit",
    day: withDate ? "2-digit" : undefined,
    month: withDate ? "short" : undefined,
    hour12: false,
  });
}

export const changeColor = (v: number | null | undefined) =>
  v == null || v === 0 ? "text-muted" : v > 0 ? "text-bull" : "text-bear";

export function fmtDate(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric" });
}
