"use client";

import { createContext, useCallback, useContext } from "react";
import { fmtPx } from "./format";

/** Price decimals for everything inside: 2 for stocks (the default), 4 for currency pairs. */
export const PriceDigits = createContext(2);

/** A price formatter that follows the surrounding PriceDigits. */
export function usePx() {
  const dp = useContext(PriceDigits);
  return useCallback((v: number | null | undefined) => fmtPx(v, dp), [dp]);
}
