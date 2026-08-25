import { describe, expect, it } from "vitest";

import {
  defaultFilters, filterJobs, filtersUrl, groupJobsByLocation, readFilters,
} from "./state";
import type { JobSummary, StoredState } from "./types";

const stored = (): StoredState => ({ saved: new Set(), overrides: {} });

function job(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: 1, title: "Job", company: "Company", source: "test",
    first_seen: "2026-08-25T00:00:00Z", location_text: "Wien",
    lat: 48.2, lon: 16.3, site_label: "Wien", hard_pass: true,
    hard_reasons: { reasons: [], flags: [] }, fit_score: 80, travel: {},
    role_family: "bioinformatics", workplace_mode: "hybrid", contract_type: "permanent",
    salary_min_eur_month: null, application_deadline: null,
    ...overrides,
  };
}

describe("frontend state", () => {
  it("round-trips the extended shareable URL state", () => {
    const filters = { ...defaultFilters(), color: "travel" as const, foreign: true, noLocation: true, score: "60" };
    const url = filtersUrl(filters, "/heimspiel/");
    expect(url).toContain("color=travel");
    expect(readFilters(url).foreign).toBe(true);
    expect(readFilters(url).noLocation).toBe(true);
    expect(readFilters(url).score).toBe("60");
  });

  it("uses role overrides in filters", () => {
    const state = stored();
    state.overrides["1"] = "data_science";
    const filters = { ...defaultFilters(), role: "data_science" };
    expect(filterJobs([job()], filters, state, null)).toHaveLength(1);
  });

  it("honors saved-only state immediately", () => {
    const state = stored();
    const filters = { ...defaultFilters(), saved: true };
    state.saved.add(1);
    expect(filterJobs([job()], filters, state, null)).toHaveLength(1);
    state.saved.delete(1);
    expect(filterJobs([job()], filters, state, null)).toHaveLength(0);
  });

  it("groups exact locations and keeps the best visible score", () => {
    const groups = groupJobsByLocation([
      job({ id: 1, fit_score: 65 }),
      job({ id: 2, fit_score: 91 }),
      job({ id: 3, lat: 47.1, lon: 15.4, fit_score: 50 }),
    ], defaultFilters(), stored());
    expect(groups).toHaveLength(2);
    expect(groups[0]).toMatchObject({ jobCount: 2, maxScore: 91, color: "#16a34a" });
  });

  it("does not let rejected jobs hide an eligible unscored location", () => {
    const groups = groupJobsByLocation([
      job({ id: 1, hard_pass: false, fit_score: null, hard_reasons: { reasons: ["PhD erforderlich"], flags: [] } }),
      job({ id: 2, fit_score: null }),
    ], { ...defaultFilters(), segment: "alle" }, stored());
    expect(groups[0]).toMatchObject({ hasEligible: 1, hasScored: 0, color: "#60a5fa" });
  });
});
