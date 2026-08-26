import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import {
  EmptyState,
  InlineError,
  LoadingState,
  SuccessMessage,
} from "../../components/States";
import { ApiError, apiClient } from "../../http/client";
import type {
  OperationalPaperSessionProfileApproveRequest,
  OperationalPaperSessionProfileArchiveRequest,
  OperationalPaperSessionProfileCreateRequest,
  OperationalPaperSessionProfileCurrent,
  OperationalPaperSessionProfileIntentRequest,
  OperationalPaperSessionProfileList,
  OperationalPaperSessionProfileReplaceRequest,
  OperationalPaperSessionProfileRevision,
  OperationalPaperSessionProfileRevisionList,
  OperationalPaperSessionProfileState,
} from "../../types/api";
import { formatDate, getErrorMessage } from "../../utils/format";

const PAGE_SIZE = 20;
const HISTORY_PAGE_SIZE = 20;
const UTC_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
const INTEGER_PATTERN = /^(0|[1-9]\d*)$/;
const DECIMAL_PATTERN = /^-?\d+(?:\.\d+)?$/;

const FORM_GRID = "form-grid mandate-form-grid";
const COMPACT_GHOST = "button button--ghost button--compact";
const WARNING_NOTICE = "operation-notice operation-notice--warning";

const STATE_LABELS: Record<OperationalPaperSessionProfileState, string> = {
  DRAFT: "Rascunho",
  APPROVED: "Aprovado",
  ARCHIVED: "Arquivado",
};

type ProfileInstrument =
  OperationalPaperSessionProfileIntentRequest["selected_instrument"];
type PositionSizingKind = NonNullable<
  OperationalPaperSessionProfileIntentRequest["execution"]["position_sizing"]
>["kind"];

type TriStateKey =
  "force_close_at_end" | "stop_on_max_drawdown" | "allow_all_in";

type ToggleKey =
  "position_sizing_enabled" | "stop_loss_enabled" | "market_regime_enabled";

interface IntentDraft {
  name: string;
  description: string;
  mandate_id: string;
  approved_revision: string;
  mandate_checksum: string;
  exchange: ProfileInstrument["exchange"];
  market_type: ProfileInstrument["market_type"];
  base_asset: string;
  quote_asset: string;
  timeframe: string;
  start_at: string;
  warmup_candles: string;
  strategy_definition_id: string;
  strategy_revision: string;
  strategy_checksum: string;
  maker_fee_bps: string;
  taker_fee_bps: string;
  fixed_slippage_bps: string;
  force_close_at_end: "" | "true" | "false";
  position_sizing_enabled: boolean;
  position_sizing_kind: PositionSizingKind;
  position_sizing_value: string;
  position_sizing_reserve: string;
  minimum_quantity: string;
  quantity_step: string;
  price_tick: string;
  minimum_notional: string;
  maximum_notional: string;
  max_order_notional: string;
  max_position_notional: string;
  max_open_orders: string;
  max_total_orders: string;
  max_drawdown_pct: string;
  stop_on_max_drawdown: "" | "true" | "false";
  allow_all_in: "" | "true" | "false";
  risk_minimum_quote_reserve: string;
  stop_loss_enabled: boolean;
  stop_loss_value: string;
  history_window: string;
  max_candles: string;
  max_orders: string;
  max_events: string;
  engine_version: string;
  market_regime_enabled: boolean;
  fast_ema_period: string;
  slow_ema_period: string;
  atr_period: string;
  volatile_atr_ratio: string;
  trend_strength_threshold: string;
  market_regime_schema_version: string;
}

type DraftStringKey = {
  [Key in keyof IntentDraft]: IntentDraft[Key] extends string ? Key : never;
}[keyof IntentDraft];

type DraftField = [
  key: DraftStringKey,
  label: string,
  required?: boolean,
  numeric?: boolean,
  placeholder?: string,
  multiline?: boolean,
];

interface ConfirmedCreateIntent {
  payload: OperationalPaperSessionProfileCreateRequest;
}

type MutationSnapshot =
  | {
      kind: "replace";
      profileId: string;
      name: string;
      payload: OperationalPaperSessionProfileReplaceRequest;
    }
  | {
      kind: "approve" | "archive";
      current: OperationalPaperSessionProfileCurrent;
    };

const emptyDraft = (): IntentDraft => ({
  name: "",
  description: "",
  mandate_id: "",
  approved_revision: "",
  mandate_checksum: "",
  exchange: "binance",
  market_type: "spot",
  base_asset: "",
  quote_asset: "",
  timeframe: "",
  start_at: "",
  warmup_candles: "",
  strategy_definition_id: "",
  strategy_revision: "",
  strategy_checksum: "",
  maker_fee_bps: "",
  taker_fee_bps: "",
  fixed_slippage_bps: "",
  force_close_at_end: "",
  position_sizing_enabled: false,
  position_sizing_kind: "explicit_quantity",
  position_sizing_value: "",
  position_sizing_reserve: "",
  minimum_quantity: "",
  quantity_step: "",
  price_tick: "",
  minimum_notional: "",
  maximum_notional: "",
  max_order_notional: "",
  max_position_notional: "",
  max_open_orders: "",
  max_total_orders: "",
  max_drawdown_pct: "",
  stop_on_max_drawdown: "",
  allow_all_in: "",
  risk_minimum_quote_reserve: "",
  stop_loss_enabled: false,
  stop_loss_value: "",
  history_window: "",
  max_candles: "",
  max_orders: "",
  max_events: "",
  engine_version: "",
  market_regime_enabled: false,
  fast_ema_period: "",
  slow_ema_period: "",
  atr_period: "",
  volatile_atr_ratio: "",
  trend_strength_threshold: "",
  market_regime_schema_version: "",
});

function strictInteger(value: string, label: string, minimum = 0): number {
  if (!INTEGER_PATTERN.test(value))
    throw new Error(`${label} deve ser um inteiro explícito.`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum)
    throw new Error(`${label} está fora do intervalo permitido.`);
  return parsed;
}

