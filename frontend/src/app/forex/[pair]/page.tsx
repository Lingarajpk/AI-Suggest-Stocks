import type { Metadata } from "next";
import { ForexPairView } from "@/components/forex/ForexPairView";
import type { Timeframe } from "@/lib/types";

const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "30m", "1h", "1d", "1w"];
const clean = (p: string) => p.toUpperCase().replace(/[^A-Z]/g, "");

export async function generateMetadata({ params }: PageProps<"/forex/[pair]">): Promise<Metadata> {
  const { pair } = await params;
  const p = clean(pair);
  return { title: `${p.slice(0, 3)}/${p.slice(3)} — AI Stock Forex` };
}

export default async function ForexPairPage({ params, searchParams }: PageProps<"/forex/[pair]">) {
  const { pair } = await params;
  const { tf } = await searchParams;
  const initial = TIMEFRAMES.find((t) => t === tf) ?? "15m";
  return <ForexPairView pair={clean(pair)} initialTimeframe={initial} />;
}
