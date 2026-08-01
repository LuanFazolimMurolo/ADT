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

## Espaços finitos de parâmetros (Fase 4-01)

`ParameterSearchService` reutiliza o descriptor e o registry da Fase 3C. Cada
parâmetro pesquisável precisa existir no descriptor e fornece uma sequência
finita e explícita de valores. Parâmetros fixos passam pela mesma normalização,
não podem também ser pesquisáveis e aparecem em todas as combinações.

Os únicos escalares aceitos continuam sendo `bool`, `int`, `Decimal` finito e
`str` não vazia. Não há coerção, e `float`, `None`, geradores, valores aninhados
e objetos mutáveis são rejeitados. Valores são normalizados e ordenados de forma
canônica; duplicatas surgidas depois da normalização causam erro explícito.
Inteiros canônicos são limitados a 128 dígitos de magnitude e validados por
limites exatos antes de qualquer conversão para texto. O texto de `Decimal`
continua limitado a 128 caracteres, mas seu tamanho final é pré-calculado antes
de criar coeficiente ou preenchimento de zeros; expoentes extremos são rejeitados
sem alocação proporcional ao expoente.

A ordem dos parâmetros vem do `StrategyPluginDescriptor`. Cada configuração
completa volta a passar por `StrategyPluginRegistry.build()`, preservando a
factory como fronteira final para invariantes cruzadas como
`fast_period < slow_period`. A política inicial é `REJECT_SPACE`: a primeira
combinação inválida rejeita o espaço inteiro e informa seu índice e a regra da
factory; nenhuma falha é filtrada silenciosamente.

A cardinalidade é calculada antes da materialização, com limite padrão de 1.000
e teto absoluto de 100.000 combinações. Esta entrega apenas produz contratos
imutáveis prontos para o futuro planner e não executa backtests.

`FixedParameter`, `SearchParameter` e `ParameterSearchSpace` validam seus
próprios invariantes mesmo em construção direta: tipos exatos, nomes e ordem
canônicos, dimensões não vazias, ausência de duplicação ou sobreposição, schema,
política, hashes, limites e produto exato da cardinalidade. `expand()` repete a
validação estrutural, de checksum e de ID antes de resolver ou chamar qualquer
factory, protegendo inclusive contra objetos congelados alterados por mecanismos
de baixo nível.
