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
npm run lint
npm run typecheck
npm test -- --run
npm run build
```

Todos os testes usam mocks de Supabase e FastAPI e não acessam serviços remotos.
