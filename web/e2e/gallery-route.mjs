// Reject off-origin requests and redirects. A context being deliberately closed
// can dispose a still-pending local fetch; no other fetch failure is ignored.
export async function guardGalleryRequest(route, origin, unexpected, isClosing) {
  const address = new URL(route.request().url());
  if (address.origin !== origin) {
    unexpected.push(address.href);
    return route.abort();
  }
  if (address.pathname === "/api/system/notify/stream" && route.request().method() === "GET") {
    return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": fixture\n\n" });
  }
  try {
    const response = await route.fetch({ maxRedirects: 0 });
    if (response.status() >= 300 && response.status() < 400) {
      unexpected.push(`redirect ${address.pathname}`);
      return route.abort();
    }
    return await route.fulfill({ response });
  } catch (error) {
    if (isClosing() && error instanceof Error && error.message.startsWith("route.fetch: Request context disposed."))
      return;
    throw error;
  }
}
