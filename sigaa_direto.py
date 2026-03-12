import argparse
import asyncio
import os
import re
import unicodedata
from urllib.parse import urlparse
from dataclasses import dataclass

from dotenv import load_dotenv


COMPONENTES_VALIDOS = {"ACC I", "ACC II", "TCC I", "TCC II"}
MAPA_COMPONENTE = {
    "ACC I": ("ATIVIDADES COMPLEMENTARES", "ATIVIDADES CURRICULARES COMPLEMENTARES I"),
    "ACC II": ("ATIVIDADES COMPLEMENTARES", "ATIVIDADES CURRICULARES COMPLEMENTARES II"),
    "TCC I": ("TRABALHO DE CONCLUSAO DE CURSO", "TRABALHO DE CONCLUSAO DE CURSO I"),
    "TCC II": ("TRABALHO DE CONCLUSAO DE CURSO", "TRABALHO DE CONCLUSAO DE CURSO II"),
}


class ConfigError(RuntimeError):
    pass


@dataclass
class ConfigSigaa:
    login: str
    senha: str
    sigaa_url: str


@dataclass
class EntradaLancamento:
    matricula: str
    periodo: str
    polo: str
    componente: str


def norm(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto)
    ascii_only = base.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


def ler_config_env() -> ConfigSigaa:
    load_dotenv()
    login = os.getenv("LOGIN")
    senha = os.getenv("SENHA")
    sigaa_url = os.getenv("SIGAA_URL")

    faltando = [
        nome
        for nome, valor in {"LOGIN": login, "SENHA": senha, "SIGAA_URL": sigaa_url}.items()
        if not valor
    ]
    if faltando:
        raise ConfigError(f"Variaveis obrigatorias faltando no .env: {', '.join(faltando)}")

    return ConfigSigaa(login=login, senha=senha, sigaa_url=sigaa_url)


def validar_entrada(entrada: EntradaLancamento) -> None:
    if entrada.componente.upper() not in COMPONENTES_VALIDOS:
        validos = ", ".join(sorted(COMPONENTES_VALIDOS))
        raise ValueError(f"Componente invalido: {entrada.componente}. Use: {validos}")


async def clicar_primeiro_visivel(page, seletores: list[str], timeout_ms: int = 7000) -> bool:
    for seletor in seletores:
        loc = page.locator(seletor).first
        try:
            await loc.wait_for(state="visible", timeout=timeout_ms)
            await loc.click()
            return True
        except Exception:
            continue
    return False


async def preencher_primeiro_visivel(page, seletores: list[str], valor: str, timeout_ms: int = 7000) -> bool:
    for seletor in seletores:
        loc = page.locator(seletor).first
        try:
            await loc.wait_for(state="visible", timeout=timeout_ms)
            await loc.fill(valor)
            return True
        except Exception:
            continue
    return False


async def marcar_checkbox_por_rotulo(page, rotulo_regex: str) -> bool:
    checkbox_por_label = page.locator(
        f"label:has-text('{rotulo_regex}') >> xpath=preceding::input[@type='checkbox'][1]"
    ).first
    try:
        await checkbox_por_label.wait_for(state="visible", timeout=2000)
        if not await checkbox_por_label.is_checked():
            await checkbox_por_label.check(force=True)
        return True
    except Exception:
        pass

    try:
        checkbox = page.locator(
            f"xpath=//tr[.//*[contains(translate(normalize-space(.), 'ÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', 'AAAAEEEIIIOOOOUUUC'), '{rotulo_regex.upper()}')]]//input[@type='checkbox'][1]"
        ).first
        await checkbox.wait_for(state="visible", timeout=2000)
        if not await checkbox.is_checked():
            await checkbox.check(force=True)
        return True
    except Exception:
        return False


async def preencher_input_por_rotulo(page, rotulo_regex: str, valor: str) -> bool:
    candidatos = [
        # Estrutura comum do SIGAA: rotulo na coluna esquerda e input na mesma linha
        f"xpath=//tr[.//*[contains(translate(normalize-space(.), 'ÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', 'AAAAEEEIIIOOOOUUUC'), '{rotulo_regex.upper()}')]]//input[not(@type='checkbox') and not(@type='hidden')][1]",
        f"xpath=//td[contains(translate(normalize-space(.), 'ÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', 'AAAAEEEIIIOOOOUUUC'), '{rotulo_regex.upper()}')]/following::input[not(@type='checkbox') and not(@type='hidden')][1]",
    ]
    for seletor in candidatos:
        loc = page.locator(seletor).first
        try:
            await loc.wait_for(state="visible", timeout=2000)
            await loc.fill(valor)
            return True
        except Exception:
            continue
    return False


