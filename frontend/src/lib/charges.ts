/**
 * Approximate Upstox equity charges (NSE) for one round trip. Rates change: check
 * https://upstox.com/brokerage-charges/ and update RATES if they differ.
 */
export const RATES = {
  intraday: { brokeragePct: 0.001, brokerageCap: 20, sttSellPct: 0.00025, sttBuyPct: 0, stampBuyPct: 0.00003, dp: 0 },
  delivery: { brokeragePct: 0.025, brokerageCap: 20, sttSellPct: 0.001, sttBuyPct: 0.001, stampBuyPct: 0.00015, dp: 18.5 },
  // Index/stock options (buying): flat ₹20 per order, STT 0.1% of the sell premium, NSE options transaction 0.03503%.
  option: { brokeragePct: 1, brokerageCap: 20, sttSellPct: 0.001, sttBuyPct: 0, stampBuyPct: 0.00003, dp: 0, exchangePct: 0.0003503 },
  // NSE currency futures: ₹20 or 0.05% per order, no STT, transaction charge 0.00035%, stamp 0.0001% on buys.
  currency: { brokeragePct: 0.0005, brokerageCap: 20, sttSellPct: 0, sttBuyPct: 0, stampBuyPct: 0.000001, dp: 0, exchangePct: 0.0000035 },
  // NSE currency options (buying): ₹20 per order, no STT, transaction charge 0.031% of premium.
  currency_option: { brokeragePct: 1, brokerageCap: 20, sttSellPct: 0, sttBuyPct: 0, stampBuyPct: 0.000001, dp: 0, exchangePct: 0.00031 },
  exchangePct: 0.0000297, // NSE transaction charge
  sebiPct: 0.000001, // ₹10 per crore
  gstPct: 0.18, // on brokerage + exchange + SEBI (+ DP)
};

export type Product = "intraday" | "delivery" | "option" | "currency" | "currency_option";

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

export type SizeLimit = "risk" | "capital" | "volatility" | "kelly";

export interface Plan {
  qty: number;
  limitedBy: SizeLimit;
  /** Each limit's quantity, so the page can say which one decided the size. */
  limits: Partial<Record<SizeLimit, number>>;
  invested: number;
  riskPerShare: number;
  riskRs: number;
  rewardRs: number;
  rr: number | null;
  atTarget: { gross: number; charges: Charges; net: number };
  atStop: { gross: number; charges: Charges; net: number };
  breakeven: number;
}

/** Smallest stop distance worth trading: tighter than this, the spread and normal noise hit it. */
export const MIN_STOP_PCT = 0.05;

/** Quarter-Kelly risk % from a track record, using the LOW end of the win rate (Wilson) so luck isn't sized up.
 * Kelly fraction = W - (1 - W) / R, with R = average win / average loss. Null when there is no record. */
export function kellyRiskPct(winLowPct: number | null | undefined, avgWinPct: number | null | undefined, avgLossPct: number | null | undefined): number | null {
  if (winLowPct == null || !avgWinPct || !avgLossPct) return null;
  const w = winLowPct / 100;
  const r = Math.abs(avgWinPct / avgLossPct);
  if (!(r > 0)) return null;
  return Math.max(0, 0.25 * (w - (1 - w) / r) * 100);
}

/** Quantity that risks `riskPct` of `capital` if the stop is hit, capped by what the capital can buy and,
 * optionally, by a volatility budget (one ATR move costs no more than riskPct) and by quarter-Kelly.
 * The extra caps can only make a position smaller, never larger. */
export function plan(opts: {
  capital: number; riskPct: number; side: "BUY" | "SELL"; entry: number; stop: number; target: number; product: Product;
  atr?: number | null; kellyPct?: number | null;
}): Plan | null {
  const { capital, riskPct, side, entry, stop, target, product, atr, kellyPct } = opts;
  if (!(capital > 0 && riskPct > 0 && entry > 0 && stop > 0 && target > 0)) return null;
  const long = side === "BUY";
  const riskPerShare = long ? entry - stop : stop - entry;
  if (riskPerShare <= 0) return null;
  const limits: Partial<Record<SizeLimit, number>> = {
    risk: Math.floor((capital * riskPct) / 100 / riskPerShare),
    capital: Math.floor(capital / entry),
  };
  if (atr && atr > 0) limits.volatility = Math.floor((capital * riskPct) / 100 / atr);
  if (kellyPct != null) limits.kelly = Math.floor((capital * Math.min(kellyPct, riskPct)) / 100 / riskPerShare);
  const [limitedBy, cap] = (Object.entries(limits) as [SizeLimit, number][]).reduce((a, b) => (b[1] < a[1] ? b : a));
  const qty = Math.max(0, cap);
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
    limitedBy,
    limits,
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
