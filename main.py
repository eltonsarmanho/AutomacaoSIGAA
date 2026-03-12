import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Evita permissao negada no sandbox e mantem perfil/config dentro do projeto.
os.environ.setdefault("BROWSER_USE_CONFIG_DIR", str(Path(".browseruse").resolve()))

from browser_use import Agent, BrowserProfile, BrowserSession, ChatGoogle, ChatOpenAI


COMPONENTES_VALIDOS = {"ACC I", "ACC II", "TCC I", "TCC II"}


@dataclass
class EntradaLancamento:
    matricula: str
    periodo: str
    polo: str
    componente: str
    conceito: str | None


@dataclass
class ConfigSigaa:
    login: str
    senha: str
    sigaa_url: str


class ConfigError(RuntimeError):
    pass


def ler_config_env() -> ConfigSigaa:
    load_dotenv()

    login = os.getenv("LOGIN")
    senha = os.getenv("SENHA")
    sigaa_url = os.getenv("SIGAA_URL")

    faltando = [
        nome
        for nome, valor in {
            "LOGIN": login,
            "SENHA": senha,
            "SIGAA_URL": sigaa_url,
        }.items()
        if not valor
    ]

    if faltando:
        raise ConfigError(f"Variaveis obrigatorias faltando no .env: {', '.join(faltando)}")

    return ConfigSigaa(login=login, senha=senha, sigaa_url=sigaa_url)


def criar_modelo_llm():
    modelo_tipo = "gemini"
    modelo = None

    maritaca_api_key = os.getenv("MARITALK_API_KEY")
    if maritaca_api_key:
        print("   [LLM] Criando modelo Maritaca Sabia...")
        try:
            modelo = ChatOpenAI(
                model="sabia-4",
                api_key=maritaca_api_key,
                base_url="https://chat.maritaca.ai/api",
                temperature=0,
            )
            modelo_tipo = "maritaca"
            print("   [OK] Modelo Maritaca Sabia criado")
        except Exception as err:
            print(f"   [WARN] Erro ao criar Maritaca: {err}")
            print("   [INFO] Tentando usar Gemini...")
            modelo = None

    if not modelo:
        google_api_key = os.getenv("GOOGLE_API_KEY")
        if not google_api_key:
            raise ConfigError(
                "Nao foi possivel criar modelo: defina MARITALK_API_KEY ou GOOGLE_API_KEY no .env."
            )

        print("   [LLM] Criando modelo Gemini...")
        modelo = ChatGoogle(
            model="gemini-3-flash-preview",
            api_key=google_api_key,
            temperature=0,
            thinking_budget=8000,  # Habilita raciocinio estendido para navegacao complexa
        )
        modelo_tipo = "gemini"
        print("   [OK] Modelo Gemini criado")

    return modelo, modelo_tipo


def carregar_instrucoes_do_arquivo(roteiro_path: Path) -> str:
    if not roteiro_path.exists():
        raise FileNotFoundError(f"Arquivo de roteiro nao encontrado: {roteiro_path}")

    conteudo = roteiro_path.read_text(encoding="utf-8").strip()
    if not conteudo:
        raise ValueError(f"Arquivo de roteiro vazio: {roteiro_path}")

    return conteudo


