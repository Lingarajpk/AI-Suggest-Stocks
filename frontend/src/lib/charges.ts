/**
 * Approximate Upstox equity charges (NSE) for one round trip. Rates change: check
 * https://upstox.com/brokerage-charges/ and update RATES if they differ.
 */
export const RATES = {
  intraday: { brokeragePct: 0.001, brokerageCap: 20, sttSellPct: 0.00025, sttBuyPct: 0, stampBuyPct: 0.00003, dp: 0 },
  delivery: { brokeragePct: 0.025, brokerageCap: 20, sttSellPct: 0.001, sttBuyPct: 0.001, stampBuyPct: 0.00015, dp: 18.5 },
  // Index/stock options (buying): flat ₹20 per order, STT 0.1% of the sell premium, NSE options transaction 0.03503%.
  option: { brokeragePct: 1, brokerageCap: 20, sttSellPct: 0.001, sttBuyPct: 0, stampBuyPct: 0.00003, dp: 0, exchangePct: 0.0003503 },
  exchangePct: 0.0000297, // NSE transaction charge
  sebiPct: 0.000001, // ₹10 per crore
  gstPct: 0.18, // on brokerage + exchange + SEBI (+ DP)
};

export type Product = "intraday" | "delivery" | "option";

export interface Charges {
  brokerage: number;
  stt: number;
  exchange: number;
  sebi: number;
  stamp: number;
  gst: number;
  dp: number;
  total: number;
}

/** Charges for buying `qty` at `buy` and selling at `sell` (the order doesn't matter for the maths). */
export function roundTrip(product: Product, qty: number, buy: number, sell: number): Charges {
  const r: (typeof RATES)["intraday"] & { exchangePct?: number } = RATES[product];
  const buyValue = qty * buy;
  const sellValue = qty * sell;
  const turnover = buyValue + sellValue;
  const order = (v: number) => (v > 0 ? Math.min(r.brokerageCap, v * r.brokeragePct) : 0);
  const brokerage = order(buyValue) + order(sellValue);
  const stt = sellValue * r.sttSellPct + buyValue * r.sttBuyPct;
  const exchange = turnover * (r.exchangePct ?? RATES.exchangePct);
  const sebi = turnover * RATES.sebiPct;
  const stamp = buyValue * r.stampBuyPct;
  const dp = qty > 0 ? r.dp : 0;
  const gst = (brokerage + exchange + sebi + dp) * RATES.gstPct;
  return { brokerage, stt, exchange, sebi, stamp, gst, dp, total: brokerage + stt + exchange + sebi + stamp + gst + dp };
}

export interface Plan {
  qty: number;
  limitedBy: "risk" | "capital";
  invested: number;
  riskPerShare: number;
  riskRs: number;
  rewardRs: number;
  rr: number | null;
  atTarget: { gross: number; charges: Charges; net: number };
  atStop: { gross: number; charges: Charges; net: number };
  breakeven: number;
}

/** Quantity that risks `riskPct` of `capital` if the stop is hit, capped by what the capital can buy. */
export function plan(opts: { capital: number; riskPct: number; side: "BUY" | "SELL"; entry: number; stop: number; target: number; product: Product }): Plan | null {
  const { capital, riskPct, side, entry, stop, target, product } = opts;
  if (!(capital > 0 && riskPct > 0 && entry > 0 && stop > 0 && target > 0)) return null;
  const long = side === "BUY";
  const riskPerShare = long ? entry - stop : stop - entry;
  if (riskPerShare <= 0) return null;
  const byRisk = Math.floor((capital * riskPct) / 100 / riskPerShare);
  const byCapital = Math.floor(capital / entry);
  const qty = Math.max(0, Math.min(byRisk, byCapital));
  const outcome = (exit: number) => {
    const gross = qty * (long ? exit - entry : entry - exit);
    const charges = long ? roundTrip(product, qty, entry, exit) : roundTrip(product, qty, exit, entry);
    return { gross, charges, net: gross - charges.total };
  };
  const reward = long ? target - entry : entry - target;
  const atTarget = outcome(target);
  const perShareCost = qty > 0 ? atTarget.charges.total / qty : 0;
  return {
    qty,
    limitedBy: byRisk <= byCapital ? "risk" : "capital",
    invested: qty * entry,
    riskPerShare,
    riskRs: qty * riskPerShare,
    rewardRs: qty * reward,
    rr: riskPerShare > 0 && reward > 0 ? reward / riskPerShare : null,
    atTarget,
    atStop: outcome(stop),
    breakeven: long ? entry + perShareCost : entry - perShareCost,
  };
}
