export interface JobSummary {
  id: number;
  title: string;
  company: string | null;
  source: string;
  first_seen: string;
  location_text: string | null;
  lat: number | null;
  lon: number | null;
  site_label: string | null;
  hard_pass: boolean | null;
  hard_reasons: { reasons: string[]; flags: string[] } | null;
  fit_score: number | null;
  travel: Record<string, { minutes: number | null; transfers: number | null }>;
  role_family: string | null;
  workplace_mode: string | null;
  contract_type: string | null;
  salary_min_eur_month: number | null;
  application_deadline: string | null;

  // Schema v1 compatibility. New exports keep these fields in job-details.json.
  url?: string | null;
  alt_urls?: string[];
  last_seen?: string;
  extraction?: Record<string, unknown>;
  fit_reasons?: string[] | null;
  gaps?: string[] | null;
  angle?: string | null;
}

export interface JobDetail {
  url: string | null;
  alt_urls: string[];
  last_seen: string;
  extraction: Record<string, unknown>;
  fit_reasons: string[] | null;
  gaps: string[] | null;
  angle: string | null;
}

export interface Company {
  name: string;
  website: string | null;
  career_url: string | null;
  initiative_score: number;
  summary: string;
  sites: { label: string; lat: number | null; lon: number | null }[];
}

export interface Meta {
  data_schema_version?: number;
  generated_at: string;
  anchors: { id: string; label: string; max_minutes: number }[];
  counts: Record<string, number>;
}

export type ColorMode = "score" | "travel";

export interface Filters {
  segment: string;
  sort: string;
  role: string;
  source: string;
  contract: string;
  score: string;
  days: string;
  anchor: string;
  minutes: string;
  initiative: boolean;
  saved: boolean;
  color: ColorMode;
  foreign: boolean;
  noLocation: boolean;
  job: string;
}

export interface StoredState {
  saved: Set<number>;
  overrides: Record<string, string>;
}

export interface LocationGroup {
  key: string;
  lat: number;
  lon: number;
  jobIds: number[];
  jobCount: number;
  firstJobId: number;
  hasEligible: number;
  hasScored: number;
  maxScore: number;
  minTravel: number;
  color: string;
}
