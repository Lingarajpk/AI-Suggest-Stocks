import type { Metadata } from "next";
import { MarketBoard } from "@/components/MarketBoard";

export const metadata: Metadata = { title: "Market board — AI Stock" };

export default function MarketPage() {
  return <MarketBoard />;
}
