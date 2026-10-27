import type { Metadata } from "next";
import { StockView } from "@/components/StockView";
import type { Timeframe } from "@/lib/types";

const TIMEFRAMES: Timeframe[] = ["1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w"];

export async function generateMetadata({ params }: PageProps<"/stock/[symbol]">): Promise<Metadata> {
  const { symbol } = await params;
  return { title: `${symbol.toUpperCase()} — AI Stock` };
}

export default async function StockPage({ params, searchParams }: PageProps<"/stock/[symbol]">) {
  const { symbol } = await params;
  const { tf } = await searchParams;
  const initial = TIMEFRAMES.find((t) => t === tf) ?? "1m";
  return <StockView symbol={symbol.toUpperCase()} initialTimeframe={initial} />;
}
