# Automacao de Lancamento de Conceito no SIGAA

## Scripts disponiveis

- `main.py`: fluxo com agente `browser-use` (LLM)
- `sigaa_direto.py`: fluxo deterministico com `playwright` (sem LLM)

Se o objetivo for evitar acoes inesperadas de agente (ex: escrever `todo.md`), use `sigaa_direto.py`.

## 1. Variaveis no `.env`

Obrigatorias:

- `LOGIN`
- `SENHA`
- `SIGAA_URL`

Opcional para `main.py` (LLM):

- `MARITALK_API_KEY`
- `GOOGLE_API_KEY`

## 2. Instalar dependencias

```bash
pip install -r requirements.txt
playwright install chromium
```

## 3. Execucao direta (sem LLM) - dry-run

Dry-run para antes do clique em `Confirmar`.

```bash
python sigaa_direto.py \
  --matricula 202285940020 \
  --periodo 2026.1 \
  --polo "OEIRAS DO PARA" \
  --componente "ACC I"
```

## 4. Execucao direta (real)

```bash
python sigaa_direto.py \
  --matricula 202285940020 \
  --periodo 2026.1 \
  --polo "OEIRAS DO PARA" \
  --componente "ACC I" \
  --executar
```

## 5. Opcoes uteis do script direto

- `--curso "..."`: sobrescreve o texto usado para localizar o curso no dropdown.
- `--atividade-nome "..."`: sobrescreve o nome da atividade da componente.
- `--headless`: executa sem interface grafica.
- `--manter-aberto`: mantem o navegador aberto ao final.

## Observacoes

- Componentes aceitas: `ACC I`, `ACC II`, `TCC I`, `TCC II`.
- O script direto usa seletores por texto/label com fallback. Mudancas visuais no SIGAA podem exigir ajuste de seletores.


Exemplo:
Para ver navegador remova --headless

 python sigaa_direto.py --matricula 202285640027 --periodo 2026.1 --polo "LIMOEIRO DO AJURU" --componente "ACC I" --headless --executar 2>&1 | head -350; python sigaa_direto.py --matricula 202285640027 --periodo 2026.1 --polo "LIMOEIRO DO AJURU" --componente "ACC II" --headless --executar 2>&1 | head -350; python sigaa_direto.py --matricula 202285640027 --periodo 2026.1 --polo "LIMOEIRO DO AJURU" --componente "ACC III" --headless --executar 2>&1 | head -350; python sigaa_direto.py --matricula 202285640027 --periodo 2026.1 --polo "LIMOEIRO DO AJURU" --componente "ACC IV" --headless --executar 2>&1 | head -350;

python sigaa_direto.py --matricula 202285940019 --periodo 2026.1 --polo "OEIRAS DO PARÁ" --componente "ACC I" --headless --executar 2>&1 | head -350; python sigaa_direto.py --matricula 202285940019 --periodo 2026.1 --polo "OEIRAS DO PARÁ" --componente "ACC II" --headless --executar 2>&1 | head -350; python sigaa_direto.py --matricula 202285940019 --periodo 2026.1 --polo "OEIRAS DO PARÁ" --componente "ACC III" --headless --executar 2>&1 | head -350; python sigaa_direto.py --matricula 202285940019 --periodo 2026.1 --polo "OEIRAS DO PARÁ" --componente "ACC IV" --headless --executar 2>&1 | head -350;