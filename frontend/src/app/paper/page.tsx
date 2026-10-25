import type { Metadata } from "next";
import { PaperTrading } from "@/components/PaperTrading";

export const metadata: Metadata = { title: "Paper trading — AI Stock" };

export default function PaperPage() {
  return <PaperTrading />;
}
