import assert from "node:assert/strict";
import { connect } from "node:net";
import test from "node:test";
import { connectHost, createGuestNetwork, publicAddress, selectPublicAddress } from "../src/mini-browser-network.js";

test("broker excludes special, local, mapped and transition addresses", () => {
  for (const address of ["127.0.0.1", "10.1.2.3", "169.254.169.254", "100.64.0.1", "192.168.1.1", "224.0.0.1", "0.0.0.0", "198.18.0.1", "::1", "::ffff:8.8.8.8", "64:ff9b::808:808", "2002:808:808::1", "2001:db8::1", "fc00::1", "fe80::1%en0", "nonsense"]) assert.equal(publicAddress(address), false, address);
  for (const address of ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"]) assert.equal(publicAddress(address), true, address);
});
test("DNS answers in every non-public class refuse, so a public name cannot point at this host or the tailnet", () => {
  const classes = {
    loopback: ["127.0.0.1", "127.255.0.1", "::1"],
    private: ["10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.0.1", "fc00::1", "fd12:3456::1"],
    linkLocal: ["169.254.0.1", "fe80::1"],
    tailnet: ["100.64.0.1", "100.100.100.100", "100.127.255.255", "fd7a:115c:a1e0::1"],
    unspecified: ["0.0.0.0", "::"],
  };
  for (const [name, addresses] of Object.entries(classes))
    for (const address of addresses) {
      assert.equal(publicAddress(address), false, `${name}: ${address}`);
      assert.throws(() => selectPublicAddress(["93.184.215.14", address]), undefined, `${name}: ${address}`);
    }
  for (const authority of ["mini.tail1234.ts.net:443", "printer.home.arpa:443"]) assert.throws(() => connectHost(authority));
});
test("CONNECT accepts only canonical DNS authorities on TLS port", () => {
  assert.equal(connectHost("EXAMPLE.com:443"), "example.com");
  for (const authority of ["example.com:80", "example.com:8443", "127.0.0.1:443", "[::1]:443", "localhost:443", "host.local:443", "user@example.com:443", "example.com.:443", "example.com:443/path", "example.com:443\r\n", "example.com"]) assert.throws(() => connectHost(authority));
});
test("fresh session uses fixed proxy without bypass; credentials expire when broker closes", async () => {
  const network = await createGuestNetwork();
  let configuration, closed = false;
  await network.configure({ setProxy: async (value) => { configuration = value; }, closeAllConnections: async () => { closed = true; } });
  assert(closed);
  assert.equal(configuration.mode, "fixed_servers");
  assert.equal(configuration.proxyBypassRules, "<-loopback>");
  const proxy = new URL(configuration.proxyRules);
  assert.equal(proxy.hostname, "127.0.0.1");
  let credentials;
  network.authenticate({ isProxy: true, host: proxy.hostname, port: Number(proxy.port) }, (...args) => { credentials = args; });
  assert.equal(credentials[0], "guest");
  assert.equal(credentials[1].length, 64);
  for (const info of [{ isProxy: false, host: proxy.hostname, port: Number(proxy.port) }, { isProxy: true, host: "example.com", port: Number(proxy.port) }]) network.authenticate(info, (...args) => assert.deepEqual(args, []));
  const request = (headers) => new Promise((resolve, reject) => {
    const socket = connect({ host: proxy.hostname, port: Number(proxy.port) });
    let response = "";
    socket.setTimeout(2000, () => { socket.destroy(); reject(new Error("Probe timed out")); });
    socket.on("error", reject);
    socket.on("data", (data) => { response += data.toString(); });
    socket.on("end", () => resolve(response));
    socket.on("connect", () => socket.end(`CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1:443\r\n${headers}\r\n`));
  });
  try {
    assert.match(await request(""), /^HTTP\/1.1 407/);
    assert.equal(await request(`Proxy-Authorization: Basic ${Buffer.from(credentials.join(":" )).toString("base64")}\r\n`), "");
  } finally { network.close(); }
  network.authenticate({ isProxy: true, host: proxy.hostname, port: Number(proxy.port) }, (...args) => assert.deepEqual(args, []));
  await assert.rejects(network.configure({ setProxy: async () => {}, closeAllConnections: async () => {} }));
});

test("one private DNS answer refuses the entire destination, including rebinding answers", () => {
  assert.equal(selectPublicAddress(["1.1.1.1", "1.1.1.1"]), "1.1.1.1");
  for (const answers of [[], ["1.1.1.1", "127.0.0.1"], ["2606:4700:4700::1111", "::1"], ["not-an-address"]]) assert.throws(() => selectPublicAddress(answers));
});
