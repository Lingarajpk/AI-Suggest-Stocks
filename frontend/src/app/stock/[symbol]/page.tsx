import type { Metadata } from "next";
import { StockView } from "@/components/StockView";

export async function generateMetadata({ params }: PageProps<"/stock/[symbol]">): Promise<Metadata> {
  const { symbol } = await params;
  return { title: `${symbol.toUpperCase()} — AI Stock` };
}

export default async function StockPage({ params }: PageProps<"/stock/[symbol]">) {
  const { symbol } = await params;
  return <StockView symbol={symbol.toUpperCase()} />;
}