function decimal(value: string, label: string): string {
  if (!DECIMAL_PATTERN.test(value))
    throw new Error(`${label} deve ser um decimal base 10 explícito.`);
  return value;
}

function optionalDecimal(value: string, label: string): string | null {
  return value === "" ? null : decimal(value, label);
}

function explicitBoolean(
  value: IntentDraft["force_close_at_end"],
  label: string,
): boolean {
  if (value === "")
    throw new Error(`${label} deve ser escolhido explicitamente.`);
  return value === "true";
}

function toIntent(
  draft: IntentDraft,
): OperationalPaperSessionProfileIntentRequest {
  if (!UTC_PATTERN.test(draft.start_at))
    throw new Error("Início UTC deve usar YYYY-MM-DDTHH:mm:ssZ.");
  if (!draft.mandate_id || !draft.mandate_checksum)
    throw new Error("Selecione e revise um mandato aprovado.");
  if (!draft.strategy_definition_id || !draft.strategy_checksum)
    throw new Error("Selecione e revise uma estratégia ativa.");

  return {
    name: draft.name.trim(),
    description: draft.description.trim(),
    mandate_binding: {
      mandate_id: draft.mandate_id,
      approved_revision: strictInteger(
        draft.approved_revision,
        "Revisão aprovada do mandato",
        1,
      ),
      specification_checksum: draft.mandate_checksum,
    },
    selected_instrument: {
      exchange: draft.exchange,
      market_type: draft.market_type,
      base_asset: draft.base_asset,
      quote_asset: draft.quote_asset,
    },
    timeframe: draft.timeframe.trim(),
    start_at: draft.start_at,
    warmup_candles: strictInteger(draft.warmup_candles, "Warmup", 0),
    strategy_definition_id: draft.strategy_definition_id,
    expected_strategy_definition_revision: strictInteger(
      draft.strategy_revision,
      "Revisão da estratégia",
      1,
    ),
    expected_strategy_parameters_checksum: draft.strategy_checksum,
    execution: {
      fees: {
        maker_fee_bps: decimal(draft.maker_fee_bps, "Maker fee"),
        taker_fee_bps: decimal(draft.taker_fee_bps, "Taker fee"),
      },
      slippage: {
        kind: "FIXED_BPS",
        fixed_bps: decimal(draft.fixed_slippage_bps, "Slippage"),
      },
      intrabar_policy: "CONSERVATIVE",
      force_close_at_end: explicitBoolean(
        draft.force_close_at_end,
        "Force close at end",
      ),
      position_sizing: draft.position_sizing_enabled
        ? {
            kind: draft.position_sizing_kind,
            value: optionalDecimal(
              draft.position_sizing_value,
              "Valor do position sizing",
            ),
            minimum_quote_reserve: decimal(
              draft.position_sizing_reserve,
              "Reserva do position sizing",
            ),
          }
        : null,
    },
    instrument_constraints: {
      minimum_quantity: decimal(draft.minimum_quantity, "Quantidade mínima"),
      quantity_step: decimal(draft.quantity_step, "Passo de quantidade"),
      price_tick: decimal(draft.price_tick, "Tick de preço"),
      minimum_notional: decimal(draft.minimum_notional, "Notional mínimo"),
      maximum_notional: optionalDecimal(
        draft.maximum_notional,
        "Notional máximo",
      ),
    },
    risk_limits: {
      max_order_notional: optionalDecimal(
        draft.max_order_notional,
        "Notional máximo por ordem",
      ),
      max_position_notional: optionalDecimal(
        draft.max_position_notional,
        "Notional máximo de posição",
      ),
      max_open_orders: strictInteger(
        draft.max_open_orders,
        "Máximo de ordens abertas",
        1,
      ),
      max_total_orders: strictInteger(
        draft.max_total_orders,
        "Máximo total de ordens",
        1,
      ),
      max_drawdown_pct: optionalDecimal(
        draft.max_drawdown_pct,
        "Drawdown máximo",
      ),
      stop_on_max_drawdown: explicitBoolean(
        draft.stop_on_max_drawdown,
        "Parada por drawdown",
      ),
      allow_all_in: explicitBoolean(draft.allow_all_in, "Permissão all-in"),
      minimum_quote_reserve: decimal(
        draft.risk_minimum_quote_reserve,
        "Reserva mínima de risco",
      ),
      stop_loss: draft.stop_loss_enabled
        ? {
            kind: "fixed_percent",
            value: decimal(draft.stop_loss_value, "Stop loss"),
          }
        : null,
    },
    history_window: strictInteger(draft.history_window, "Janela histórica", 1),
    max_candles: strictInteger(draft.max_candles, "Máximo de candles", 1),
    max_orders: strictInteger(draft.max_orders, "Máximo de ordens", 1),
    max_events: strictInteger(draft.max_events, "Máximo de eventos", 1),
    engine_version: draft.engine_version.trim(),
    market_regime_policy: draft.market_regime_enabled
      ? {
          fast_ema_period: strictInteger(
            draft.fast_ema_period,
            "EMA rápida",
            1,
          ),
          slow_ema_period: strictInteger(draft.slow_ema_period, "EMA lenta", 1),
          atr_period: strictInteger(draft.atr_period, "Período ATR", 1),
          volatile_atr_ratio: decimal(
            draft.volatile_atr_ratio,
            "Razão ATR volátil",
          ),
          trend_strength_threshold: decimal(
            draft.trend_strength_threshold,
            "Limiar de tendência",
          ),
          schema_version: strictInteger(
            draft.market_regime_schema_version,
            "Schema de regime",
            1,
          ),
        }
      : null,
  };
}