async def clicar_texto(page, texto: str, timeout_ms: int = 7000) -> bool:
    candidatos = [
        page.get_by_role("link", name=re.compile(re.escape(texto), re.IGNORECASE)).first,
        page.get_by_role("button", name=re.compile(re.escape(texto), re.IGNORECASE)).first,
        page.get_by_text(re.compile(re.escape(texto), re.IGNORECASE)).first,
    ]
    for loc in candidatos:
        try:
            await loc.wait_for(state="visible", timeout=timeout_ms)
            await loc.click()
            return True
        except Exception:
            continue
    return False


def variacoes_periodo(periodo: str) -> list[str]:
    base = periodo.strip()
    return [base, base.replace(".", "-"), base.replace("-", ".")]


def base_sigaa_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}"


async def selecionar_opcao_em_dropdown(page, opcao_desejada: str, filtro_dropdown: str | None = None) -> bool:
    select_loc = page.locator("select")
    total = await select_loc.count()
    alvo = norm(opcao_desejada)
    filtro = norm(filtro_dropdown) if filtro_dropdown else None

    for i in range(total):
        sel = select_loc.nth(i)

        if filtro:
            descritor = await sel.evaluate(
                "el => [el.id || '', el.name || '', el.getAttribute('aria-label') || '', el.className || ''].join(' ')"
            )
            if filtro not in norm(descritor):
                continue

        opcoes = await sel.locator("option").all_text_contents()
        candidata = next((o for o in opcoes if alvo in norm(o)), None)
        if not candidata:
            continue

        try:
            await sel.select_option(label=candidata.strip())
            return True
        except Exception:
            try:
                valor = await sel.locator("option", has_text=candidata.strip()).first.get_attribute("value")
                if valor is not None:
                    await sel.select_option(value=valor)
                    return True
            except Exception:
                continue

    return False


async def clicar_seta_selecao_discente(page, matricula: str) -> bool:
    # 1) Preferencia: linha com matricula
    linha = page.locator(f"tr:has-text('{matricula}')").first
    try:
        if await linha.count():
            for seletor in ["a", "input[type='image']", "img"]:
                alvo = linha.locator(seletor).first
                if await alvo.count():
                    await alvo.click()
                    return True
    except Exception:
        pass

    # 2) Linha com FORMANDO
    linha_formando = page.locator("tr:has-text('FORMANDO')").first
    try:
        if await linha_formando.count():
            for seletor in ["a", "input[type='image']", "img"]:
                alvo = linha_formando.locator(seletor).first
                if await alvo.count():
                    await alvo.click()
                    return True
    except Exception:
        pass

    # 3) Fallback generico
    return await clicar_primeiro_visivel(
        page,
        [
            "a[title*='Selecion']",
            "img[alt*='Selecion']",
            "input[type='image'][alt*='Selecion']",
            "a:has(i.fa-arrow-right)",
        ],
        timeout_ms=4000,
    )


async def _aguardar_navegacao(page, esperado_na_url: str, timeout_ms: int = 10000) -> bool:
    """Aguarda até a URL conter o fragmento esperado."""
    try:
        await page.wait_for_url(f"**{esperado_na_url}**", timeout=timeout_ms)
        return True
    except Exception:
        return esperado_na_url in page.url


