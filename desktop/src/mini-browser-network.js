import { randomBytes, timingSafeEqual } from "node:crypto";
import { Resolver } from "node:dns/promises";
import { createServer } from "node:http";
import { BlockList, connect, isIP } from "node:net";
import { networkInterfaces } from "node:os";

const denied = new BlockList();
for (const [address, prefix] of [["0.0.0.0", 8], ["10.0.0.0", 8], ["100.64.0.0", 10],
  ["127.0.0.0", 8], ["169.254.0.0", 16], ["172.16.0.0", 12], ["192.0.0.0", 24],
  ["192.0.2.0", 24], ["192.88.99.0", 24], ["192.168.0.0", 16], ["198.18.0.0", 15],
  ["198.51.100.0", 24], ["203.0.113.0", 24], ["224.0.0.0", 3]]) denied.addSubnet(address, prefix);
const global6 = new BlockList();
global6.addSubnet("2000::", 3, "ipv6");
for (const [address, prefix] of [["2001::", 23], ["2001:db8::", 32], ["2002::", 16], ["3fff::", 20]])
  denied.addSubnet(address, prefix, "ipv6");

/** Conservative address admission; mapped IPv4, translation and scoped addresses are not supported. */
export function publicAddress(address) {
  const family = isIP(address);
  if (!family || address.includes("%")) return false;
  if (family === 6 && !global6.check(address, "ipv6")) return false;
  if (denied.check(address, family === 6 ? "ipv6" : "ipv4")) return false;
  const local = new BlockList();
  for (const rows of Object.values(networkInterfaces())) for (const row of rows ?? [])
    local.addAddress(row.address, row.family === "IPv6" ? "ipv6" : "ipv4");
  return !local.check(address, family === 6 ? "ipv6" : "ipv4");
}

export function connectHost(authority) {
  if (typeof authority !== "string" || authority.length > 260 ||
      !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?:443$/i.test(authority))
    throw new Error("Unsupported browser destination.");
  const host = authority.slice(0, -4).toLowerCase();
  if (/(?:^|\.)(?:localhost|local|internal|test|invalid)$/.test(host)) throw new Error("Unsupported browser destination.");
  return host;
}

export function selectPublicAddress(addresses) {
  const unique = [...new Set(addresses)];
  if (!unique.length || unique.length > 32 || !unique.every(publicAddress))
    throw new Error("Unsupported browser destination.");
  return unique[0];
}

/** DNS and socket connection belong to the broker, never to Chromium's destination resolver. */
async function resolvePublic(host) {
  const resolver = new Resolver({ timeout: 2000, tries: 1 });
  const timer = setTimeout(() => resolver.cancel(), 3000);
  try {
    const answers = await Promise.all([resolver.resolve4(host), resolver.resolve6(host)].map(async (query) => {
      try { return await query; }
      catch (error) { if (error.code === "ENODATA" || error.code === "ENOTFOUND") return []; throw error; }
    }));
    return selectPublicAddress(answers.flat());
  } finally { clearTimeout(timer); }
}

/** Guest-only CONNECT broker. It never sees TLS plaintext or any product credentials. */
export async function createGuestNetwork() {
  const username = "guest", password = randomBytes(32).toString("hex");
  const authorization = Buffer.from(`Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`);
  const sockets = new Set();
  let closed = false;
  const server = createServer({ maxHeaderSize: 8192, headersTimeout: 5000, requestTimeout: 5000 },
    (_request, response) => { response.writeHead(403, { Connection: "close" }); response.end(); });
  server.maxConnections = 32;
  server.maxHeadersCount = 16;
  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.setTimeout(30000, () => socket.destroy());
    socket.on("error", () => socket.destroy());
    socket.on("close", () => sockets.delete(socket));
  });
  server.on("clientError", (_error, socket) => socket.destroy());
  server.on("connect", async (request, client, head) => {
    const supplied = Buffer.from(request.headers["proxy-authorization"] ?? "");
    if (supplied.length !== authorization.length || !timingSafeEqual(supplied, authorization)) {
      client.end('HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="guest"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');
      return;
    }
    let upstream;
    try {
      const host = connectHost(request.url);
      if (head.length > 16384 || request.headers["transfer-encoding"] || request.headers["content-length"])
        throw new Error("Unsupported browser request.");
      const address = await resolvePublic(host);
      if (closed || client.destroyed) return;
      // Numeric host plus explicit family prevents a second name lookup or DNS-rebinding race.
      upstream = connect({ host: address, port: 443, family: isIP(address) });
      sockets.add(upstream);
      upstream.setTimeout(10000, () => upstream.destroy());
      upstream.on("error", () => { client.destroy(); upstream.destroy(); });
      upstream.on("close", () => { sockets.delete(upstream); client.destroy(); });
      client.on("close", () => upstream.destroy());
      upstream.once("connect", () => {
        if (closed || client.destroyed || upstream.remoteAddress !== address || !publicAddress(address)) {
          upstream.destroy(); client.destroy(); return;
        }
        upstream.setTimeout(30000);
        client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
        if (head.length) upstream.write(head);
        client.pipe(upstream); upstream.pipe(client);
      });
    } catch { upstream?.destroy(); client.destroy(); }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0, exclusive: true }, resolve);
  });
  const port = server.address().port;
  const close = () => {
    closed = true;
    for (const socket of sockets) socket.destroy();
    server.close();
  };
  server.on("error", close);
  return Object.freeze({
    async configure(session) {
      await session.setProxy({ mode: "fixed_servers", proxyRules: `http://127.0.0.1:${port}`, proxyBypassRules: "<-loopback>" });
      await session.closeAllConnections();
      if (closed) throw new Error("Browser network is unavailable.");
    },
    authenticate(info, callback) {
      if (!closed && info?.isProxy === true && info.host === "127.0.0.1" && info.port === port) callback(username, password);
      else callback();
    },
    close,
  });
}
