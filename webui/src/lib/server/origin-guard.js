function firstHeaderValue(value) {
  return value?.split(",", 1)[0]?.trim() || "";
}

function normalizedProtocol(value) {
  return firstHeaderValue(value).replace(/:$/, "").toLowerCase();
}

/**
 * Decide whether a request came from a different browser origin.
 *
 * `request.nextUrl.origin` can describe the server's internal listener rather
 * than the origin visible to the browser. Compare against the forwarded/Host
 * headers instead, after using Fetch Metadata when the browser supplied it.
 */
export function isCrossOriginRequest({
  secFetchSite,
  origin,
  host,
  forwardedHost,
  protocol,
  forwardedProto,
}) {
  const site = firstHeaderValue(secFetchSite).toLowerCase();
  if (site === "same-origin") return false;
  if (site === "same-site" || site === "cross-site") return true;
  if (site && site !== "none") return true;

  const rawOrigin = origin?.trim();
  if (!rawOrigin) return false;

  let parsedOrigin;
  try {
    parsedOrigin = new URL(rawOrigin);
  } catch {
    return true;
  }

  // An Origin header is a serialized origin, never a full URL with a path,
  // credentials, query, or fragment. This also rejects opaque "null" origins.
  if (parsedOrigin.origin === "null" || parsedOrigin.origin !== rawOrigin) {
    return true;
  }

  const externalHost = (
    firstHeaderValue(forwardedHost) || firstHeaderValue(host)
  ).toLowerCase();
  const externalProtocol =
    normalizedProtocol(forwardedProto) || normalizedProtocol(protocol);
  if (!externalHost || !externalProtocol) return true;

  return (
    parsedOrigin.host.toLowerCase() !== externalHost ||
    parsedOrigin.protocol !== `${externalProtocol}:`
  );
}
