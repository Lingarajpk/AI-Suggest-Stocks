import type { MetadataRoute } from "next";

/** Lets the site be installed ("Add to Home Screen"), which iPhone requires for push notifications. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "AI Stock — Market Intelligence",
    short_name: "AI Stock",
    description: "Intraday BUY/SELL calls and alerts for NSE stocks and indices",
    start_url: "/",
    display: "standalone",
    background_color: "#0b1220",
    theme_color: "#0b1220",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
