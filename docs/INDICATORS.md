# Indicadores técnicos determinísticos

A Fase 3C introduz uma biblioteca local de indicadores para pesquisa e backtesting. Os
indicadores não representam recomendação financeira e não executam ordens.

## Contratos do núcleo

- Entradas e saídas usam `Decimal`; `float` não participa dos cálculos.
- Toda série é imutável, estritamente cronológica e usa timestamps UTC.
- O timestamp de uma série criada a partir de candles é o `close_time`, instante em que o
  conjunto OHLCV está integralmente conhecido.
- `warmup_points` informa quantos pontos iniciais permanecem indisponíveis como `None`.
- `calculate_as_of()` materializa somente o prefixo observado antes de chamar o indicador.
- Views limitadas rejeitam leitura posterior ao índice `as_of_index`.
- O contexto Decimal interno possui precisão e arredondamento fixos, independentemente do
  contexto global do processo.

## Identidade e compatibilidade

Cada indicador possui um `IndicatorDescriptor` com:

- nome seguro;
- versão da implementação;
- versão do schema;
- parâmetros canônicos, imutáveis e ordenados pelo nome.

A versão inicial do schema é `1`. Versões futuras desconhecidas são rejeitadas em vez de
serem interpretadas como uma versão antiga.

## EMA

`ExponentialMovingAverage(period)` usa a média aritmética dos primeiros `period` valores
como semente. Os `period - 1` pontos anteriores são aquecimento. Depois da semente, aplica:

```text
alpha = 2 / (period + 1)
ema_atual = ema_anterior + alpha * (valor_atual - ema_anterior)
```

## RSI

`RelativeStrengthIndex(period)` usa o alisamento de Wilder. São necessários `period`
movimentos, portanto os primeiros `period` pontos são aquecimento. Casos-limite são
explícitos:

- somente ganhos: `100`;
- somente perdas: `0`;
- nenhuma variação: `50`.

## Séries de candles e ATR

`CandleSeries` aceita somente candles fechados, cronológicos e pertencentes ao mesmo
instrumento e timeframe. O acesso limitado e `calculate_candles_as_of()` materializam
somente o prefixo observado, impedindo leitura futura de OHLCV.

`TrueRange` usa `high - low` no primeiro candle. Nos seguintes, usa o maior valor entre:

- `high - low`;
- `abs(high - close_anterior)`;
- `abs(low - close_anterior)`.

`AverageTrueRange(period)` usa a média dos primeiros `period` true ranges como semente e,
depois, o alisamento de Wilder. Os primeiros `period - 1` pontos são aquecimento.

## Indicadores compostos

`IndicatorBundle` agrupa saídas nomeadas que compartilham exatamente os mesmos timestamps.
O bundle rejeita nomes duplicados, componentes desalinhados e acesso a componentes
inexistentes. `calculate_composite_as_of()` aplica a mesma proteção por prefixo usada nos
indicadores de saída única.

## MACD

`MovingAverageConvergenceDivergence(fast_period, slow_period, signal_period)` calcula:

- linha MACD: EMA rápida menos EMA lenta;
- sinal: EMA da linha MACD;
- histograma: linha MACD menos sinal.

As EMAs usam a mesma semente por média simples da implementação `ExponentialMovingAverage`.
A linha MACD fica disponível em `slow_period - 1`; sinal e histograma ficam disponíveis em
`slow_period + signal_period - 2`.

## Bandas de Bollinger

`BollingerBands(period, standard_deviations)` usa média aritmética móvel e desvio-padrão
populacional da janela. O multiplicador deve ser um `Decimal` positivo e finito. As três
saídas são `middle`, `upper` e `lower`, todas disponíveis depois de `period - 1` pontos de
aquecimento.
