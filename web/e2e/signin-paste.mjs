// An app whose sign-in is refused (no address bar): it says what to do and signs in from the pasted link.
import { chromium } from "playwright-core";

const [url, shots] = process.argv.slice(2);
const browser = await chromium.launch();
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 700 }, serviceWorkers: "block" });
  await context.addInitScript(() => {
    if (!sessionStorage.getItem("hz.token")) sessionStorage.setItem("hz.token", "dead-token-of-an-earlier-hz-ui");
  });
  const page = await context.newPage();
  const origin = new URL(url).origin;
  await page.goto(`${origin}/`);
  await page.getByText("Sign in again").first().waitFor({ timeout: 20000 });
  const field = page.getByLabel("Paste the link `hz ui` printed");
  await field.waitFor();
  await page.screenshot({ path: `${shots}/signin-paste-refused.png` });
  await field.fill(`${origin.replace("127.0.0.1", "localhost")}/#token=x`);
  await page.getByRole("button", { name: "Sign in again", exact: true }).click();
  await page.getByText(/isn't a link with a sign-in token/).waitFor();
  await field.fill(url);
  await page.getByRole("button", { name: "Sign in again", exact: true }).click();
  await page.getByRole("contentinfo").waitFor({ timeout: 20000 });
  await page.getByText("Sign in again").first().waitFor({ state: "hidden", timeout: 10000 });
  await page.screenshot({ path: `${shots}/signin-paste-signed-in.png` });
  console.log(JSON.stringify({ pasted: true }));
} finally {
  await browser.close();
}
