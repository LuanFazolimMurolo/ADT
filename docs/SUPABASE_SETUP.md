# Supabase setup — Fases 1A e 1B

Este documento descreve o fluxo versionado do Supabase no ADT. A Fase 1A
prepara o schema inicial e o cadastro controlado do primeiro administrador;
ela não implementa login, dashboard administrativo, estratégias, mercado,
backtesting, Telegram ou machine learning.

Nenhum comando deste documento foi executado contra o projeto remoto durante
a implementação da Fase 1A.

## Fonte oficial das migrations

A única fonte oficial do schema versionado é:

```text
supabase/migrations/
└── 20260729000000_phase_1a_initial_schema.sql
```

O arquivo `supabase/config.toml` configura o projeto Supabase local, mas não
substitui as migrations. O diretório `database/migrations/` não existia quando
a Fase 1A foi iniciada e não foi criado: manter duas fontes aplicáveis do schema
causaria divergência de histórico. Se documentação histórica sobre o banco for
adicionada no futuro, ela deve ficar em `docs/`, nunca em um segundo diretório
de migrations.

Toda mudança de schema deve ser feita em uma nova migration, com prefixo de
timestamp, revisada e versionada. Não edite uma migration que já tenha sido
aplicada a um ambiente compartilhado e não use o SQL Editor remoto como fluxo
normal de mudança, pois isso deixa o banco diferente do histórico do
repositório.

## Supabase CLI local

O CLI é uma dependência de desenvolvimento do `package.json` da raiz. Ele não
deve ser instalado globalmente. O CLI distribuído por npm requer Node.js 20 ou
superior e um runtime compatível com Docker para iniciar a stack local.

A dependência já está declarada no repositório. Em um clone novo, instale-a
junto com as demais dependências a partir da raiz:

```bash
npm install
npm run supabase -- --version
```

Em uma configuração inicial equivalente, a declaração local seria feita com
`npm install --save-dev supabase`; não é necessário repetir esse comando
enquanto a dependência permanecer no `package.json`.

Todos os exemplos deste projeto usam `npm run supabase -- <comando>` para
garantir que o executável local seja usado. Não execute `npm install -g
supabase`. O diretório já contém `supabase/config.toml`; portanto também não é
necessário executar `supabase init`, especialmente com `--force`.

Referências oficiais:

