import type { Filters, JobSummary, LocationGroup, Segment, StoredState } from "./types";

export const TRAVEL_UNKNOWN = 9999;

export const DEFAULT_SEGMENT = "top";
export const SEGMENTS: [string, string][] = [
  ["top", "Bewerben + Stretch"], ["bewerben", "Bewerben"], ["stretch", "Stretch"],
  ["nicht", "Nicht"], ["alle", "Alle"],
];
export const POSITIONS: [string, string][] = [
  ["", "Alle Stellen"], ["job", "Jobs"], ["phd", "PhD / Predoc"], ["postdoc", "Postdoc"],
  ["training", "Praktikum / Thesis"],
];
const POSITION_GROUP: Record<string, string> = {
  job: "job", phd: "phd", predoc: "phd", postdoc: "postdoc", internship: "training", thesis: "training",
};
export const positionGroup = (job: JobSummary): string => POSITION_GROUP[job.position_type ?? "job"] ?? "job";
export const REC_LABEL: Record<Segment, string> = {
  bewerben: "Bewerben", stretch: "Stretch", nicht: "Nicht bewerben",
};

export const defaultFilters = (): Filters => ({
  segment: DEFAULT_SEGMENT, position: "", sort: "rec", role: "", source: "", contract: "",
  score: "", days: "", anchor: "", minutes: "", initiative: false, saved: false,
  color: "score", foreign: false, noLocation: false, job: "",
});

export function readFilters(search: string): Filters {
  const p = new URLSearchParams(search);
  return {
    segment: p.get("seg") ?? DEFAULT_SEGMENT,
    position: p.get("pos") ?? "",
    sort: p.get("sort") ?? "rec",
    role: p.get("role") ?? "",
    source: p.get("source") ?? "",
    contract: p.get("contract") ?? "",
    score: p.get("score") ?? "",
    days: p.get("days") ?? "",
    anchor: p.get("anchor") ?? "",
    minutes: p.get("minutes") ?? "",
    initiative: p.get("init") === "1",
    saved: p.get("saved") === "1",
    color: p.get("color") === "travel" ? "travel" : "score",
    foreign: p.get("foreign") === "hide",
    noLocation: p.get("noloc") === "1",
    job: p.get("job") ?? "",
  };
}

export function filtersUrl(filters: Filters, pathname: string): string {
  const p = new URLSearchParams();
  if (filters.segment !== DEFAULT_SEGMENT) p.set("seg", filters.segment);
  if (filters.position) p.set("pos", filters.position);
  if (filters.sort !== "rec") p.set("sort", filters.sort);
  if (filters.role) p.set("role", filters.role);
  if (filters.source) p.set("source", filters.source);
  if (filters.contract) p.set("contract", filters.contract);
  if (filters.score) p.set("score", filters.score);
  if (filters.days) p.set("days", filters.days);
  if (filters.anchor) p.set("anchor", filters.anchor);
  if (filters.minutes) p.set("minutes", filters.minutes);
  if (filters.initiative) p.set("init", "1");
  if (filters.saved) p.set("saved", "1");
  if (filters.color === "travel") p.set("color", "travel");
  if (filters.foreign) p.set("foreign", "hide");
  if (filters.noLocation) p.set("noloc", "1");
  if (filters.job) p.set("job", filters.job);
  const query = p.toString();
  return query ? `?${query}` : pathname;
}

export const coordinateKey = (lat: number, lon: number): string =>
  `${lat.toFixed(7)},${lon.toFixed(7)}`;

export const isForeign = (job: JobSummary): boolean =>
  !!job.hard_reasons?.flags.includes("Standort außerhalb Österreichs");

export const effectiveRole = (job: JobSummary, state: StoredState): string =>
  state.overrides[String(job.id)] ?? job.role_family ?? "";

/** Empfehlung aus dem Jev-Scoring; ältere Exporte ohne Empfehlung fallen auf die
 *  Filterlogik zurück (Treffer → bewerben, Grenzfall → stretch, raus → nicht). */
export function effectiveSegment(job: JobSummary, state: StoredState): Segment {
  const overridden = state.overrides[String(job.id)] != null;
  const roleOnly = !!job.hard_reasons?.reasons.length
    && job.hard_reasons.reasons.every((reason) => reason.startsWith("Rollenfamilie"));
  if (job.recommendation) {
    if (job.recommendation === "nicht_bewerben") return overridden && roleOnly ? "stretch" : "nicht";
    return job.recommendation;
  }
  if (job.hard_pass) return job.hard_reasons?.flags.length ? "stretch" : "bewerben";
  if (overridden && roleOnly) return "stretch";
  return "nicht";
}

const REC_RANK: Record<Segment, number> = { bewerben: 0, stretch: 1, nicht: 2 };

export function segmentMatches(segment: string, value: Segment): boolean {
  if (segment === "alle") return true;
  if (segment === "top") return value !== "nicht";
  return segment === value;
}

