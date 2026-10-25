import type { Metadata } from "next";
import { StrategyLab } from "@/components/StrategyLab";

export const metadata: Metadata = { title: "Strategy lab — AI Stock" };

export default function LabPage() {
  return <StrategyLab />;
}