- [Desenvolvimento local e CLI](https://supabase.com/docs/guides/local-development)
- [Referência do Supabase CLI](https://supabase.com/docs/reference/cli/getting-started)
- [Fluxo local com migrations](https://supabase.com/docs/guides/local-development/cli-workflows)

## Localizar o Project Ref

Abra o projeto correto no dashboard do Supabase. O Project Ref é o trecho
`<project-ref>` desta URL:

```text
https://supabase.com/dashboard/project/<project-ref>
```

No dashboard, o mesmo valor fica em **Project Settings > General**, no campo
**Reference ID**. Compare o nome da organização e do projeto antes de copiá-lo.
O Project Ref identifica o destino; ele não é a senha do banco nem um access
token.

Use placeholders nos comandos documentados e nunca registre senha, access
token ou URL de conexão no Git.

## Login e link futuros

Os comandos abaixo são passos manuais futuros. Eles não fazem parte da
aplicação local concluída nesta mudança.

```bash
# Autentica o CLI de forma interativa.
npm run supabase -- login

# Associa este checkout ao projeto remoto escolhido.
npm run supabase -- link --project-ref <project-ref>

# Confere os projetos e qual deles está associado antes de qualquer escrita.
npm run supabase -- projects list
```

`login` pode armazenar o token no cofre de credenciais do sistema ou, quando
esse recurso não está disponível, sob o diretório de configuração do usuário.
Não informe o token como argumento de linha de comando e nunca o copie para o
repositório.

`link` não aplica migrations, mas define o alvo dos comandos remotos seguintes.
Deixe o CLI solicitar a senha do banco de forma interativa. Em automação,
forneça `SUPABASE_ACCESS_TOKEN` e `SUPABASE_DB_PASSWORD` somente pelo gerenciador
de segredos do ambiente.

## Aplicar migrations futuramente

Depois de revisar a migration, confirmar o projeto associado e garantir um
backup apropriado do banco remoto:

```bash
# Mostra o que ainda seria aplicado, sem alterar o banco.
npm run supabase -- db push --dry-run

# Aplica somente migrations pendentes ao projeto associado.
npm run supabase -- db push
```

O `db push` registra as migrations aplicadas no schema
`supabase_migrations`. Execute o `--dry-run` primeiro, confira novamente o alvo
associado e só então autorize a escrita remota. Não use `--include-seed` em
produção.

Para testar uma reconstrução local e descartável, com a stack local iniciada,
o comando abaixo reaplica as migrations desde o início:

```bash
npm run supabase -- start
npm run supabase -- db reset
```

`db reset` apaga os dados da instância local antes de reconstruí-la. Não o
execute se houver dados locais que precisem ser preservados. Encerre a stack
sem apagar os dados com:

```bash
npm run supabase -- stop
```

## Comandos destrutivos ou de alto risco

- `db reset` é destrutivo mesmo no ambiente local: ele apaga os dados locais.
- `db reset --linked` apaga o schema e os dados do projeto remoto associado.
  **Nunca o execute contra o projeto remoto do ADT.**
- `db push` altera o banco remoto. A operação deve ser precedida por revisão,
  backup, conferência do link e `db push --dry-run`; uma migration pode conter
  SQL destrutivo.
- `migration repair` altera o histórico de migrations. Não o use sem
  investigação e aprovação explícita.
- Comandos SQL como `DROP`, `TRUNCATE` e alterações que removam colunas ou
  tabelas exigem plano de migração e backup; não devem ser improvisados no
  dashboard remoto.

O simples fato de um comando estar documentado aqui não autoriza sua execução.
Na Fase 1A não foram executados `login`, `link`, `db push`, `db reset` nem
qualquer conexão com o Supabase remoto.

## Localizar o UUID do administrador

O administrador precisa existir primeiro no Supabase Auth:

1. No dashboard do projeto correto, abra **Authentication > Users**.
2. Crie o único usuário administrador, se ele ainda não existir. Não habilite
   cadastro público para isso.
3. Abra esse usuário e copie o campo **User UID** (o UUID do registro em
   `auth.users`).
4. Confira se o valor tem formato UUID antes de usá-lo como
   `ADT_ADMIN_USER_ID`.

Não use o e-mail como identificador, não invente um UUID e não exponha o User
UID no frontend. A chave estrangeira de `app_admins.user_id` exige que o usuário
já exista em `auth.users`.

## Executar o bootstrap do administrador

Pré-requisitos:

- a migration inicial já foi aplicada ao banco escolhido;
- o usuário já existe em `auth.users`;
- as dependências do backend, incluindo o driver PostgreSQL, estão instaladas
  no ambiente virtual local;
- `ADT_ADMIN_USER_ID` contém o UUID validado do usuário;
- `SUPABASE_DATABASE_URL` contém a URL de conexão PostgreSQL do mesmo projeto.

Em um ambiente virtual já criado, sincronize primeiro as dependências:

```bash
cd services/backend
.venv/bin/python -m pip install -e '.[dev]'
cd ../..
```

O script usa o driver PostgreSQL `psycopg` diretamente. A variável deve conter
uma URL PostgreSQL compatível com esse driver:

```dotenv
SUPABASE_DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DATABASE
```

Não use uma URL do SQLAlchemy/asyncpg, como
`postgresql+asyncpg://...`, nesse script.

Obtenha a URL de conexão na opção **Connect** do dashboard. Ela contém
credenciais e pertence somente ao backend/à operação. Injete as duas variáveis
na sessão de shell ou em um gerenciador de segredos; não coloque valores reais
no comando, na documentação ou em arquivos versionados.

O script não carrega arquivos `.env` automaticamente. Se as variáveis estiverem
em `services/backend/.env`, exporte-as explicitamente a partir da raiz:

```bash
set -a
source services/backend/.env
set +a
services/backend/.venv/bin/python scripts/bootstrap_admin.py
```

O script:

- valida o UUID antes de conectar;
- usa uma transação;
- insere o usuário em `app_admins` de forma idempotente;
- não duplica o registro quando executado novamente;
- não imprime a URL do banco nem credenciais.

É seguro repetir o bootstrap para o mesmo UUID. Uma falha de chave estrangeira
indica, em geral, que o UUID não existe em `auth.users` no banco apontado pela
URL. Não contorne essa validação inserindo dados diretamente nas tabelas do
Supabase Auth.

## Separação de variáveis

A Fase 1C adiciona autenticação Supabase ao frontend. Somente valores
expressamente públicos podem receber o prefixo `VITE_`:

| Contexto | Variável | Regra |
| --- | --- | --- |
| Frontend | `VITE_ADT_API_URL` | URL pública da API do ADT. |
| Frontend | `VITE_SUPABASE_URL` | URL pública do projeto Supabase. |
| Frontend | `VITE_SUPABASE_PUBLISHABLE_KEY` | Chave publicável; a segurança continua dependendo de RLS e do backend. |
| Frontend legado | `VITE_SUPABASE_ANON_KEY` | Somente se o projeto ainda usar a chave `anon`; não é uma chave administrativa. |
| Backend/bootstrap | `ADT_ADMIN_USER_ID` | UUID do administrador inicial; não expor na UI pública. |
| Backend | `SUPABASE_URL` | URL pública do projeto usada para derivar issuer e JWKS. |
| Backend | `SUPABASE_PUBLISHABLE_KEY` | Chave publicável do projeto; não concede acesso administrativo. |
| Backend/bootstrap | `SUPABASE_DATABASE_URL` | Segredo com acesso direto ao PostgreSQL; nunca usar no frontend. |
| Operação/CLI | `SUPABASE_ACCESS_TOKEN` | Token secreto da conta para automação do CLI. |
| Operação/CLI | `SUPABASE_DB_PASSWORD` | Senha secreta usada por comandos remotos do CLI. |

Uma chave legada `anon` ou a chave publicável pode aparecer no frontend porque
foi projetada para uso público, mas nunca concede autorização por si só: RLS e
as políticas do banco são obrigatórias. Chaves `secret`/`service_role`, URL do
banco, senha, access token e qualquer credencial administrativa são
exclusivamente de backend/operação e nunca podem ter prefixo `VITE_`.

Por regra do ADT, novas variáveis próprias do backend usam o prefixo `ADT_`.
Os nomes padronizados `SUPABASE_*` documentados acima são exceções da integração
com Supabase e têm escopo explícito de frontend, backend ou operação.

## Redirect URLs da recuperação de senha

A recuperação implementada na Fase 1C envia o usuário para
`/admin/reset-password`. No dashboard do projeto correto, abra
**Authentication > URL Configuration > Redirect URLs** e adicione manualmente:

```text
http://localhost:5173/admin/reset-password
https://SEU-DOMINIO-DE-PRODUCAO/admin/reset-password
```

Substitua o placeholder pelo domínio HTTPS real usado pelo frontend. Não use
curingas mais amplos que o necessário. A lista de Redirect URLs não é alterada
por código, migration ou comando durante a implementação local.

## Autenticação do backend na Fase 1B

O backend valida tokens de usuário localmente com as chaves públicas
assimétricas disponíveis em:

```text
<SUPABASE_URL>/auth/v1/.well-known/jwks.json
```

O projeto deve usar uma chave de assinatura assimétrica compatível (`ES256` ou
`RS256`). O backend valida assinatura, issuer
`<SUPABASE_URL>/auth/v1`, audience `authenticated`, expiração e o UUID de
`sub`. As chaves são mantidas somente em cache de memória por tempo limitado e
um `kid` desconhecido força uma atualização controlada do cache.

Não configure `SUPABASE_SECRET_KEY` no serviço e não use o JWT secret legado
para validação local. A autorização administrativa também não vem de
`app_metadata` ou `user_metadata`: depois da autenticação, o backend consulta
`public.app_admins` pelo UUID verificado.

Referências oficiais:

- [JWTs do Supabase e verificação por JWKS](https://supabase.com/docs/guides/auth/jwts)
- [Chaves de assinatura JWT](https://supabase.com/docs/guides/auth/signing-keys)
- [Campos obrigatórios dos JWTs](https://supabase.com/docs/guides/auth/jwt-fields)
