import { test, expect } from "@playwright/test";
import { docScreenshot, loadFixture, navigateTo, openApp, selectFixtureField } from "./helpers";

interface Report { n_candidates: number; candidates: { novel?: boolean | null }[] }
const report = loadFixture<Report>("SMACS-0723/report/candidates.json");

test("anomaly page shows candidate cards with badges, SED and cutouts", async ({ page }) => {
  await openApp(page, "/");
  await selectFixtureField(page);
  await navigateTo(page, "Anomalies");

  await expect(page.getByRole("heading", { name: "Anomaly Candidates" })).toBeVisible();
  await expect(page.getByText("12 candidates from 18,437 sources")).toBeVisible();

  const cards = page.getByTestId("candidate-card");
  await expect(cards).toHaveCount(report.n_candidates);

  // Badges derived from the fixture.
  const nNovel = report.candidates.filter((c) => c.novel).length;
  await expect(page.locator("span", { hasText: /^NOVEL$/ })).toHaveCount(nNovel);
  await expect(page.getByText("z=7.84")).toBeVisible();
  await expect(page.getByText("High-z", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("X-ray AGN")).toBeVisible();
  await expect(page.getByText("Star", { exact: true })).toBeVisible();
  await expect(page.getByText("GAIA")).toBeVisible();

  // Thumbnails come from read_binary_file_base64 as data URLs.
  const thumbs = cards.first().locator("img");
  await expect(thumbs.first()).toHaveAttribute("src", /^data:image\/png;base64,/);

  // Expand the top candidate: interactive SED and the multi-band cutout strip.
  await cards.first().getByRole("button").first().click();
  await expect(page.getByText("Spectral Energy Distribution")).toBeVisible();
  await expect(cards.first().locator(".js-plotly-plot")).toBeVisible();
  await expect(page.getByText("Multi-band Cutouts")).toBeVisible();
  // Detection + 6 filters = 7 cutouts, plus the header thumbnail.
  await expect(cards.first().locator("img")).toHaveCount(8);
  await expect(cards.first()).toContainText("Photo-z (EAZY)");
  await expect(cards.first()).toContainText("7.840");

  await docScreenshot(page, "06-anomalies");

  // Quick filters.
  await page.getByRole("button", { name: /^High-z/ }).click();
  await expect(cards).toHaveCount(3);
  await page.getByRole("button", { name: /^Stars/ }).click();
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText("ID 1055");
});
