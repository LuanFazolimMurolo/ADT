# ADT Web

Frontend público e painel administrativo privado do ADT. O projeto usa React,
TypeScript estrito, Vite, React Router e o cliente oficial do Supabase.

Requer Node.js 20 ou mais recente.

## Limites de segurança

- O site público continua em `/`, sem cadastro e sem link de login.
- A autenticação administrativa começa somente em `/admin/login`.
- Supabase Auth cria, persiste e renova a sessão; o frontend não cria tokens em
  `localStorage`.
- Uma sessão do Supabase não concede acesso por si só. O frontend exige a
  confirmação de `GET /api/v1/admin/me` pelo backend.
- Toda leitura ou escrita administrativa usa o FastAPI com
  `Authorization: Bearer <access-token>`.
- Respostas 401/403 encerram a sessão local; mutações nunca são repetidas
  automaticamente.
- Cálculos financeiros, autorização administrativa e persistência permanecem
  no backend/PostgreSQL.
- Nunca configure `SUPABASE_SECRET_KEY`, `SUPABASE_DATABASE_URL`, senha do banco
  ou tokens administrativos em variáveis `VITE_*`.

## Configuração

Crie `apps/web/.env.local` (ignorado pelo Git):

```dotenv
VITE_ADT_API_URL=http://localhost:8000
VITE_SUPABASE_URL=https://PROJECT_REF.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_REPLACE_ME
```

As três variáveis são validadas na inicialização. Quando alguma estiver ausente
ou uma URL for inválida, a aplicação mostra apenas os nomes que precisam ser
corrigidos, nunca valores recebidos.

## Executar frontend e backend

Terminal 1, a partir da raiz, com as variáveis do backend exportadas:

```bash
set -a
source .env
set +a
cd services/backend
.venv/bin/python -m app.main
```

Terminal 2:

```bash
cd apps/web
npm install
npm run dev
```

Abra `http://localhost:5173` para o site público ou
`http://localhost:5173/admin/login` para o acesso administrativo.

O backend deve permitir `http://localhost:5173` em `ADT_CORS_ORIGINS`.

## Redirect URLs do Supabase

No projeto correto, configure manualmente em **Authentication > URL
Configuration > Redirect URLs**:

```text
http://localhost:5173/admin/reset-password
https://SEU-DOMINIO-DE-PRODUCAO/admin/reset-password
```

A segunda URL deve usar o domínio HTTPS real do frontend em produção. Esta
configuração é manual; o frontend não altera o painel remoto.

## Rotas

- `/`: página pública;
- `/admin/login`: autenticação;
- `/admin/forgot-password`: solicitação de recuperação;
- `/admin/reset-password`: definição da nova senha;
- `/admin`: dashboard privado;
- `/admin/simulations`: histórico e criação;
- `/admin/simulations/:simulationId`: ledger e encerramento;
- `/admin/settings`: configurações não secretas.

## Qualidade

```bash
npm run generate:api
npm run lint
npm run typecheck
npm run typecheck:e2e
npm test -- --run --silent
npm run build
```

`src/types/openapi.generated.ts` é gerado dos schemas Pydantic/OpenAPI. Os
aliases usados pela aplicação ficam em `src/types/api.ts`; não edite o arquivo
gerado manualmente.

Todos os testes usam valores fictícios e não acessam serviços remotos.

## Testes end-to-end locais

A suíte Playwright inicia somente o Vite em `127.0.0.1`. O SDK oficial do
Supabase continua ativo no navegador, mas todas as chamadas de Supabase Auth e
FastAPI são interceptadas em origens loopback controladas. Qualquer tentativa
de acessar outra origem faz o teste falhar. As variáveis E2E são injetadas pelo
`playwright.config.ts`, têm valores fictícios e substituem qualquer
`apps/web/.env.local`.

Instale o Chromium gerenciado pelo Playwright uma vez:

```bash
cd apps/web
npm run test:e2e:install
```

Execute a suíte:

```bash
npm run test:e2e
```

Modos opcionais:

```bash
npm run test:e2e:headed
npm run test:e2e:ui
```

Os relatórios e artefatos locais ficam em `playwright-report/` e
`test-results/`, ambos ignorados pelo Git. Estes E2E validam a integração do
navegador, SDK e contratos HTTP; regras PostgreSQL, RLS, triggers e transações
continuam cobertas pelos testes de integração do backend.

A suíte contém 27 cenários de público, login, autorização, restauração/refresh
da sessão, logout, recuperação/redefinição, dashboard, simulações, movimentos,
configurações, falhas 503, acessibilidade e responsividade.

## Deploy do frontend

O bundle contém somente as três variáveis públicas `VITE_*`; qualquer variável
incluída pelo Vite deve ser tratada como publicamente legível. Source maps de
produção permanecem desativados.

O host estático/CDN deve adicionar a CSP do frontend, pois headers retornados
pelo FastAPI protegem somente a API. Uma base adequada, ajustando as duas
origens reais, é:

```text
default-src 'self';
script-src 'self';
style-src 'self';
img-src 'self' data:;
font-src 'self';
connect-src 'self' https://API-ADT https://PROJECT_REF.supabase.co;
base-uri 'self';
form-action 'self';
frame-ancestors 'none';
```

Configure também HSTS no domínio HTTPS final. Rate limiting de login deve usar
os controles do Supabase Auth; limites distribuídos da API devem ficar no
gateway/reverse proxy, não em memória dentro de uma única réplica.

O checklist completo está em
[`docs/PHASE1_HOMOLOGATION.md`](../../docs/PHASE1_HOMOLOGATION.md).

## Dashboard de performance do paper trading

Após o login administrativo, abra
`http://localhost:5173/admin/paper-trading`. A página usa exclusivamente
`GET /api/v1/admin/paper-trading/dashboard?page=1&page_size=20`, atualiza a
cada 30 segundos e permite uma atualização manual. Os totais pertencem apenas à
página carregada e são exibidos como valores nominais porque sessões diferentes
podem usar ativos de cotação diferentes.

O frontend não cria sessões, não executa estratégias e não altera o runner. A
comparação de até duas sessões ocorre somente no navegador sobre a resposta já
autorizada pelo backend. Depois de qualquer mudança nos schemas Pydantic, execute
`npm run generate:api` e mantenha `npm run check:api` no gate.
