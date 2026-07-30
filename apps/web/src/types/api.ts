/**
 * Stable application-facing aliases generated from the backend OpenAPI schema.
 *
 * Run `npm run generate:api` after changing a Pydantic request/response model.
 */
import type { components } from './openapi.generated'

type ApiSchemas = components['schemas']

export type JsonValue = ApiSchemas['JsonValue-Input']
export type AdminMe = ApiSchemas['AdminMeResponse']
export type HealthResponse = ApiSchemas['HealthResponse']
export type PageMeta = ApiSchemas['PageMeta']

export type SimulationStatus = ApiSchemas['SimulationStatus']
export type SimulationListItem = ApiSchemas['SimulationListItem']
export type SimulationDetail = ApiSchemas['SimulationDetailResponse']
export type SimulationListResponse = ApiSchemas['SimulationListResponse']
export type SimulationCreateRequest = ApiSchemas['SimulationCreateRequest']

export type MovementCreateType = ApiSchemas['MovementCreateType']
export type CapitalMovementType = ApiSchemas['CapitalMovementType']
export type MovementCreateRequest = ApiSchemas['MovementCreateRequest']
export type CapitalMovement = ApiSchemas['CapitalMovementResponse']
export type MovementListResponse = ApiSchemas['MovementListResponse']

export type SettingPatchRequest = ApiSchemas['SettingPatchRequest']
export type Setting = ApiSchemas['SettingResponse']
export type SettingsListResponse = ApiSchemas['SettingsListResponse']

export type PublicSimulationSummary =
  ApiSchemas['PublicSimulationSummaryResponse']
export type ApiErrorEnvelope = ApiSchemas['ErrorResponse']