function fromCurrent(
  current: OperationalPaperSessionProfileCurrent,
): IntentDraft {
  const s = current.revision.specification;
  const e = s.execution;
  const c = s.instrument_constraints;
  const r = s.risk_limits;
  const snapshot = s.strategy_snapshot;
  const sizing = e.position_sizing;
  const stopLoss = r.stop_loss;
  const regime = s.market_regime_policy;

  return {
    name: s.name,
    description: s.description,
    mandate_id: s.mandate_binding.mandate_id,
    approved_revision: String(s.mandate_binding.approved_revision),
    mandate_checksum: s.mandate_binding.specification_checksum,
    exchange: s.selected_instrument.exchange,
    market_type: s.selected_instrument.market_type,
    base_asset: s.selected_instrument.base_asset,
    quote_asset: s.selected_instrument.quote_asset,
    timeframe: s.timeframe,
    start_at: s.start_at,
    warmup_candles: String(s.warmup_candles),
    strategy_definition_id: snapshot.strategy_definition_id,
    strategy_revision: String(snapshot.source_revision),
    strategy_checksum: snapshot.parameters_checksum,
    maker_fee_bps: e.fees.maker_fee_bps,
    taker_fee_bps: e.fees.taker_fee_bps,
    fixed_slippage_bps: e.slippage.fixed_bps,
    force_close_at_end: String(e.force_close_at_end) as "true" | "false",
    position_sizing_enabled: sizing !== null,
    position_sizing_kind: sizing?.kind ?? "explicit_quantity",
    position_sizing_value: sizing?.value ?? "",
    position_sizing_reserve: sizing?.minimum_quote_reserve ?? "",
    minimum_quantity: c.minimum_quantity,
    quantity_step: c.quantity_step,
    price_tick: c.price_tick,
    minimum_notional: c.minimum_notional,
    maximum_notional: c.maximum_notional ?? "",
    max_order_notional: r.max_order_notional ?? "",
    max_position_notional: r.max_position_notional ?? "",
    max_open_orders: String(r.max_open_orders),
    max_total_orders: String(r.max_total_orders),
    max_drawdown_pct: r.max_drawdown_pct ?? "",
    stop_on_max_drawdown: String(r.stop_on_max_drawdown) as "true" | "false",
    allow_all_in: String(r.allow_all_in) as "true" | "false",
    risk_minimum_quote_reserve: r.minimum_quote_reserve,
    stop_loss_enabled: stopLoss !== null,
    stop_loss_value: stopLoss?.value ?? "",
    history_window: String(s.history_window),
    max_candles: String(s.max_candles),
    max_orders: String(s.max_orders),
    max_events: String(s.max_events),
    engine_version: s.engine_version,
    market_regime_enabled: regime !== null,
    fast_ema_period: regime ? String(regime.fast_ema_period) : "",
    slow_ema_period: regime ? String(regime.slow_ema_period) : "",
    atr_period: regime ? String(regime.atr_period) : "",
    volatile_atr_ratio: regime?.volatile_atr_ratio ?? "",
    trend_strength_threshold: regime?.trend_strength_threshold ?? "",
    market_regime_schema_version: regime ? String(regime.schema_version) : "",
  };
}

function safeError(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return getErrorMessage(error, fallback);
  if (error.status === 0 || error.status >= 500) return fallback;
  if (error.status === 400 || error.status === 422)
    return "Configuração rejeitada. Revise os campos.";
  if (error.status === 401) return "Sessão administrativa expirada.";
  if (error.status === 403) return "Acesso administrativo negado.";
  if (error.status === 404) return "Perfil ou revisão não disponível.";
  if (error.status === 409)
    return "Perfil alterado no servidor. Revise o estado recarregado.";
  return getErrorMessage(error, fallback);
}

function checksumSummary(checksum: string): string {
  return `${checksum.slice(0, 12)}…${checksum.slice(-8)}`;
}

function StateBadge({ state }: { state: OperationalPaperSessionProfileState }) {
  const modifier =
    state === "APPROVED"
      ? "operation-state--success"
      : state === "DRAFT"
        ? "operation-state--warning"
        : "";
  return (
    <span className={`operation-state ${modifier}`.trim()}>
      {STATE_LABELS[state]} ({state})
    </span>
  );
}

function Pagination({
  label,
  limit,
  offset,
  total,
  disabled,
  onChange,
}: {
  label: string;
  limit: number;
  offset: number;
  total: number;
  disabled: boolean;
  onChange(offset: number): void;
}) {
  const currentPage = total === 0 ? 0 : Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit);
  return (
    <nav className="operation-pagination" aria-label={label}>
      <button
        className={COMPACT_GHOST}
        type="button"
        disabled={disabled || offset === 0}
        onClick={() => onChange(Math.max(0, offset - limit))}
      >
        Anterior
      </button>
      <span>
        Página {currentPage} de {totalPages} · {total} registro(s)
      </span>
      <button
        className={COMPACT_GHOST}
        type="button"
        disabled={disabled || offset + limit >= total}
        onClick={() => onChange(offset + limit)}
      >
        Próxima
      </button>
    </nav>
  );
}

