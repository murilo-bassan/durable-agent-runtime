# runloop

[English](README.md) · **Português**

Roda agentes de IA em tarefas longas sem perder trabalho. Se o servidor cair no meio,
o agente continua de onde parou e nada é feito duas vezes.

![runloop](docs/screenshots/run-full.png)

## O problema

Na demo, um agente é um loop num processo só. Em produção, ele vai passar por um deploy no meio
da tarefa, um servidor que cai, um provedor que devolve erro, uma pessoa que demora horas para
aprovar e um usuário que clica duas vezes em "enviar". O runloop foi feito para que nada disso
quebre a tarefa nem gere ação duplicada.

## O que ele garante

- **Nada se perde quando um worker cai.** O agente salva cada passo no Postgres. Se o processo morrer, outro worker retoma do último passo salvo.
- **Nada acontece duas vezes.** Ações como "criar pedido de compra" são registradas com uma chave única; se a tarefa for repetida, a ação não é refeita.
- **Uma pessoa aprova antes de ações importantes.** O agente pausa e espera. A aprovação vale só para aqueles argumentos exatos: se o agente mudar o valor do pedido, precisa de outra aprovação.
- **O custo tem limite.** Cada usuário tem um orçamento de tokens, que é reservado antes de chamar o modelo, então várias tarefas ao mesmo tempo não estouram o limite.
- **Clique duplo cria uma tarefa só**, por causa do cabeçalho `Idempotency-Key`.
- **Dá para cancelar a qualquer momento**, inclusive no meio da execução.

## O teste de caos

O `scripts/chaos.py` coloca 24 tarefas na fila, mata um worker com `SIGKILL` no meio do trabalho e confere o resultado:

```
enqueued 24 runs
worker A killed with 4 runs in flight
succeeded 24/24 · runs that needed a retry: 4 · purchase orders: 24
chaos drill passed ✓
```

As 24 tarefas terminaram, 4 precisaram ser retomadas e nenhum pedido saiu duplicado.

## Rodando

```bash
git clone https://github.com/murilo-bassan/durable-agent-runtime.git
cd durable-agent-runtime
pip install -r requirements-dev.txt
docker compose up -d db                       # Postgres 16 na porta 55432

python -m pytest                              # testes contra um Postgres de verdade
python scripts/chaos.py                       # o teste de caos

RUNTIME_EMBEDDED_WORKER=true python -m uvicorn runtime.api:app --port 8700
python scripts/demo.py                        # cria tarefas de exemplo
```

Console em http://localhost:8700 e documentação da API em http://localhost:8700/docs.
Para subir tudo com dois workers disputando a fila: `docker compose up --build`.

Sem configuração, o agente usa um planejador determinístico, o que deixa os testes reproduzíveis.
Para usar um LLM: `LLM_PROVIDER=openai`, `LLM_API_KEY` e, se quiser, `LLM_BASE_URL` (qualquer API compatível com a da OpenAI).

## Como funciona

A fila é uma tabela no Postgres. Cada worker pega a próxima tarefa com `SELECT ... FOR UPDATE SKIP LOCKED`,
o que impede dois workers de pegarem a mesma. Enquanto trabalha, o worker manda sinais de vida;
se parar de mandar, a tarefa volta para a fila.

```
queued ──► running ──► succeeded | failed | cancelled
  ▲           │ └── worker sumiu ──► queued (nova tentativa) … ──► dead
  └── aprovação ◄── waiting_approval
```

As decisões de projeto estão em [`docs/adr`](docs/adr).

| Pedido rejeitado duas vezes, o agente se adapta | Console |
|---|---|
| ![](docs/screenshots/rejected-full.png) | ![](docs/screenshots/console.png) |

**Stack:** Python, PostgreSQL, FastAPI, Docker

Licença MIT.