def montar_tarefa(
    config_sigaa: ConfigSigaa,
    entrada: EntradaLancamento,
    instrucoes_do_video: str,
    dry_run: bool,
    mapear_elementos: bool,
) -> str:
    modo = (
        "MODO DRY-RUN: NUNCA clicar em salvar/confirmar/submeter. Pare antes da confirmacao final."
        if dry_run
        else "MODO EXECUCAO REAL: pode confirmar o lancamento ao final."
    )

    conceito_texto = (
        entrada.conceito
        if entrada.conceito
        else "Nao informado. Se estiver vazio, apenas navegar e parar antes de confirmar."
    )

    extra_mapeamento = ""
    if mapear_elementos:
        extra_mapeamento = """
Modo de mapeamento de elementos:
1. Evite acoes desnecessarias fora do fluxo principal.
2. Nao criar/editar arquivos locais (ex: todo.md).
3. Priorize navegacao e leitura dos componentes visiveis na tela.
4. Use indices de elementos de forma consistente durante os passos.
"""

    return f"""
Voce e um agente de automacao para o SIGAA.

{modo}

Credenciais SIGAA:
- URL: {config_sigaa.sigaa_url}
- Login: {config_sigaa.login}
- Senha: {config_sigaa.senha}

Entrada do lancamento:
- Matricula: {entrada.matricula}
- Periodo: {entrada.periodo}
- Polo: {entrada.polo}
- Componente curricular: {entrada.componente}
- Conceito/nota: {conceito_texto}

Siga este roteiro do video:
{instrucoes_do_video}

Regras obrigatorias:
1. Acesse o SIGAA e autentique com as credenciais fornecidas.
2. Navegue no modulo Ensino e no fluxo de lancamento de conceito de ACC/TCC.
3. Localize exatamente o aluno pela matricula e aplique filtros de periodo/polo/componente.
4. Antes de confirmar, valide visualmente se o aluno e a componente estao corretos.
5. Em dry-run, nunca confirme; em execucao real, confirme somente uma vez e registre no resumo final.
6. No fim, entregue um resumo curto do que foi feito e o status final.

{extra_mapeamento}
""".strip()


def _extrair_acoes_step(agent_output: Any) -> list[dict[str, Any]]:
    acoes: list[dict[str, Any]] = []
    if not agent_output or not getattr(agent_output, "action", None):
        return acoes

    for action in agent_output.action:
        payload = action.model_dump(exclude_none=True, exclude_unset=True)
        action_name = "unknown_action"
        action_data: dict[str, Any] = {}
        if payload:
            action_name = list(payload.keys())[0]
            valor = payload[action_name]
            action_data = valor if isinstance(valor, dict) else {"value": valor}

        acoes.append(
            {
                "action_name": action_name,
                "index": action.get_index(),
                "payload": action_data,
            }
        )
    return acoes


def _serializar_elemento(index: int, node: Any) -> dict[str, Any]:
    attrs = getattr(node, "attributes", {}) or {}
    attrs_importantes = {}
    for k in [
        "id",
        "name",
        "class",
        "title",
        "aria-label",
        "placeholder",
        "value",
        "type",
        "href",
        "role",
    ]:
        v = attrs.get(k)
        if v:
            attrs_importantes[k] = v

    texto = ""
    try:
        texto = node.get_meaningful_text_for_llm()
    except Exception:
        texto = getattr(node, "node_value", "") or ""

    return {
        "index": index,
        "tag": getattr(node, "tag_name", ""),
        "xpath": getattr(node, "xpath", ""),
        "text": (texto or "").strip(),
        "attributes": attrs_importantes,
    }


def _filtrar_elementos_relevantes(elementos: list[dict[str, Any]], componente: str) -> list[dict[str, Any]]:
    termos = [
        "modulo",
        "portal",
        "coorden",
        "atividades",
        "matric",
        "buscar",
        "tipo de atividade",
        "proximo",
        "senha",
        "confirmar",
        "formando",
    ]
    termos.append(componente.lower())

    relevantes = []
    for el in elementos:
        texto = f"{el.get('text', '')} {json.dumps(el.get('attributes', {}), ensure_ascii=False)}".lower()
        if any(t in texto for t in termos):
            relevantes.append(el)
    return relevantes


def validar_entrada(entrada: EntradaLancamento) -> None:
    if entrada.componente.upper() not in COMPONENTES_VALIDOS:
        validos = ", ".join(sorted(COMPONENTES_VALIDOS))
        raise ValueError(f"Componente invalida: {entrada.componente}. Use uma de: {validos}")