export function bestTravel(job: JobSummary, anchor: string): number | null {
  const entries = anchor ? [job.travel[anchor]] : Object.values(job.travel);
  const minutes = entries.flatMap((entry) => entry?.minutes == null ? [] : [entry.minutes]);
  return minutes.length ? Math.min(...minutes) : null;
}

export function scoreColor(job: JobSummary, state: StoredState): string {
  if (effectiveSegment(job, state) === "nicht") return "#9ca3af";
  const score = job.fit_score;
  if (score == null) return "#60a5fa";
  if (score >= 80) return "#16a34a";
  if (score >= 60) return "#84cc16";
  if (score >= 40) return "#f59e0b";
  return "#ef4444";
}

export function travelColor(minutes: number | null): string {
  if (minutes == null) return "#9ca3af";
  if (minutes <= 45) return "#16a34a";
  if (minutes <= 60) return "#84cc16";
  if (minutes <= 90) return "#f59e0b";
  return "#ef4444";
}

export function filterJobs(
  jobs: JobSummary[], filters: Filters, state: StoredState, locationKey: string | null,
): JobSummary[] {
  const cutoff = filters.days ? Date.now() - Number(filters.days) * 86_400_000 : null;
  const output = jobs.filter((job) => {
    if (!segmentMatches(filters.segment, effectiveSegment(job, state))) return false;
    if (filters.position && positionGroup(job) !== filters.position) return false;
    if (filters.saved && !state.saved.has(job.id)) return false;
    if (filters.role && effectiveRole(job, state) !== filters.role) return false;
    if (filters.source && job.source !== filters.source) return false;
    if (filters.contract && job.contract_type !== filters.contract) return false;
    if (filters.score && (job.fit_score == null || job.fit_score < Number(filters.score))) return false;
    if (cutoff && Date.parse(job.first_seen) < cutoff) return false;
    if (filters.minutes) {
      const travel = bestTravel(job, filters.anchor);
      if (travel == null || travel > Number(filters.minutes)) return false;
    }
    if (locationKey && (job.lat == null || job.lon == null || coordinateKey(job.lat, job.lon) !== locationKey)) return false;
    if (filters.noLocation && job.lat != null && job.lon != null) return false;
    if (filters.foreign && isForeign(job)) return false;
    return true;
  });
  const byScore = (a: JobSummary, b: JobSummary) => (b.fit_score ?? -1) - (a.fit_score ?? -1);
  const sorters: Record<string, (a: JobSummary, b: JobSummary) => number> = {
    rec: (a, b) =>
      REC_RANK[effectiveSegment(a, state)] - REC_RANK[effectiveSegment(b, state)] || byScore(a, b),
    deadline: (a, b) =>
      (a.application_deadline ?? "9999").localeCompare(b.application_deadline ?? "9999") || byScore(a, b),
    score: (a, b) => (b.fit_score ?? -1) - (a.fit_score ?? -1),
    travel: (a, b) => (bestTravel(a, filters.anchor) ?? 9e9) - (bestTravel(b, filters.anchor) ?? 9e9),
    new: (a, b) => Date.parse(b.first_seen) - Date.parse(a.first_seen),
  };
  return output.sort(sorters[filters.sort] ?? sorters.rec);
}

function groupColor(group: Omit<LocationGroup, "color">, filters: Filters): string {
  if (filters.color === "travel")
    return travelColor(group.minTravel >= TRAVEL_UNKNOWN ? null : group.minTravel);
  if (!group.hasEligible) return "#9ca3af";
  if (!group.hasScored) return "#60a5fa";
  if (group.maxScore >= 80) return "#16a34a";
  if (group.maxScore >= 60) return "#84cc16";
  if (group.maxScore >= 40) return "#f59e0b";
  return "#ef4444";
}

export function groupJobsByLocation(
  jobs: JobSummary[], filters: Filters, state: StoredState,
): LocationGroup[] {
  const groups = new Map<string, Omit<LocationGroup, "color">>();
  for (const job of jobs) {
    if (job.lat == null || job.lon == null) continue;
    const key = coordinateKey(job.lat, job.lon);
    const eligible = effectiveSegment(job, state) !== "nicht";
    const scored = eligible && job.fit_score != null;
    const travel = bestTravel(job, filters.anchor) ?? TRAVEL_UNKNOWN;
    const existing = groups.get(key);
    if (!existing) {
      groups.set(key, {
        key, lat: job.lat, lon: job.lon, jobIds: [job.id], jobCount: 1, firstJobId: job.id,
        hasEligible: Number(eligible), hasScored: Number(scored),
        maxScore: scored ? job.fit_score! : -1, minTravel: travel,
      });
      continue;
    }
    existing.jobIds.push(job.id);
    existing.jobCount += 1;
    existing.hasEligible = Math.max(existing.hasEligible, Number(eligible));
    existing.hasScored = Math.max(existing.hasScored, Number(scored));
    if (scored) existing.maxScore = Math.max(existing.maxScore, job.fit_score!);
    existing.minTravel = Math.min(existing.minTravel, travel);
  }
  return [...groups.values()].map((group) => ({ ...group, color: groupColor(group, filters) }));
}
