# Homologação da Fase 1

Este é o gate operacional para encerrar a Fase 1 do ADT. A implementação e a
infraestrutura de testes automatizados locais das Fases 1A–1D estão prontas. A
fase permanece em **homologação** até que o gate completo esteja verde no
commit candidato, as migrations revisadas sejam aplicadas ao projeto Supabase
escolhido e todos os itens manuais deste documento sejam evidenciados.

Nenhum comando remoto, migration, bootstrap, commit ou push foi executado
durante a Fase 1D.

## Arquitetura homologada localmente

```text
Browser React/Vite
  ├── Supabase Auth: identidade, sessão e recuperação de senha
  └── FastAPI /api/v1/admin/*: única superfície administrativa
        ├── JWT assimétrico validado por issuer, audience, expiração e JWKS
        ├── app_admins: autorização administrativa no banco
        └── PostgreSQL: transações, ledger e configurações

Data API do Supabase
  ├── anon/authenticated: SELECT somente na view pública
  └── tabelas administrativas: sem privilégios diretos
```

Uma sessão válida do Supabase identifica o usuário, mas não concede acesso
administrativo. O backend consulta `app_admins` depois de validar o token e é o
único escritor das tabelas administrativas.

Há duas topologias de teste, ambas sem credenciais reais:

1. O Playwright inicia o Vite, usa o SDK real do Supabase no browser e intercepta
   Auth e FastAPI em origens exclusivamente loopback. Qualquer requisição de
   rede não prevista falha o teste.
2. O Pytest cria PostgreSQL temporário, aplica os arquivos SQL ao banco
   descartável e exercita JWT local assinado, JWKS controlado, FastAPI,
   `app_admins`, serviços e transações reais.

Aplicar o SQL no cluster temporário faz parte do teste de contrato local; isso
não executa `supabase db push`, não altera o histórico do CLI e não alcança o
projeto remoto.

## Gate automatizado local

Use Node.js 20 ou superior e um ambiente virtual Python do projeto. Não instale
dependências globalmente.

### Backend

```bash
cd services/backend
.venv/bin/ruff check app scripts tests
.venv/bin/ruff format --check app scripts tests
.venv/bin/mypy app scripts
.venv/bin/pytest
```

Os testes PostgreSQL usam binários locais (`initdb`, `pg_ctl` e `postgres`) e um
cluster temporário. Em ambientes isolados, o processo de teste precisa ter
permissão para criar o socket Unix temporário.

### Frontend

```bash
cd apps/web
npm ci
npm run generate:api
npm run check:api
npm run lint
npm run typecheck
npm run typecheck:e2e
npm test -- --run
npm run build
npx playwright test --project=chromium
```

Na primeira execução local, instale o navegador gerenciado pelo Playwright:

```bash
cd apps/web
npx playwright install chromium
```

`generate:api` exporta o OpenAPI diretamente do código FastAPI e regenera os
tipos TypeScript. `check:api` falha quando o arquivo versionado estiver
divergente.

### Projeto

```bash
git diff --check
git status --short
git check-ignore -v .env .env.local apps/web/.env.local services/backend/.env
```

Inspecione somente arquivos versionados e novos na varredura de segredos. Não
inclua `.git`, dependências, builds, ambientes virtuais ou arquivos locais
ignorados.

## Checklist antes de alterar o ambiente remoto

- [ ] Confirmar organização, nome e Project Ref do Supabase.
- [ ] Confirmar que o destino é homologação, não produção com capital real.
- [ ] Criar ou validar backup e plano de reversão do banco.
- [ ] Revisar as duas migrations em `supabase/migrations/`, em ordem.
- [ ] Antes da 1D, verificar se há chaves legadas incompatíveis. A consulta deve
      retornar zero linhas; revise e saneie qualquer chave retornada antes de
      repetir a migration:

