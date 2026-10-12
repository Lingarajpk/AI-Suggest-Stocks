import type { Metadata } from "next";
import { WatchlistView } from "@/components/WatchlistView";

export const metadata: Metadata = { title: "Watchlist — AI Stock" };

export default function WatchlistPage() {
  return <WatchlistView />;
}