async def _clicar_menu_atividades_matricular(page) -> bool:
    """
    Navega pelo menu bar ThemeOffice do SIGAA.
    Como os itens de submenu possuem onclicks atrelados a tabelas/linhas (<tr>),
    tentar clicar no <a> as vezes nao aciona o form.

    Passo a passo com base no mapeamento:
      1. Clique em 'Atividades' (td class 'ThemeOfficeMainItem')
      2. Clique na linha 'Matricular' (tr class 'ThemeOfficeMenuItem')
    """
    # 1. Clicar em Atividades (td[2])
    seletores_atividades = [
        "td.ThemeOfficeMainItem:has-text('Atividades')",
        "td.ThemeOfficeMainItemHover:has-text('Atividades')",
        "xpath=//form//table//td[2][contains(., 'Atividades')]",
    ]
    for sel in seletores_atividades:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=3000)
            await loc.click(force=True)
            await page.wait_for_timeout(500)
            break
        except Exception:
            continue
    else:
        # Se os seletores falharem, tenta JS
        await page.evaluate("""
            () => {
                const tds = Array.from(document.querySelectorAll('td.ThemeOfficeMainItem'));
                const ativ = tds.find(td => /Atividades/i.test(td.textContent));
                if (ativ) ativ.click();
            }
        """)
        await page.wait_for_timeout(500)

    # 2. Clicar em Matricular na linha apropriada.
    # No mapeamento: <tr class="ThemeOfficeMenuItem"> text="Matricular" xpath="...div[5]/table/tbody/tr[3]"
    seletores_matricular = [
        "tr.ThemeOfficeMenuItem:has-text('Matricular')",
        "xpath=//tr[contains(@class, 'ThemeOfficeMenuItem') and contains(., 'Matricular')]",
        "xpath=//form//div[5]//table//tr[3]", # Mapeamento exato
        "a:has-text('Matricular')",
    ]
    for sel in seletores_matricular:
        try:
            loc = page.locator(sel).first
            # force=True bypassa visibilidade - util se o menu tentar esconder no momento do clique
            await loc.click(force=True, timeout=3000)
            return True
        except Exception:
            continue

    # Fallback extremo via JS que aciona onclick do TR
    clicou = await page.evaluate("""
        () => {
            const trs = Array.from(document.querySelectorAll('tr.ThemeOfficeMenuItem, td.ThemeOfficeMenuItens tr'));
            const alvo = trs.find(tr => /^Matricular/i.test(tr.textContent.trim()));
            if (alvo) {
                alvo.click();
                return true;
            }
            return false;
        }
    """)
    if clicou:
        return True

    return False