```sql
select key
from public.system_settings
where key !~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$';
```

      A migration interrompe de forma segura e com mensagem explícita se essa
      pré-condição não for atendida; ela não altera automaticamente dados
      legados.
- [ ] Identificar consumidores externos da Data API; depois da hardening 1D,
      tabelas base deixam de ser públicas e somente a view permanece legível.
- [ ] Confirmar que o projeto usa chave assimétrica JWT `ES256` ou `RS256`.
- [ ] Confirmar que nenhuma chave `service_role`, secret key, senha ou URL do
      banco está em arquivo versionado, bundle ou variável `VITE_*`.
- [ ] Fazer o dry-run e guardar a saída como evidência:

```bash
npm run supabase -- login
npm run supabase -- link --project-ref <project-ref>
npm run supabase -- projects list
npm run supabase -- db push --dry-run
```

Esses comandos são instruções para o operador autorizado; não foram executados
pela implementação. Confira o alvo associado novamente antes de qualquer
escrita. Não use `db reset --linked`.

## Aplicação e bootstrap manuais

Somente depois da aprovação do dry-run:

- [ ] Aplicar migrations pendentes com `npm run supabase -- db push`.
- [ ] Verificar que a instalação nova recebeu 1A e 1D; uma instalação que já
      tinha 1A deve receber apenas 1D.
- [ ] Criar o administrador em **Authentication > Users**, sem cadastro público.
- [ ] Copiar o UUID real do usuário para `ADT_ADMIN_USER_ID`.
- [ ] Configurar `SUPABASE_DATABASE_URL` somente no backend/ambiente operacional,
      com TLS obrigatório.
- [ ] Executar `scripts/bootstrap_admin.py` uma vez e repetir para comprovar
      idempotência.
- [ ] Confirmar que nenhuma saída expôs URL, senha ou token.

As instruções completas e os riscos de cada comando estão em
[`SUPABASE_SETUP.md`](./SUPABASE_SETUP.md).

## Configuração de deploy

### Supabase Auth

- [ ] Cadastrar exatamente a URL local necessária durante homologação:
      `http://localhost:5173/admin/reset-password`.
- [ ] Cadastrar exatamente a URL HTTPS de produção:
      `https://SEU-DOMINIO/admin/reset-password`.
- [ ] Não usar curingas amplos em Redirect URLs.
- [ ] Desabilitar cadastro público se o produto continuar com administrador
      único controlado.

### Backend

- [ ] Definir `ADT_ENVIRONMENT=production`.
- [ ] Definir `SUPABASE_URL` com HTTPS e o projeto correto.
- [ ] Definir `SUPABASE_PUBLISHABLE_KEY` sem usar uma secret key.
- [ ] Definir `SUPABASE_DATABASE_URL` com `sslmode=require`,
      `verify-ca` ou `verify-full`.
- [ ] Definir `ADT_CORS_ORIGINS` apenas com origens HTTPS exatas, sem localhost,
      caminhos, curingas ou credenciais embutidas.
- [ ] Manter documentação OpenAPI desabilitada em produção.
- [ ] Validar que o proxy preserve/retorne `X-Request-ID` e HTTPS.
- [ ] Configurar limites de taxa no gateway para login, recuperação e API. O
      backend limita corpo e token, mas rate limiting distribuído não pertence
      ao processo individual.

### Frontend

- [ ] Definir `VITE_ADT_API_URL`, `VITE_SUPABASE_URL` e
      `VITE_SUPABASE_PUBLISHABLE_KEY` com os valores públicos corretos.
- [ ] Conferir que nenhuma variável secreta tem prefixo `VITE_`.
- [ ] Servir exclusivamente por HTTPS.
- [ ] Manter source maps de produção não publicados; a configuração atual não
      os gera no build padrão.
- [ ] Aplicar CSP no host/CDN compatível com as origens exatas do frontend,
      FastAPI e Supabase. O CSP defensivo do backend protege suas respostas,
      mas não substitui o header servido no documento HTML.
