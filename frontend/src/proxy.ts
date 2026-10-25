import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/** Pages need a login: without the session cookie, go to /login and come back afterwards.
 * Only the cookie's presence is checked here; the backend verifies it on every /api call, and a
 * 401 there (expired or forged cookie) also sends the browser to /login. */
export function proxy(request: NextRequest) {
  if (request.cookies.has("ais_session")) return NextResponse.next();
  const url = request.nextUrl.clone();
  const next = request.nextUrl.pathname + request.nextUrl.search;
  url.pathname = "/login";
  url.search = next && next !== "/" ? `?next=${encodeURIComponent(next)}` : "";
  return NextResponse.redirect(url);
}

export const config = {
  // Everything except the API proxy, the login page, Next's assets and public files.
  matcher: ["/((?!api|login|_next/static|_next/image|favicon.ico|manifest.webmanifest|sw.js|.*\\.(?:png|svg|ico|webp|jpg)$).*)"],
};