async def executar_fluxo_direto(args: argparse.Namespace) -> None:
    try:
        from playwright.async_api import async_playwright
    except Exception as err:
        raise RuntimeError(
            "Playwright nao encontrado. Instale com: pip install playwright && playwright install chromium"
        ) from err

    cfg = ler_config_env()
    entrada = EntradaLancamento(
        matricula=args.matricula,
        periodo=args.periodo,
        polo=args.polo,
        componente=args.componente.upper(),
    )
    validar_entrada(entrada)

    tipo_atividade, atividade_nome = MAPA_COMPONENTE[entrada.componente]
    if args.atividade_nome:
        atividade_nome = args.atividade_nome

    base = base_sigaa_url(cfg.sigaa_url)

    print("[1/9] Abrindo navegador e SIGAA...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=args.headless)
        context = await browser.new_context()
        page = await context.new_page()

        # --- LOGIN ---
        # Confirmado no mapeamento step 2-3: campos name="user.login" e name="user.senha"
        print("[2/9] Login...")
        await page.goto(cfg.sigaa_url, wait_until="domcontentloaded")

        login_field = page.locator("input[name='user.login']").first
        await login_field.wait_for(state="visible", timeout=10000)
        await login_field.fill(cfg.login)

        senha_field = page.locator("input[name='user.senha']").first
        await senha_field.fill(cfg.senha)

        submit = page.locator("input[type='submit']").first
        await submit.click()
        await page.wait_for_load_state("domcontentloaded")

        # --- SELECAO DE PERIODO NO CALENDARIO (step 4 do mapeamento) ---
        # Apos o login, SIGAA redireciona para calendarios_graduacao_vigentes.jsf.
        # E necessario clicar no link do periodo academico para ativar o contexto
        # de semestre. Sem esse passo, o dropdown de cursos no coordenador.jsf fica vazio.
        print("[3/9] Selecionando periodo academico no calendario...")
        await page.wait_for_load_state("domcontentloaded")
        if "calendarios" in page.url or "verTelaLogin" not in page.url:
            # Tenta clicar no link do periodo (ex: "2026.1" ou "2026-1") dentro da pagina
            clicou_periodo = False
            for variacao in variacoes_periodo(entrada.periodo):
                try:
                    link_periodo = page.locator(
                        f"a:has-text('{variacao}')"
                    ).first
                    await link_periodo.wait_for(state="visible", timeout=3000)
                    await link_periodo.click()
                    await page.wait_for_load_state("domcontentloaded")
                    clicou_periodo = True
                    print(f"   [OK] Periodo '{variacao}' selecionado.")
                    break
                except Exception:
                    continue

            if not clicou_periodo:
                # Fallback: clica no primeiro link de periodo disponivel na tabela
                try:
                    link_qualquer = page.locator(
                        "table a[href*='verMenuPrincipal'], "
                        "table a[href*='calendario'], "
                        "td.destaque a, "
                        "td a[href*='periodo']"
                    ).first
                    await link_qualquer.wait_for(state="visible", timeout=3000)
                    await link_qualquer.click()
                    await page.wait_for_load_state("domcontentloaded")
                    print(f"   [WARN] Periodo '{entrada.periodo}' nao encontrado; clicou no primeiro periodo disponivel.")
                except Exception:
                    print(f"   [WARN] Nao foi possivel clicar em periodo; tentando continuar.")

        # --- PORTAL COORD. GRADUACAO (step 5 do mapeamento) ---
        # Apos selecionar o periodo, o link do portal aparece no menu principal.
        # href="/sigaa/verPortalCoordenadorGraduacao.do" confirmado no step 5.
        print("[3/9] Abrindo Portal Coord. Graduacao...")
        if "coordenador.jsf" not in page.url:
            link_portal = page.locator("a[href*='verPortalCoordenadorGraduacao']").first
            try:
                await link_portal.wait_for(state="visible", timeout=5000)
                await link_portal.click()
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                # Fallback via URL direta (so funciona se o contexto de periodo ja estiver ativo)
                portal_do_url = f"{base}/sigaa/verPortalCoordenadorGraduacao.do"
                await page.goto(portal_do_url, wait_until="domcontentloaded")
                await page.wait_for_load_state("domcontentloaded")

        if "coordenador.jsf" not in page.url:
            raise RuntimeError(f"Nao foi possivel abrir o Portal Coord. Graduacao. URL atual: {page.url}")

        # --- SELECAO DE CURSO ---
        # Confirmado no mapeamento step 6-7: dropdown na pagina coordenador.jsf.
        # A opcao real observada: "SISTEMAS DE INFORMACAO - OEIRAS/CCAME - OEIRAS DO PARÁ"
        # Selecionamos pela correspondencia parcial com o polo.
        # O dropdown tem onchange="submit()" portanto a pagina recarrega automaticamente.
        print("[4/9] Selecionando curso pelo polo...")
        await page.wait_for_load_state("domcontentloaded")
        alvo_curso = args.curso if args.curso else entrada.polo
        selecionou = await selecionar_opcao_em_dropdown(page, alvo_curso)
        if selecionou:
            # Aguarda o reload provocado pelo onchange="submit()"
            await page.wait_for_load_state("domcontentloaded")
        else:
            print(f"[WARN] Opcao de curso com '{alvo_curso}' nao encontrada. Seguindo com curso atual.")

        # --- MENU ATIVIDADES > MATRICULAR ---
        # Confirmado no mapeamento step 8-9:
        #   step 8: click no link dentro de td.ThemeOfficeMainItem 'Atividades'
        #   step 9: click no link 'Matricular' dentro de tr.ThemeOfficeMenuItem
        # Resultado: navega para busca_discente.jsf
        print("[5/9] Menu Atividades > Matricular...")
        if not await _clicar_menu_atividades_matricular(page):
            raise RuntimeError("Nao foi possivel navegar pelo menu Atividades > Matricular.")

        await _aguardar_navegacao(page, "busca_discente.jsf", timeout_ms=8000)
        if "busca_discente.jsf" not in page.url:
            raise RuntimeError(f"Esperava busca_discente.jsf, mas URL e: {page.url}")

        # --- BUSCA DO DISCENTE ---
        # Confirmado no mapeamento step 10:
        #   click em input[id="formulario:checkMatricula"] (radio/checkbox de criterio matricula)
        #   fill em input[id="formulario:matriculaDiscente"]
        #   click em input[id="formulario:buscar"]
        print("[6/9] Buscando aluno por matricula...")
        await page.wait_for_load_state("domcontentloaded")

        check_mat = page.locator('[id="formulario:checkMatricula"]').first
        try:
            await check_mat.wait_for(state="visible", timeout=5000)
            await check_mat.click()
        except Exception:
            print("[WARN] Checkbox formulario:checkMatricula nao encontrado; tentando fallback...")
            await clicar_primeiro_visivel(
                page,
                ["input[type='checkbox'][id*='checkMatricula']", "input[type='radio'][id*='checkMatricula']"],
                timeout_ms=3000,
            )

        campo_mat = page.locator('[id="formulario:matriculaDiscente"]').first
        try:
            await campo_mat.wait_for(state="visible", timeout=5000)
            await campo_mat.fill(entrada.matricula)
        except Exception:
            if not await preencher_primeiro_visivel(
                page,
                ["input[id*='matriculaDiscente']", "input[title*='Matrícula']", "input[title*='Matricula']"],
                entrada.matricula,
            ):
                raise RuntimeError("Nao foi possivel preencher o campo de matricula.")

        buscar_btn = page.locator('[id="formulario:buscar"]').first
        try:
            await buscar_btn.wait_for(state="visible", timeout=5000)
            await buscar_btn.click()
        except Exception:
            if not await clicar_primeiro_visivel(
                page,
                ["input[value='Buscar']", "input[id*='buscar']", "button:has-text('Buscar')"],
            ):
                raise RuntimeError("Nao foi possivel clicar em Buscar.")

        await page.wait_for_load_state("domcontentloaded")

        # --- SELECAO DO DISCENTE ---
        # Confirmado no mapeamento step 11: click em idx=46 (link na linha do aluno)
        # A linha contem a matricula; clicamos no primeiro link/imagem disponivel
        print("[7/9] Selecionando discente na lista...")
        if not await clicar_seta_selecao_discente(page, entrada.matricula):
            raise RuntimeError("Nao foi possivel selecionar o discente na lista de resultados.")

        await _aguardar_navegacao(page, "busca_atividade.jsf", timeout_ms=8000)
        if "busca_atividade.jsf" not in page.url:
            raise RuntimeError(f"Esperava busca_atividade.jsf, mas URL e: {page.url}")

        # --- SELECAO DE ATIVIDADE ---
        # Confirmado no mapeamento step 12:
        #   select em select[id="form:idTipoAtividade"] → "ATIVIDADES COMPLEMENTARES"
        #   click em input[id="form:atividades"] (Buscar Atividades)
        # Steps 13-20: agente buscou e clicou no link da atividade na tabela de resultados
        # Step 21: clicou em botao de confirmacao/proximo para ir a dados_registro.jsf
        print("[8/9] Selecionando tipo de atividade e buscando componente...")
        await page.wait_for_load_state("domcontentloaded")

        sel_tipo = page.locator('[id="form:idTipoAtividade"]').first
        try:
            await sel_tipo.wait_for(state="visible", timeout=5000)
            await sel_tipo.select_option(label=tipo_atividade)
        except Exception:
            if not await selecionar_opcao_em_dropdown(page, tipo_atividade, filtro_dropdown="tipoAtividade"):
                raise RuntimeError(f"Nao foi possivel selecionar tipo de atividade: {tipo_atividade}")

        buscar_ativ = page.locator('[id="form:atividades"]').first
        try:
            await buscar_ativ.wait_for(state="visible", timeout=5000)
            await buscar_ativ.click()
        except Exception:
            if not await clicar_primeiro_visivel(
                page,
                ["input[value*='Buscar Atividades']", "input[id*='atividades']"],
            ):
                raise RuntimeError("Nao foi possivel clicar em Buscar Atividades.")

        await page.wait_for_load_state("domcontentloaded")

        # Localiza a linha da atividade pelo nome parcial e clica no link de selecao
        alvo_ativ = norm(atividade_nome)
        linha = page.locator(
            f"xpath=//tr[.//td[contains(translate(normalize-space(.), "
            f"'ÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', 'AAAAEEEIIIOOOOUUUC'), '{alvo_ativ.upper()}')]]"
        ).first

        # Fallback caso normalize nao funcione: busca pelo texto original
        if not await linha.count():
            linha = page.locator(f"tr:has-text('{atividade_nome}')").first

        if await linha.count():
            clicou = False
            for sel in ["a", "input[type='image']", "img", "input[type='submit']"]:
                alvo_el = linha.locator(sel).first
                if await alvo_el.count():
                    await alvo_el.click()
                    clicou = True
                    break
            if not clicou:
                raise RuntimeError(f"Encontrei '{atividade_nome}' mas nao consegui clicar na selecao.")
        else:
            raise RuntimeError(f"Atividade '{atividade_nome}' nao encontrada na tabela de resultados.")

        # Aguarda e clica em "Proximo Passo" (step 21 do mapeamento: click que leva a dados_registro.jsf)
        await page.wait_for_load_state("domcontentloaded")
        if "dados_registro.jsf" not in page.url:
            clicou_proximo = await clicar_primeiro_visivel(
                page,
                [
                    "input[value*='Próximo']",
                    "input[value*='Proximo']",
                    "button:has-text('Próximo')",
                    "button:has-text('Proximo')",
                    "a:has-text('Próximo')",
                    # fallback: qualquer submit que nao seja "Selecionar Outra Atividade" e "Buscar"
                    "input[type='submit']:not([id='form:btnAtividades']):not([id='form:atividades'])",
                ],
                timeout_ms=5000,
            )
            if clicou_proximo:
                await _aguardar_navegacao(page, "dados_registro.jsf", timeout_ms=8000)

        if "dados_registro.jsf" not in page.url:
            raise RuntimeError(f"Esperava dados_registro.jsf, mas URL e: {page.url}")

        # --- SENHA E CONFIRMACAO ---
        # Confirmado no mapeamento step 22:
        #   input[id="form:senha"]                 → campo de senha
        #   input[id="form:botaoConfirmarRegistro"] → botao Confirmar
        print("[9/9] Senha e confirmacao final...")
        await page.wait_for_load_state("domcontentloaded")

        campo_senha = page.locator('[id="form:senha"]').first
        try:
            await campo_senha.wait_for(state="visible", timeout=8000)
            await campo_senha.fill(cfg.senha)
        except Exception:
            if not await preencher_primeiro_visivel(
                page,
                ["input[id*='senha']", "input[type='password']"],
                cfg.senha,
            ):
                raise RuntimeError("Nao foi possivel preencher a senha de confirmacao.")

        if args.executar:
            confirmar_btn = page.locator('[id="form:botaoConfirmarRegistro"]').first
            try:
                await confirmar_btn.wait_for(state="visible", timeout=5000)
                await confirmar_btn.click()
            except Exception:
                if not await clicar_primeiro_visivel(
                    page,
                    [
                        "input[id*='botaoConfirmarRegistro']",
                        "input[value='Confirmar']",
                        "button:has-text('Confirmar')",
                    ],
                ):
                    raise RuntimeError("Nao foi possivel clicar em Confirmar.")

            await page.wait_for_timeout(3000)
            conteudo = norm(await page.locator("body").inner_text())
            if "sucesso" in conteudo and ("matricula em atividade" in conteudo or "realizada com sucesso" in conteudo):
                print("[OK] Confirmacao executada — mensagem de sucesso detectada.")
            else:
                print("[WARN] Confirmacao clicada, mas mensagem de sucesso nao foi identificada no corpo da pagina.")
        else:
            print("[DRY-RUN] Parado antes do clique em 'Confirmar'. Senha preenchida mas NAO enviada.")

        if args.manter_aberto:
            print("[INFO] Navegador mantido aberto. Pressione Enter para fechar...")
            input()

        await context.close()
        await browser.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automacao SIGAA por instrucoes diretas (sem LLM)"
    )
    parser.add_argument("--matricula", required=True, help="Matricula do aluno")
    parser.add_argument("--periodo", required=True, help="Periodo academico (ex: 2026.1)")
    parser.add_argument("--polo", required=True, help="Polo (usado para selecionar curso)")
    parser.add_argument("--componente", required=True, help="ACC I, ACC II, TCC I, TCC II")
    parser.add_argument("--curso", default=None, help="Texto do curso no dropdown (sobrescreve --polo)")
    parser.add_argument("--atividade-nome", default=None, help="Nome exato/parcial da atividade para selecionar")
    parser.add_argument("--executar", action="store_true", help="Executa confirmacao final")
    parser.add_argument("--headless", action="store_true", help="Executa sem UI")
    parser.add_argument("--manter-aberto", action="store_true", help="Mantem navegador aberto no final")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(executar_fluxo_direto(args))


if __name__ == "__main__":
    main()
