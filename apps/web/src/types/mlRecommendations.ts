export type MLRecommendationEvidenceKind =
  "PERFORMANCE_PREDICTION" | "DEGRADATION" | "MARKET_REGIME" | "ANOMALY";

export type MLStrategyParameterKind =
  "boolean" | "integer" | "decimal" | "string";

export type MLStrategyParameterValue = null | boolean | number | string;

export interface MLImplementationReference {
  name: string;
  version: string;
  content_checksum: string;
}

export interface MLStrategyParameter {
  name: string;
  kind: string;
  value: MLStrategyParameterValue;
}

export interface MLStrategyReference {
  identity: string;
  checksum: string;
  plugin_name: string;
  plugin_version: string;
  plugin_schema_version: number;
  lifecycle_version: number;
  parameters: MLStrategyParameter[];
  parameters_checksum: string;
  configuration_checksum: string;
  implementation: MLImplementationReference;
}

export interface MLRecommendationEvidenceReference {
  kind: MLRecommendationEvidenceKind;
  evidence_identity: string;
  evidence_checksum: string;
  information_time: string;
  strategy_subject_identity: string | null;
}

export interface MLRecommendation {
  schema_version: 1;
  identity: string;
  checksum: string;
  candidate: MLStrategyReference;
  evidence: MLRecommendationEvidenceReference[];
  rationale_codes: string[];
  recommendation_time: string;
  implementation: MLImplementationReference;
}

export interface MLRecommendationPage {
  schema_version: 1;
  items: MLRecommendation[];
  count: number;
}
