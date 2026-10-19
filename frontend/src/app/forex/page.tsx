import type { Metadata } from "next";
import { ForexDesk } from "@/components/forex/ForexDesk";

export const metadata: Metadata = { title: "Forex — AI Stock" };

export default function ForexPage() {
  return <ForexDesk />;
}
