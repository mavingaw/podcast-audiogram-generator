/**
 * Multi-viewport layout audit.
 *
 * The smoke tests catch crashes and body-level horizontal overflow; they do
 * NOT catch a panel whose content is clipped inside its own grid column, or
 * a control that lost its styling. This drives the real UI at desktop /
 * tablet / phone widths, reports any element that spills the viewport, and
 * (with --shots DIR) writes a screenshot of every view for eyes-on review.
 * It navigates via the hamburger below 780px, so tablet/phone views are the
 * real pages, not Home three times.
 *
 *   node layout-audit.mjs --base-url https://... --username ... --password ... [--shots DIR]
 * (args are positional here: base user pass [shots])
 */
import { chromium } from "playwright";
const [base, user, pass, shots] = process.argv.slice(2);
const browser = await chromium.launch();
const widths = [1440, 1024, 768];
const findings = [];

for (const w of widths) {
  const page = await browser.newPage({ viewport: { width: w, height: 900 } });
  await page.goto(base, { waitUntil: "networkidle" });
  await page.fill('input[autocomplete="username"]', user);
  await page.fill('input[type="password"]', pass);
  await page.click(".primary");
  await page.waitForTimeout(2500);

  const visit = async (label, nav) => {
    if (nav) {
      let btn = page.locator(`.main-nav button:has-text("${nav}")`).first();
      // Below 780px the sidebar hides behind the hamburger; open it first,
      // or the nav click silently no-ops and we screenshot the wrong page.
      if (!(await btn.isVisible().catch(() => false))) {
        const burger = page.locator(".mobile-menu").first();
        if (await burger.count()) { await burger.click(); await page.waitForTimeout(500); }
      }
      btn = page.locator(`.main-nav button:has-text("${nav}")`).first();
      if (await btn.isVisible().catch(() => false)) { await btn.click(); await page.waitForTimeout(1200); }
      else { findings.push(`[${w}px ${label}] could not navigate (no hamburger?)`); }
    }
    // Any element wider than the viewport, or whose right edge spills it.
    const spills = await page.evaluate((vw) => {
      const out = [];
      for (const el of document.querySelectorAll("body *")) {
        const r = el.getBoundingClientRect();
        if (r.width < 4 || r.height < 4) continue;
        if (r.right > vw + 2 || r.left < -2) {
          const cls = String(el.className?.baseVal ?? el.className ?? "").split(" ")[0];
          if (cls) out.push(`${el.tagName.toLowerCase()}.${cls} right=${Math.round(r.right)}`);
        }
      }
      return [...new Set(out)].slice(0, 8);
    }, w);
    const bodyScroll = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    if (bodyScroll > 2 || spills.length) {
      findings.push(`[${w}px ${label}] bodyOverflow=${bodyScroll}px  spills: ${spills.join(", ") || "none"}`);
    }
    if (shots) await page.screenshot({ path: `${shots}/audit-${w}-${label}.png` });
  };

  await visit("home", null);
  await visit("projects", "Projects");
  const card = page.locator(".library-card > button").first();
  if (await card.count()) { await card.click(); await page.waitForTimeout(2500); await visit("studio", null); }
  await visit("settings", "Settings");
  await page.close();
}

console.log(findings.length ? findings.join("\n") : "No overflow at any width/view.");
await browser.close();
