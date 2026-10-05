export function Disclaimer() {
  return (
    <footer className="mx-auto mt-10 max-w-[1400px] border-t border-line px-4 py-6 text-xs leading-relaxed text-faint sm:px-6">
      <p>
        <strong className="text-muted">Not investment advice.</strong> Signals are rule-based technical scores computed from completed candles. They are not
        validated predictions and carry no guarantee of returns. Outcome probabilities will be shown only after horizon-specific models pass out-of-sample
        testing. Entry, target and stop-loss levels are volatility-based scenarios. Market data: Upstox Developer API (subject to your account&apos;s
        entitlements and Upstox terms). No trades are placed by this application.
      </p>
    </footer>
  );
}
