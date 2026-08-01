# Plugins de estratégia determinísticos

A Fase 3C introduz um registry local e explícito de plugins de estratégia. Plugins não são
carregados por caminho de módulo, string de importação ou código fornecido pelo usuário.
Somente factories registradas no código podem ser resolvidas.

## Identidade e versões

Cada plugin declara `StrategyPluginDescriptor` com:

- nome e versão seguros;
- versão do schema do plugin;
- versão do ciclo de vida;
- descrição não vazia;
- schema canônico de parâmetros;
- capacidades de indicadores exigidas.

As versões suportadas inicialmente são schema `1` e ciclo de vida `1`. Versões futuras
são rejeitadas de forma explícita.

## Parâmetros

Os tipos aceitos são booleano, inteiro, `Decimal` e string. Não existe coerção implícita e
`float` é proibido. Parâmetros desconhecidos, ausentes ou fora dos limites são rejeitados.
A normalização gera uma tupla imutável ordenada pelo nome e, a partir dela, o mesmo
`StrategyDescriptor` consumido pelo motor de backtesting.

## Compatibilidade com indicadores

Requisitos de indicadores usam identidade exata de nome, versão e schema. Parâmetros do
indicador não participam da capacidade porque são definidos pela instância da estratégia.
O registry bloqueia a construção quando uma capacidade obrigatória não está disponível.

## Ciclo de vida

Cada chamada de `StrategyPluginRegistry.build()` deve retornar uma instância nova. A
instância implementa o ciclo de vida já usado pelo motor:

1. `on_start`;
2. `on_candle` para cada candle fechado;
3. `on_fill` após fills;
4. `on_end`.

O motor continua sendo o único componente que altera ordens, portfolio, risco e ledger.

## Exemplos incluídos

- `no-op@1`: estratégia técnica sem ordens;
- `ema-cross-example@1`: exemplo não financeiro que observa duas EMAs e somente emite
  intenções depois de uma mudança de relação já confirmada por candles fechados.

Esses exemplos existem para validar os contratos e não representam recomendação financeira.

## Definições reutilizáveis e CRUD

A camada `StrategyDefinitionService` separa o plugin aprovado de uma configuração
persistível criada por administrador. Cada definição registra a identidade e as versões do
plugin, um documento de parâmetros com tipos explícitos, SHA-256, revisão otimista e estado
`ACTIVE` ou `ARCHIVED`.

`Decimal` é armazenado como texto canônico dentro de uma entrada marcada como `decimal`;
portanto, não existe ambiguidade com parâmetros `string`, nenhum `float` é introduzido pelo
JSON e a representação independe do contexto Decimal global. Criações e leituras validam o
checksum, as versões do plugin, as capacidades de indicadores, o schema de parâmetros e as
invariantes cruzadas da factory antes de aceitar a definição.

O serviço define operações limitadas de listagem, leitura, criação, substituição e
arquivamento. O arquivamento é uma transição sem retorno e bloqueia execução ou alteração.
A persistência PostgreSQL usa revisão otimista e uma transição irreversível para `ARCHIVED`. A API administrativa expõe listagem paginada, leitura, criação, substituição e arquivamento. Cada parâmetro de entrada carrega `kind` e `value`; valores `decimal` atravessam o JSON como texto base 10 e são convertidos para `Decimal` somente após validação.

A tabela é backend-only: RLS é habilitada, os papéis da Data API não recebem privilégios, exclusões são bloqueadas e toda atualização deve incrementar a revisão exatamente uma vez. O registry continua sendo a única origem de código executável; o banco nunca armazena caminhos de importação ou código de usuário.
