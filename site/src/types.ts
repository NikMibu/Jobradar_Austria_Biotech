export type TrafficStatus = "green" | "yellow" | "red";
export type Recommendation = "bewerben" | "stretch" | "nicht_bewerben";
export type Segment = "bewerben" | "stretch" | "nicht";
export type PositionType = "job" | "phd" | "predoc" | "postdoc" | "internship" | "thesis" | "initiative";
export type RankingLabel = "yes" | "maybe" | "no";

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
  score_confidence?: number | null;
  formal_status?: TrafficStatus | null;
  practical_status?: TrafficStatus | null;
  recommendation?: Recommendation | null;
  text_quality?: "full" | "stub" | null;
  position_type?: PositionType | null;
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
  score_breakdown?: Record<string, number> | null;
  score_evidence?: Record<string, unknown> | null;
  formal_reasons?: string[];
  practical_reasons?: string[];
  fallback_model?: string | null;
}

export interface JobDetail {
  url: string | null;
  alt_urls: string[];
  last_seen: string;
  extraction: Record<string, unknown>;
  fit_reasons: string[] | null;
  gaps: string[] | null;
  angle: string | null;
  score_breakdown: Record<string, number> | null;
  score_evidence: Record<string, unknown> | null;
  formal_reasons: string[];
  practical_reasons: string[];
  fallback_model: string | null;
  recommendation_probs: Partial<Record<Recommendation, number>> | null;
  recommendation_notes: string[];
}

export interface JevRequirement {
  requirement: string;
  importance: "must" | "nice";
  job_evidence: string;
  level: number | null;
  p_missing: number;
  p_transferable: number;
  p_direct: number;
}

export interface JevEvidence {
  requirements: JevRequirement[];
  domain: number;
  interest: number;
  phd_topic: number | null;
  not_claim: number;
  hard_no: number;
  confidence: number;
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
  profile_version?: number;
  score_version?: number;
  extraction_model?: string;
  scoring_model?: string;
  generated_at: string;
  anchors: { id: string; label: string; max_minutes: number }[];
  counts: Record<string, number>;
}

export type ColorMode = "score" | "travel";

export interface Filters {
  segment: string;
  position: string;
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
  expired: boolean;
  job: string;
}

export interface StoredState {
  saved: Set<number>;
  overrides: Record<string, string>;
  labels: Record<string, RankingLabel>;
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