- [ ] Aplicar HSTS no ponto que termina TLS e verificar o header do backend em
      produção.

## Fluxos funcionais manuais

Registre resultado, horário, ambiente e `X-Request-ID` de falhas.

### Público e autenticação

- [ ] A página pública carrega sem sessão e mostra o estado correto com e sem
      simulação ativa.
- [ ] `/admin` sem sessão redireciona para o login.
- [ ] Credenciais inválidas mostram mensagem segura e não revelam detalhes.
- [ ] Login do administrador chama `/api/v1/admin/me` e abre o destino interno
      solicitado.
- [ ] Um parâmetro de destino externo não causa open redirect.
- [ ] Usuário autenticado ausente de `app_admins` recebe 403 e perde o acesso
      administrativo local.
- [ ] Token inválido ou expirado recebe 401; uma leitura tenta uma única
      renovação e encerra a sessão se ela falhar.
- [ ] Recarregar o browser restaura uma sessão válida sem piscar conteúdo
      administrativo para usuário não autorizado.
- [ ] Logout remove a sessão local mesmo se o serviço de Auth estiver
      indisponível.
- [ ] Recuperação envia somente para a Redirect URL cadastrada.
- [ ] Link válido permite definir nova senha.
- [ ] Link expirado mostra estado seguro, sem token na tela ou logs.

### Administração e ledger

- [ ] Dashboard mostra loading, vazio, dados e falha de backend.
- [ ] Criar simulação gera `INITIAL_CAPITAL` na mesma transação.
- [ ] Criar uma segunda simulação ativa retorna 409.
- [ ] Depósito aumenta o saldo sem alterar P/L.
- [ ] Retirada diminui o saldo sem alterar P/L.
- [ ] Retirada acima do saldo é recusada sem movimento parcial.
- [ ] Ajuste positivo e negativo atualiza P/L separadamente dos fluxos.
- [ ] Duplo clique não duplica uma mutação.
- [ ] Concluir uma simulação é irreversível e impede novos movimentos.
- [ ] Cancelar uma simulação é irreversível e impede novos movimentos.
- [ ] O ledger não aceita update nem delete.
- [ ] Datas são exibidas a partir de ISO 8601 e UUIDs permanecem strings.
- [ ] Valores monetários mantêm precisão decimal; número JSON, notação
      científica, mais de oito casas e valor absoluto a partir de `1e12` são
      rejeitados.
- [ ] Paginação, empty states e atualização após mutação estão corretos.
- [ ] Configurações não secretas podem ser listadas e atualizadas.
- [ ] Confirmações aceitam teclado, prendem o foco e devolvem o foco ao fechar.
- [ ] Layout continua utilizável nas larguras móvel e desktop suportadas.

## Checklist de segurança e observabilidade

- [ ] CORS aceita somente origens configuradas e permite apenas
      `GET`, `POST`, `PATCH` e `OPTIONS`, com `Authorization` e headers
      necessários.
- [ ] Origem não permitida não recebe headers CORS.
- [ ] Produção rejeita localhost e origem sem HTTPS na configuração.
- [ ] Respostas têm `X-Content-Type-Options`, `Referrer-Policy`,
      `Content-Security-Policy`, `Permissions-Policy` e proteção contra framing.
- [ ] Respostas administrativas não são armazenadas por caches compartilhados.
- [ ] Erros 400, 401, 403, 404, 409, 413, 422, 500 e 503 têm mensagem segura e
      `X-Request-ID`.
- [ ] Logs são JSON estruturado, correlacionáveis e não contêm bearer token,
      senha, chave, URL do banco, corpo sensível ou stack trace na resposta.
- [ ] JWT rejeita algoritmo simétrico, issuer/audience incorretos, `sub` não
      UUID, token expirado e `kid` desconhecido.