function IntentFields({
  draft,
  disabled,
  onChange,
}: {
  draft: IntentDraft;
  disabled: boolean;
  onChange(next: IntentDraft): void;
}) {
  const update = <K extends keyof IntentDraft>(key: K, value: IntentDraft[K]) =>
    onChange({ ...draft, [key]: value });
  const fields = (definitions: DraftField[]) =>
    definitions.map(
      ([key, label, required, numeric, placeholder, multiline]) => (
        <label key={key}>
          {label}
          {multiline ? (
            <textarea
              required={required}
              value={draft[key]}
              placeholder={placeholder}
              onChange={(event) => update(key, event.currentTarget.value)}
            />
          ) : (
            <input
              required={required}
              inputMode={numeric ? "numeric" : undefined}
              value={draft[key]}
              placeholder={placeholder}
              onChange={(event) => update(key, event.currentTarget.value)}
            />
          )}
        </label>
      ),
    );
  const triState = (key: TriStateKey, label: string) => (
    <label>
      {label}
      <select
        required
        value={draft[key]}
        onChange={(event) =>
          update(key, event.currentTarget.value as IntentDraft[TriStateKey])
        }
      >
        <option value="">Escolha</option>
        <option value="true">Sim</option>
        <option value="false">Não</option>
      </select>
    </label>
  );

  const toggle = (key: ToggleKey, label: string) => (
    <label className="mandate-check">
      <input
        type="checkbox"
        checked={draft[key]}
        onChange={(event) => update(key, event.currentTarget.checked)}
      />
      {label}
    </label>
  );

  return (
    <div className="mandate-replace">
      <fieldset disabled={disabled}>
        <legend>Identidade do perfil</legend>
        <div className={FORM_GRID}>
          {fields([
            ["name", "Nome do perfil", true],
            ["description", "Descrição", true, false, undefined, true],
            ["timeframe", "Timeframe canônico", true, false, "Ex.: 1h"],
            [
              "start_at",
              "Início UTC (ISO 8601)",
              true,
              false,
              "YYYY-MM-DDTHH:mm:ssZ",
            ],
            ["warmup_candles", "Warmup candles", true, true],
            ["engine_version", "Engine version", true],
          ])}
        </div>
      </fieldset>

      <fieldset disabled={disabled}>
        <legend>Mandato aprovado e instrumento exatos</legend>
        <div className={FORM_GRID}>
          {fields([
            [
              "mandate_id",
              "Mandate UUID",
              true,
              false,
              "UUID exato do mandato aprovado",
            ],
            ["approved_revision", "Approved mandate revision", true, true],
            [
              "mandate_checksum",
              "Mandate specification SHA-256",
              true,
              false,
              "64 caracteres hexadecimais",
            ],
            ["exchange", "Exchange", true, false, "binance"],
            ["market_type", "Market type", true, false, "spot"],
            ["base_asset", "Base asset", true, false, "BTC"],
            ["quote_asset", "Quote asset", true, false, "USDT"],
          ])}
        </div>
      </fieldset>

      <fieldset disabled={disabled}>
        <legend>Estratégia — evidência exata de origem</legend>
        <div className={FORM_GRID}>
          {fields([
            [
              "strategy_definition_id",
              "Strategy definition UUID",
              true,
              false,
              "UUID exato da estratégia revisada",
            ],
            ["strategy_revision", "Expected strategy revision", true, true],
            [
              "strategy_checksum",
              "Expected parameters SHA-256",
              true,
              false,
              "64 caracteres hexadecimais",
            ],
          ])}
        </div>
      </fieldset>

      <fieldset disabled={disabled}>
        <legend>Execução determinística</legend>
        <div className={FORM_GRID}>
          {fields([
            ["maker_fee_bps", "Maker fee (bps)", true],
            ["taker_fee_bps", "Taker fee (bps)", true],
            ["fixed_slippage_bps", "Slippage fixo (bps)", true],
          ])}
          <label>
            Intrabar policy
            <input value="CONSERVATIVE" disabled />
          </label>
          {triState("force_close_at_end", "Force close at end")}
          {toggle("position_sizing_enabled", "Configurar position sizing")}
          {draft.position_sizing_enabled && (
            <>
              <label>
                Position sizing kind
                <select
                  value={draft.position_sizing_kind}
                  onChange={(event) =>
                    update(
                      "position_sizing_kind",
                      event.currentTarget.value as PositionSizingKind,
                    )
                  }
                >
                  <option value="explicit_quantity">explicit_quantity</option>
                  <option value="fixed_notional">fixed_notional</option>
                  <option value="equity_percent">equity_percent</option>
                </select>
              </label>
              {fields([
                ["position_sizing_value", "Position sizing value"],
                [
                  "position_sizing_reserve",
                  "Position sizing minimum quote reserve",
                  true,
                ],
              ])}
            </>
          )}
        </div>
      </fieldset>

      <fieldset disabled={disabled}>
        <legend>Restrições determinísticas de simulação paper</legend>
        <div className={FORM_GRID}>
          {fields([
            ["minimum_quantity", "Quantidade mínima", true],
            ["quantity_step", "Passo de quantidade", true],
            ["price_tick", "Tick de preço", true],
            ["minimum_notional", "Notional mínimo", true],
            ["maximum_notional", "Notional máximo (opcional)"],
          ])}
        </div>
      </fieldset>

      <fieldset disabled={disabled}>
        <legend>Limites de risco</legend>
        <div className={FORM_GRID}>
          {fields([
            ["max_order_notional", "Notional máximo por ordem (opcional)"],
            ["max_position_notional", "Notional máximo da posição (opcional)"],
            ["max_open_orders", "Máximo de ordens abertas", true, true],
            ["max_total_orders", "Máximo total de ordens", true, true],
            ["max_drawdown_pct", "Drawdown máximo % (opcional)"],
          ])}
          {triState("stop_on_max_drawdown", "Parar no drawdown máximo")}
          {triState("allow_all_in", "Permitir all-in")}
          {fields([
            ["risk_minimum_quote_reserve", "Reserva mínima de cotação", true],
          ])}
          {toggle("stop_loss_enabled", "Configurar stop loss fixed_percent")}
          {draft.stop_loss_enabled &&
            fields([["stop_loss_value", "Stop loss %", true]])}
        </div>
      </fieldset>

      <fieldset disabled={disabled}>
        <legend>Limites do perfil</legend>
        <div className={FORM_GRID}>
          {fields([
            ["history_window", "Janela histórica", true, true],
            ["max_candles", "Máximo de candles", true, true],
            ["max_orders", "Máximo de ordens", true, true],
            ["max_events", "Máximo de eventos", true, true],
          ])}
        </div>
      </fieldset>

      <fieldset disabled={disabled}>
        <legend>Política opcional de regime de mercado</legend>
        {toggle("market_regime_enabled", "Incluir política completa")}
        {draft.market_regime_enabled && (
          <div className={FORM_GRID}>
            {fields([
              ["fast_ema_period", "EMA rápida", true, true],
              ["slow_ema_period", "EMA lenta", true, true],
              ["atr_period", "Período ATR", true, true],
              ["volatile_atr_ratio", "Razão ATR volátil", true],
              [
                "trend_strength_threshold",
                "Limiar de força da tendência",
                true,
              ],
              [
                "market_regime_schema_version",
                "Schema da política",
                true,
                true,
              ],
            ])}
          </div>
        )}
      </fieldset>
    </div>
  );
}