async def executar_automacao(args: argparse.Namespace) -> None:
    load_dotenv()
    config_sigaa = ler_config_env()

    entrada = EntradaLancamento(
        matricula=args.matricula,
        periodo=args.periodo,
        polo=args.polo,
        componente=args.componente.upper(),
        conceito=args.conceito,
    )
    validar_entrada(entrada)

    modelo, modelo_tipo = criar_modelo_llm()
    print(f"   [OK] Provider selecionado: {modelo_tipo}")

    instrucoes_do_video = args.instrucoes
    if not instrucoes_do_video:
        roteiro_path = Path(args.roteiro_path)
        print(f"   [ROTEIRO] Carregando instrucoes de: {roteiro_path}")
        instrucoes_do_video = carregar_instrucoes_do_arquivo(roteiro_path)
        print("   [OK] Instrucoes carregadas com sucesso")

    tarefa = montar_tarefa(
        config_sigaa=config_sigaa,
        entrada=entrada,
        instrucoes_do_video=instrucoes_do_video,
        dry_run=not args.executar,
        mapear_elementos=args.mapear_elementos,
    )
    map_output_path = Path(args.map_output)
    if args.mapear_elementos:
        map_output_path.parent.mkdir(parents=True, exist_ok=True)
        map_output_path.write_text("", encoding="utf-8")

    browser_profile = BrowserProfile(
        headless=args.headless,
        keep_alive=False,
    )
    browser_session = BrowserSession(browser_profile=browser_profile)

    agente = Agent(
        task=tarefa,
        llm=modelo,
        browser_session=browser_session,
        use_vision=True,
        max_actions_per_step=8,
    )

    print("   [RUN] Iniciando agente browser-use...")
    try:
        if args.mapear_elementos:
            async def registrar_step(agent: Any):
                browser_state = await agent.browser_session.get_browser_state_summary(cached=True)
                agent_output = agent.state.last_model_output
                step_number = agent.state.n_steps
                selector_map = (browser_state.dom_state.selector_map or {}) if browser_state.dom_state else {}
                elementos = [
                    _serializar_elemento(i, node)
                    for i, node in sorted(selector_map.items(), key=lambda x: x[0])[: args.map_max_elements]
                ]
                relevantes = _filtrar_elementos_relevantes(elementos, entrada.componente)
                record = {
                    "step": step_number,
                    "url": browser_state.url,
                    "title": browser_state.title,
                    "actions": _extrair_acoes_step(agent_output),
                    "elements_count_in_step": len(selector_map),
                    "elements_relevantes": relevantes,
                    "elements_all": elementos if args.map_full else [],
                }
                with map_output_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")

            historico = await agente.run(max_steps=args.max_steps, on_step_end=registrar_step)
        else:
            historico = await agente.run(max_steps=args.max_steps)
        resultado = historico.final_result()
        print("\n===== RESULTADO FINAL =====")
        print(resultado if resultado else "Sem resumo final retornado pelo agente.")
        if args.mapear_elementos:
            print(f"\n[MAP] Relatorio salvo em: {map_output_path}")
    finally:
        await agente.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automacao de lancamento de conceito no SIGAA com browser-use"
    )
    parser.add_argument("--matricula", required=True, help="Matricula do aluno")
    parser.add_argument("--periodo", required=True, help="Periodo academico (ex: 2025.2)")
    parser.add_argument("--polo", required=True, help="Polo do aluno")
    parser.add_argument(
        "--componente",
        required=True,
        help="Componente curricular: ACC I, ACC II, TCC I, TCC II",
    )
    parser.add_argument(
        "--conceito",
        default='E',
        help="Conceito/nota para lancamento (opcional)",
    )
    parser.add_argument(
        "--instrucoes",
        default=None,
        help="Roteiro textual manual no formato de passos",
    )
    parser.add_argument(
        "--roteiro-path",
        default="Roteiro.md",
        help="Caminho do arquivo .md com o roteiro operacional",
    )
    parser.add_argument(
        "--executar",
        action="store_true",
        help="Executa lancamento real (sem esta flag fica em dry-run)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Executa navegador sem interface grafica",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=60,
        help="Maximo de passos do agente",
    )
    parser.add_argument(
        "--mapear-elementos",
        action="store_true",
        help="Salva relatorio por etapa com indices e atributos HTML dos elementos",
    )
    parser.add_argument(
        "--map-output",
        default="elementos_mapeados.jsonl",
        help="Arquivo JSONL de saida do mapeamento de elementos",
    )
    parser.add_argument(
        "--map-max-elements",
        type=int,
        default=250,
        help="Quantidade maxima de elementos coletados por etapa",
    )
    parser.add_argument(
        "--map-full",
        action="store_true",
        help="Inclui todos os elementos coletados (alem dos relevantes) no JSONL",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(executar_automacao(args))


if __name__ == "__main__":
    main()