- [ ] Cache JWKS tem tempo limitado e atualização controlada.
- [ ] Data API nega CRUD direto nas tabelas base a `anon`, `authenticated` e
      `service_role`.
- [ ] A view pública permite somente leitura e não expõe dados administrativos.
- [ ] Queries administrativas continuam parametrizadas; nenhum valor de
      usuário é interpolado em SQL.
- [ ] A sessão usa o armazenamento padrão do SDK Supabase; CSP, ausência de XSS
      e HTTPS são obrigatórios porque bearer tokens ficam acessíveis ao
      JavaScript do mesmo origin.
- [ ] CSRF não depende de cookie implícito: a API exige bearer token no header e
      CORS exato. Manter qualquer autenticação futura por cookie fora dessa
      premissa até nova revisão.
- [ ] Auditorias de dependências foram revisadas e riscos remanescentes têm
      responsável e prazo.

## Health checks

- [ ] `GET /health` responde quando o processo está vivo sem consultar o banco.
- [ ] `GET /health/database` responde `200` somente com PostgreSQL disponível e
      `503` de forma sanitizada quando indisponível.
- [ ] `GET /health/readiness` reflete a prontidão conjunta da aplicação e banco.
- [ ] `GET /api/v1/system/status` expõe somente metadados públicos e informa
      integrações opcionais ainda não configuradas.
- [ ] Nenhuma resposta revela host, usuário, senha, DSN ou topologia interna.

## Riscos conhecidos na revisão de 29/07/2026

- `npm audit --omit=dev` ainda reporta duas entradas de severidade alta
  (`react-router-dom` e sua dependência `react-router`) para o advisory
  [GHSA-qwww-vcr4-c8h2](https://github.com/advisories/GHSA-qwww-vcr4-c8h2).
  O fluxo vulnerável existe somente nas APIs RSC instáveis, que este SPA
  Vite não usa. O risco não é explorável na arquitetura atual, mas o alerta
  permanece aberto até uma atualização compatível para a versão corrigida.
- A auditoria npm completa passou de 23 entradas (incluindo duas críticas) para
  18 entradas altas depois da atualização de Vite/Vitest. As 16 entradas que
  não pertencem ao grafo de produção ficam nas ferramentas ESLint e
  `openapi-typescript`, sobretudo por `minimatch`/`brace-expansion`. Corrigi-las
  exige upgrades principais e migração de configuração; não exponha servidores
  dessas ferramentas à rede e planeje a atualização separadamente.
- `pip check` confirma uma árvore Python instalada consistente, mas não consulta
  advisories. O projeto ainda não possui lock Python com hashes nem
  `pip-audit`; a auditoria Python reproduzível continua pendente antes do
  encerramento formal.
- `@redocly/openapi-core` solicita npm 9.5 ou superior. A geração foi validada
  com Node 22/npm 9.2, com aviso de engine; use Node 20+ com npm compatível no
  CI para eliminar a divergência.
- A suíte unitária frontend tem 64 testes, mas ainda não instala um provider de
  cobertura Vitest. Os testes passam com avisos React de atualizações
  assíncronas fora de `act(...)`; corrigir esses avisos melhora a robustez da
  suíte, embora não haja falha funcional observada.

## Critério de encerramento

A Fase 1 pode mudar de **homologação** para **concluída** somente quando:

- [ ] todo o gate automatizado estiver verde no commit candidato;
- [ ] migrations e bootstrap tiverem evidência no ambiente escolhido;
- [ ] todos os fluxos funcionais, de segurança e health checks acima passarem;
- [ ] dependências vulneráveis tiverem sido corrigidas ou formalmente aceitas;
- [ ] CORS, Redirect URLs, TLS, CSP no host e rate limiting estiverem
      configurados;
- [ ] nenhum item bloqueante permanecer aberto.

Falhar em um item mantém a Fase 1 em homologação. Isso não autoriza operar
capital real; o ADT continua limitado ao escopo de simulação em papel.