function RevisionDetails({
  revision,
  historical,
}: {
  revision: OperationalPaperSessionProfileRevision;
  historical: boolean;
}) {
  const specification = revision.specification;
  return (
    <section
      className={
        historical
          ? "mandate-revision mandate-revision--historical"
          : "mandate-revision"
      }
      aria-label={
        historical
          ? "Revisão histórica imutável do perfil"
          : "Revisão atual exata do perfil"
      }
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">
            {historical
              ? "Histórico imutável · somente leitura"
              : "Configuração corrente"}
          </p>
          <h3>
            Revisão {revision.revision} · {specification.name}
          </h3>
        </div>
        <code title={revision.specification_checksum}>
          {checksumSummary(revision.specification_checksum)}
        </code>
      </div>
      <pre className="mandate-code">{JSON.stringify(revision, null, 2)}</pre>
    </section>
  );
}

export function OperationalPaperSessionProfilesPage() {
  const mountedRef = useRef(true);
  const listSequenceRef = useRef(0);
  const selectedSequenceRef = useRef({ detail: 0, history: 0, revision: 0 });
  const selectedIdRef = useRef<string | null>(null);

  const [stateFilter, setStateFilter] = useState<
    "" | OperationalPaperSessionProfileState
  >("");
  const [listOffset, setListOffset] = useState(0);
  const [catalog, setCatalog] =
    useState<OperationalPaperSessionProfileList | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [current, setCurrent] =
    useState<OperationalPaperSessionProfileCurrent | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [history, setHistory] =
    useState<OperationalPaperSessionProfileRevisionList | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historicalRevision, setHistoricalRevision] =
    useState<OperationalPaperSessionProfileRevision | null>(null);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [revisionError, setRevisionError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [createDraft, setCreateDraft] = useState<IntentDraft>(emptyDraft);
  const [replaceDraft, setReplaceDraft] = useState<IntentDraft>(emptyDraft);
  const [editing, setEditing] = useState(false);
  const [createIntent, setCreateIntent] =
    useState<ConfirmedCreateIntent | null>(null);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [mutationSnapshot, setMutationSnapshot] =
    useState<MutationSnapshot | null>(null);
  const [mutationBusy, setMutationBusy] = useState<
    "create" | "replace" | "approve" | "archive" | null
  >(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [reviewRequired, setReviewRequired] = useState(false);

  const busy = mutationBusy !== null;

  const chooseProfile = useCallback((profileId: string | null) => {
    selectedIdRef.current = profileId;
    setSelectedId(profileId);
    setCurrent(null);
    setDetailError(null);
    setHistory(null);
    setHistoryOffset(0);
    setHistoricalRevision(null);
    setRevisionError(null);
    setEditing(false);
    setReviewRequired(false);
  }, []);

  const loadCatalog = useCallback(async () => {
    const sequence = ++listSequenceRef.current;
    setListLoading(true);
    setListError(null);
    try {
      const response = await apiClient.listOperationalPaperSessionProfiles({
        limit: PAGE_SIZE,
        offset: listOffset,
        state: stateFilter || undefined,
      });
      if (!mountedRef.current || sequence !== listSequenceRef.current) return;
      setCatalog(response);
      if (selectedIdRef.current === null && response.items[0])
        chooseProfile(response.items[0].profile.profile_id);
    } catch (error) {
      if (mountedRef.current && sequence === listSequenceRef.current)
        setListError(safeError(error, "Falha ao carregar perfis."));
    } finally {
      if (mountedRef.current && sequence === listSequenceRef.current)
        setListLoading(false);
    }
  }, [chooseProfile, listOffset, stateFilter]);

  const loadSelected = useCallback(
    async <T,>(
      kind: "detail" | "history" | "revision",
      profileId: string,
      request: () => Promise<T>,
      setLoading: (value: boolean) => void,
      setError: (value: string | null) => void,
      setValue: (value: T | null) => void,
      fallback: string,
    ) => {
      const sequence = ++selectedSequenceRef.current[kind];
      setLoading(true);
      setError(null);
      try {
        const response = await request();
        if (
          mountedRef.current &&
          sequence === selectedSequenceRef.current[kind] &&
          selectedIdRef.current === profileId
        )
          setValue(response);
      } catch (error) {
        if (
          mountedRef.current &&
          sequence === selectedSequenceRef.current[kind] &&
          selectedIdRef.current === profileId
        )
          setError(safeError(error, fallback));
      } finally {
        if (
          mountedRef.current &&
          sequence === selectedSequenceRef.current[kind] &&
          selectedIdRef.current === profileId
        )
          setLoading(false);
      }
    },
    [],
  );

  const loadDetail = useCallback(
    (profileId: string) =>
      loadSelected(
        "detail",
        profileId,
        () => apiClient.getOperationalPaperSessionProfile(profileId),
        setDetailLoading,
        setDetailError,
        setCurrent,
        "Falha ao carregar perfil.",
      ),
    [loadSelected],
  );

  const loadHistory = useCallback(
    (profileId: string, offset: number) =>
      loadSelected(
        "history",
        profileId,
        () =>
          apiClient.listOperationalPaperSessionProfileRevisions(profileId, {
            limit: HISTORY_PAGE_SIZE,
            offset,
          }),
        setHistoryLoading,
        setHistoryError,
        setHistory,
        "Falha ao carregar histórico.",
      ),
    [loadSelected],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      listSequenceRef.current += 1;
      selectedSequenceRef.current.detail += 1;
      selectedSequenceRef.current.history += 1;
      selectedSequenceRef.current.revision += 1;
    };
  }, []);
  useEffect(() => {
    void loadCatalog();
  }, [loadCatalog]);
  useEffect(() => {
    if (selectedId) void loadDetail(selectedId);
  }, [loadDetail, selectedId]);
  useEffect(() => {
    if (selectedId) void loadHistory(selectedId, historyOffset);
  }, [historyOffset, loadHistory, selectedId]);

  const refreshAuthoritative = useCallback(async () => {
    const profileId = selectedIdRef.current;
    await Promise.all([
      loadCatalog(),
      profileId ? loadDetail(profileId) : Promise.resolve(),
      profileId ? loadHistory(profileId, historyOffset) : Promise.resolve(),
    ]);
  }, [historyOffset, loadCatalog, loadDetail, loadHistory]);

  const inspectRevision = async (revision: number) => {
    if (!selectedId) return;
    const profileId = selectedId;
    setHistoricalRevision(null);
    await loadSelected(
      "revision",
      profileId,
      () =>
        apiClient.getOperationalPaperSessionProfileRevision(
          profileId,
          revision,
        ),
      setRevisionLoading,
      setRevisionError,
      setHistoricalRevision,
      "Falha ao carregar revisão.",
    );
  };

  const clearFeedback = () => {
    setSuccess(null);
    setMutationError(null);
    setFormError(null);
  };

  const handleConflict = async (error: unknown) => {
    setMutationError(safeError(error, "O perfil mudou no servidor."));
    setReviewRequired(true);
    setEditing(false);
    setMutationSnapshot(null);
    await refreshAuthoritative();
  };

  const updateCreateDraft = (next: IntentDraft) => {
    setCreateDraft(next);
    setCreateIntent(null);
    setFormError(null);
  };

  const prepareCreate = (event: FormEvent) => {
    event.preventDefault();
    clearFeedback();
    try {
      setCreateIntent({
        payload: {
          intent: toIntent(createDraft),
          idempotency_key: crypto.randomUUID(),
        },
      });
      setCreateDialogOpen(true);
    } catch (error) {
      setFormError(getErrorMessage(error, "Revise o formulário."));
    }
  };

  const submitCreate = async (confirmed: ConfirmedCreateIntent) => {
    setMutationBusy("create");
    setMutationError(null);
    try {
      const response = await apiClient.createOperationalPaperSessionProfile(
        confirmed.payload,
      );
      if (!mountedRef.current) return;
      setCreateIntent(null);
      setCreateDialogOpen(false);
      setCreateOpen(false);
      setCreateDraft(emptyDraft());
      setSuccess(
        `Perfil ${response.revision.specification.name} criado como DRAFT. Nenhuma sessão paper foi iniciada.`,
      );
      setStateFilter("");
      setListOffset(0);
      chooseProfile(response.profile.profile_id);
      setCurrent(response);
      await refreshAuthoritative();
    } catch (error) {
      if (!mountedRef.current) return;
      setCreateDialogOpen(false);
      if (!(error instanceof ApiError && error.status === 0))
        setCreateIntent(null);
      setMutationError(
        error instanceof ApiError && error.status === 0
          ? "Resposta incerta. Repita somente o mesmo envio confirmado."
          : safeError(error, "Falha ao criar perfil."),
      );
    } finally {
      if (mountedRef.current) setMutationBusy(null);
    }
  };

  const prepareReplace = (event: FormEvent) => {
    event.preventDefault();
    if (!current || current.profile.state !== "DRAFT" || reviewRequired) return;
    clearFeedback();
    try {
      setMutationSnapshot({
        kind: "replace",
        profileId: current.profile.profile_id,
        name: replaceDraft.name,
        payload: {
          intent: toIntent(replaceDraft),
          expected_revision: current.profile.current_revision,
          expected_record_version: current.profile.record_version,
        },
      });
    } catch (error) {
      setFormError(getErrorMessage(error, "Revise o formulário."));
    }
  };

  const confirmMutation = async () => {
    const snapshot = mutationSnapshot;
    if (!snapshot || reviewRequired) return;
    setMutationBusy(snapshot.kind);
    setMutationError(null);
    try {
      if (snapshot.kind === "replace") {
        const response =
          await apiClient.replaceOperationalPaperSessionProfileDraft(
            snapshot.profileId,
            snapshot.payload,
          );
        if (mountedRef.current) {
          setCurrent(response);
          setEditing(false);
          setSuccess(
            `Rascunho substituído pela revisão ${response.profile.current_revision}.`,
          );
        }
      } else if (snapshot.kind === "approve") {
        const payload: OperationalPaperSessionProfileApproveRequest = {
          expected_revision: snapshot.current.profile.current_revision,
          expected_checksum: snapshot.current.revision.specification_checksum,
          expected_record_version: snapshot.current.profile.record_version,
        };
        await apiClient.approveOperationalPaperSessionProfile(
          snapshot.current.profile.profile_id,
          payload,
        );
        if (mountedRef.current)
          setSuccess("Perfil aprovado. Nenhuma sessão paper foi iniciada.");
      } else {
        const payload: OperationalPaperSessionProfileArchiveRequest = {
          expected_record_version: snapshot.current.profile.record_version,
        };
        await apiClient.archiveOperationalPaperSessionProfile(
          snapshot.current.profile.profile_id,
          payload,
        );
        if (mountedRef.current)
          setSuccess("Perfil arquivado; histórico preservado.");
      }
      if (!mountedRef.current) return;
      setMutationSnapshot(null);
      await refreshAuthoritative();
    } catch (error) {
      if (!mountedRef.current) return;
      if (error instanceof ApiError && error.status === 409)
        await handleConflict(error);
      else {
        setMutationSnapshot(null);
        setMutationError(safeError(error, "Falha na ação."));
      }
    } finally {
      if (mountedRef.current) setMutationBusy(null);
    }
  };

  const changeCatalogContext = (
    nextState: "" | OperationalPaperSessionProfileState,
    nextOffset: number,
  ) => {
    chooseProfile(null);
    setCatalog(null);
    setStateFilter(nextState);
    setListOffset(nextOffset);
  };

  return (
    <div className="operational-mandates-page">
      <header className="page-heading mandate-page-heading">
        <div>
          <p className="eyebrow">Phase 7</p>
          <h1>Perfis de sessão paper</h1>
          <p>Aprovar um perfil não inicia uma sessão paper.</p>
        </div>
        <div className="mandate-heading-actions">
          <button
            className="button button--ghost"
            type="button"
            disabled={busy || listLoading}
            onClick={() => void refreshAuthoritative()}
          >
            Atualizar
          </button>
          <button
            className="button"
            type="button"
            disabled={busy}
            onClick={() => {
              clearFeedback();
              setCreateOpen((open) => !open);
            }}
          >
            {createOpen ? "Fechar criação" : "Novo perfil"}
          </button>
        </div>
      </header>

      {success && <SuccessMessage message={success} />}
      {mutationError && <InlineError message={mutationError} />}
      {reviewRequired && (
        <div className={WARNING_NOTICE} role="alert">
          <p>
            O estado autoritativo foi recarregado. Revise antes de reenviar.
          </p>
          <button
            className={COMPACT_GHOST}
            type="button"
            disabled={detailLoading || historyLoading || busy}
            onClick={() => {
              setReviewRequired(false);
              setMutationError(null);
            }}
          >
            Estado revisado
          </button>
        </div>
      )}

      {createOpen && (
        <section
          className="panel mandate-create"
          aria-labelledby="profile-create-title"
        >
          <div className="section-heading">
            <div>
              <p className="eyebrow">Intento</p>
              <h2 id="profile-create-title">Criar perfil DRAFT</h2>
            </div>
            <span>19 campos congelados</span>
          </div>
          <form onSubmit={prepareCreate}>
            <IntentFields
              draft={createDraft}
              disabled={busy}
              onChange={updateCreateDraft}
            />
            {formError && <InlineError message={formError} />}
            <div className="form-actions">
              <button className="button" type="submit" disabled={busy}>
                Revisar criação
              </button>
            </div>
          </form>
          {createIntent && !createDialogOpen && (
            <div className={WARNING_NOTICE} role="status">
              <p>Existe um envio confirmado sem resposta.</p>
              <button
                className={COMPACT_GHOST}
                type="button"
                disabled={busy}
                onClick={() => void submitCreate(createIntent)}
              >
                Repetir o mesmo envio
              </button>
            </div>
          )}
        </section>
      )}

      <section className="panel" aria-labelledby="profile-catalog-title">
        <div className="section-heading mandate-catalog-heading">
          <div>
            <p className="eyebrow">Catálogo</p>
            <h2 id="profile-catalog-title">Perfis cadastrados</h2>
          </div>
          <label>
            Estado
            <select
              value={stateFilter}
              disabled={listLoading || busy}
              onChange={(event) =>
                changeCatalogContext(
                  event.currentTarget.value as
                    "" | OperationalPaperSessionProfileState,
                  0,
                )
              }
            >
              <option value="">Todos</option>
              <option value="DRAFT">DRAFT</option>
              <option value="APPROVED">APPROVED</option>
              <option value="ARCHIVED">ARCHIVED</option>
            </select>
          </label>
        </div>
        {listLoading && !catalog ? (
          <LoadingState message="Carregando perfis…" />
        ) : listError ? (
          <InlineError message={listError} />
        ) : catalog?.items.length === 0 ? (
          <EmptyState
            title="Nenhum perfil encontrado"
            description="Altere o filtro ou crie um perfil."
          />
        ) : catalog ? (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Perfil</th>
                    <th>Estado</th>
                    <th>Concorrência</th>
                    <th>Instrumento</th>
                    <th>Criado em</th>
                    <th>Ação</th>
                  </tr>
                </thead>
                <tbody>
                  {catalog.items.map((item) => (
                    <tr key={item.profile.profile_id}>
                      <td>
                        <strong>{item.revision.specification.name}</strong>
                        <small className="mandate-cell-note">
                          <code>{item.profile.profile_id}</code>
                        </small>
                      </td>
                      <td>
                        <StateBadge state={item.profile.state} />
                      </td>
                      <td>
                        rev. {item.profile.current_revision} · versão{" "}
                        {item.profile.record_version}
                      </td>
                      <td>
                        {
                          item.revision.specification.selected_instrument
                            .base_asset
                        }
                        /
                        {
                          item.revision.specification.selected_instrument
                            .quote_asset
                        }
                      </td>
                      <td>{formatDate(item.profile.created_at)}</td>
                      <td>
                        <button
                          className={COMPACT_GHOST}
                          type="button"
                          aria-label={`Inspecionar perfil ${item.revision.specification.name}`}
                          onClick={() => chooseProfile(item.profile.profile_id)}
                        >
                          Inspecionar
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              label="Paginação do catálogo de perfis"
              limit={catalog.limit}
              offset={catalog.offset}
              total={catalog.total}
              disabled={listLoading || busy}
              onChange={(offset) => changeCatalogContext(stateFilter, offset)}
            />
          </>
        ) : null}
      </section>

      {selectedId && (
        <section
          className="panel mandate-detail"
          aria-labelledby="profile-detail-title"
        >
          {detailLoading && !current ? (
            <LoadingState message="Carregando perfil selecionado…" />
          ) : detailError ? (
            <InlineError message={detailError} />
          ) : current ? (
            <>
              <div className="section-heading mandate-detail-heading">
                <div>
                  <p className="eyebrow">Agregado administrativo atual</p>
                  <h2 id="profile-detail-title">
                    {current.revision.specification.name}
                  </h2>
                  <code className="mandate-code">
                    {current.profile.profile_id}
                  </code>
                </div>
                <StateBadge state={current.profile.state} />
              </div>
              <p className="operation-notice">
                Perfil de configuração; nenhuma sessão paper está ativa.
              </p>
              <pre className="mandate-code">
                {JSON.stringify(current.profile, null, 2)}
              </pre>
              <RevisionDetails revision={current.revision} historical={false} />
              <div className="operation-controls">
                {current.profile.state === "DRAFT" && (
                  <button
                    className="button button--ghost"
                    type="button"
                    disabled={busy || reviewRequired}
                    onClick={() => {
                      clearFeedback();
                      setReplaceDraft(fromCurrent(current));
                      setEditing(true);
                    }}
                  >
                    Substituir rascunho
                  </button>
                )}
                {current.profile.state === "DRAFT" && (
                  <button
                    className="button"
                    type="button"
                    disabled={busy || reviewRequired}
                    onClick={() =>
                      setMutationSnapshot({ kind: "approve", current })
                    }
                  >
                    Aprovar revisão atual
                  </button>
                )}
                {current.profile.state !== "ARCHIVED" && (
                  <button
                    className="button button--danger"
                    type="button"
                    disabled={busy || reviewRequired}
                    onClick={() =>
                      setMutationSnapshot({ kind: "archive", current })
                    }
                  >
                    Arquivar perfil
                  </button>
                )}
              </div>
              {editing && current.profile.state === "DRAFT" && (
                <form className="mandate-replace" onSubmit={prepareReplace}>
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Substituição completa</p>
                      <h3>Editar perfil DRAFT</h3>
                    </div>
                    <span>
                      rev. {current.profile.current_revision} · versão{" "}
                      {current.profile.record_version}
                    </span>
                  </div>
                  <IntentFields
                    draft={replaceDraft}
                    disabled={busy}
                    onChange={(next) => {
                      setReplaceDraft(next);
                      setFormError(null);
                    }}
                  />
                  {formError && <InlineError message={formError} />}
                  <div className="form-actions">
                    <button
                      className="button button--ghost"
                      type="button"
                      disabled={busy}
                      onClick={() => setEditing(false)}
                    >
                      Cancelar
                    </button>
                    <button
                      className="button"
                      type="submit"
                      disabled={busy || reviewRequired}
                    >
                      Revisar substituição
                    </button>
                  </div>
                </form>
              )}
            </>
          ) : null}
        </section>
      )}

      {selectedId && current && (
        <section className="panel" aria-labelledby="profile-history-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Histórico imutável</p>
              <h2 id="profile-history-title">Revisões do perfil</h2>
            </div>
            <span>Ordem preservada da API</span>
          </div>
          {historyLoading && !history ? (
            <LoadingState message="Carregando revisões…" />
          ) : historyError ? (
            <InlineError message={historyError} />
          ) : history?.items.length === 0 ? (
            <EmptyState
              title="Sem revisões"
              description="Nenhuma revisão retornada."
            />
          ) : history ? (
            <>
              <ol className="mandate-history-list">
                {history.items.map((revision) => (
                  <li key={revision.revision}>
                    <div>
                      <strong>
                        Revisão {revision.revision} ·{" "}
                        {revision.specification.name}
                      </strong>
                      <small>
                        {formatDate(revision.created_at)} ·{" "}
                        {checksumSummary(revision.specification_checksum)}
                      </small>
                    </div>
                    <button
                      className={COMPACT_GHOST}
                      type="button"
                      aria-label={`Inspecionar revisão histórica ${revision.revision}`}
                      disabled={revisionLoading}
                      onClick={() => void inspectRevision(revision.revision)}
                    >
                      Inspecionar revisão {revision.revision}
                    </button>
                  </li>
                ))}
              </ol>
              <Pagination
                label="Paginação do histórico do perfil"
                limit={history.limit}
                offset={history.offset}
                total={history.total}
                disabled={historyLoading || busy}
                onChange={(offset) => {
                  setHistoricalRevision(null);
                  setHistoryOffset(offset);
                }}
              />
            </>
          ) : null}
          {revisionLoading && (
            <LoadingState message="Carregando revisão exata…" />
          )}
          {revisionError && <InlineError message={revisionError} />}
          {historicalRevision && (
            <RevisionDetails revision={historicalRevision} historical />
          )}
        </section>
      )}

      <ConfirmDialog
        open={createDialogOpen && createIntent !== null}
        title="Criar perfil DRAFT?"
        description={
          createIntent
            ? `Intento exato: ${JSON.stringify(createIntent.payload.intent)}. Nenhuma sessão paper será iniciada por esta configuração.`
            : "Revise o intento completo."
        }
        confirmLabel="Criar perfil DRAFT"
        busy={mutationBusy === "create"}
        onCancel={() => {
          if (!busy) {
            setCreateDialogOpen(false);
            setCreateIntent(null);
          }
        }}
        onConfirm={() => {
          if (createIntent) void submitCreate(createIntent);
        }}
      />
      <ConfirmDialog
        open={mutationSnapshot !== null}
        title={
          mutationSnapshot?.kind === "approve"
            ? "Aprovar revisão atual?"
            : mutationSnapshot?.kind === "archive"
              ? "Arquivar perfil?"
              : "Substituir DRAFT?"
        }
        description={
          mutationSnapshot?.kind === "approve"
            ? `Revisão ${mutationSnapshot.current.profile.current_revision} · checksum ${mutationSnapshot.current.revision.specification_checksum} · versão ${mutationSnapshot.current.profile.record_version}. Aprovação administrativa para futura materialização; nenhuma sessão paper será iniciada.`
            : mutationSnapshot?.kind === "archive"
              ? `Perfil ${mutationSnapshot.current.profile.profile_id} · versão ${mutationSnapshot.current.profile.record_version}. O arquivamento é terminal para uso futuro e não exclui o histórico.`
              : mutationSnapshot?.kind === "replace"
                ? `${mutationSnapshot.name} · revisão esperada ${mutationSnapshot.payload.expected_revision} · versão esperada ${mutationSnapshot.payload.expected_record_version}.`
                : "Confirme a ação."
        }
        confirmLabel={
          mutationSnapshot?.kind === "approve"
            ? "Aprovar perfil"
            : mutationSnapshot?.kind === "archive"
              ? "Arquivar perfil"
              : "Substituir rascunho"
        }
        danger={mutationSnapshot?.kind === "archive"}
        busy={mutationBusy !== null}
        onCancel={() => {
          if (!busy) setMutationSnapshot(null);
        }}
        onConfirm={() => void confirmMutation()}
      />
    </div>
  );
}
