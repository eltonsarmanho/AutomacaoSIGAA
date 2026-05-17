"""
rastreador_sigaa.py — Agente de rastreamento interativo do SIGAA.

COMO USAR:
  python rastreador_sigaa.py

O script abre o browser VISÍVEL. Navegue normalmente e faça o processo
completo de matrícula de ACC de um aluno. O agente registra automaticamente:
  - Cada página visitada (URL, título)
  - Cada clique (elemento, id, name, tipo, texto, xpath)
  - Cada formulário submetido (campos e valores)
  - Requests HTTP relevantes (URL, método, corpo POST)
  - Screenshots nos momentos-chave

Ao fechar o browser (ou pressionar Ctrl+C), o script salva o mapeamento em
  mapeamento_acc_<timestamp>.jsonl
e gera um relatório legível em
  relatorio_mapeamento.txt

Em seguida, pergunta se deseja aplicar as correções automaticamente nos scripts.
"""

import asyncio
import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# ── Arquivo de saída ───────────────────────────────────────────────────────────
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
JSONL_PATH = Path(f"mapeamento_acc_{TIMESTAMP}.jsonl")
RELATORIO_PATH = Path("relatorio_mapeamento.txt")

# ── JS injetado para capturar cliques com detalhes ────────────────────────────
_JS_CAPTURE = r"""
(function() {
    if (window.__sigaaRastreadorAtivo) return;
    window.__sigaaRastreadorAtivo = true;
    window.__sigaaEventos = [];

    function getXPath(el) {
        if (!el || el.nodeType !== 1) return '';
        if (el.id) return '//*[@id="' + el.id + '"]';
        var parts = [];
        while (el && el.nodeType === 1) {
            var idx = 0, sib = el.previousSibling;
            while (sib) { if (sib.nodeType === 1 && sib.nodeName === el.nodeName) idx++; sib = sib.previousSibling; }
            parts.unshift(el.nodeName.toLowerCase() + (idx > 0 ? '[' + (idx+1) + ']' : ''));
            el = el.parentNode;
        }
        return '/' + parts.join('/');
    }

    function getDetails(el) {
        if (!el) return {};
        var form = el.closest('form');
        var tr = el.closest('tr');
        return {
            tag: el.tagName,
            id: el.id || null,
            name: el.getAttribute('name') || null,
            type: el.getAttribute('type') || null,
            value: (el.value || '').substring(0, 200),
            text: (el.textContent || el.innerText || '').trim().substring(0, 200),
            href: el.getAttribute('href') || null,
            onclick: (el.getAttribute('onclick') || '').substring(0, 300),
            src: el.getAttribute('src') || null,
            alt: el.getAttribute('alt') || null,
            title: el.getAttribute('title') || null,
            className: el.className || null,
            xpath: getXPath(el),
            formId: form ? form.id : null,
            formAction: form ? form.action : null,
            rowText: tr ? tr.textContent.trim().substring(0, 200) : null,
        };
    }

    function getFormData(form) {
        if (!form) return null;
        var fields = {};
        var inputs = form.querySelectorAll('input, select, textarea');
        inputs.forEach(function(inp) {
            if (!inp.name) return;
            if (inp.type === 'password') {
                fields[inp.name] = '***';
            } else if (inp.type === 'checkbox' || inp.type === 'radio') {
                if (inp.checked) fields[inp.name] = inp.value;
            } else {
                var v = inp.tagName === 'SELECT'
                    ? (inp.selectedOptions[0] ? inp.selectedOptions[0].text.trim() : inp.value)
                    : inp.value;
                if (v) fields[inp.name] = v.substring(0, 200);
            }
        });
        return {
            id: form.id,
            action: form.action,
            method: form.method,
            fields: fields
        };
    }

    document.addEventListener('click', function(e) {
        var el = e.target;
        // Subir até encontrar um elemento clicável relevante
        for (var i = 0; i < 5; i++) {
            if (!el) break;
            if (['A','INPUT','BUTTON','IMG'].includes(el.tagName)) break;
            el = el.parentElement;
        }
        if (!el) el = e.target;

        var det = getDetails(el);
        det.pageUrl = window.location.href;
        det.pageTitle = document.title;
        det.ts = Date.now();
        det.eventType = 'click';
        det.viewState = (document.querySelector('input[name="javax.faces.ViewState"]') || {}).value;

        // Se é submit de form, capturar os dados
        var form = el.closest('form');
        if (el.type === 'submit' || el.type === 'image' || (el.tagName === 'A' && el.onclick)) {
            det.formData = getFormData(form);
        }

        window.__sigaaEventos.push(det);
    }, true);

    // Capturar submits de form
    document.addEventListener('submit', function(e) {
        var form = e.target;
        var ev = {
            eventType: 'submit',
            pageUrl: window.location.href,
            pageTitle: document.title,
            ts: Date.now(),
            formData: getFormData(form),
        };
        window.__sigaaEventos.push(ev);
    }, true);
})();
"""

# ── Helpers ────────────────────────────────────────────────────────────────────

def _salvar_evento(f, evento: dict):
    f.write(json.dumps(evento, ensure_ascii=False) + "\n")
    f.flush()


def norm(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto)
    ascii_only = base.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


# ── Rastreador principal ───────────────────────────────────────────────────────

async def rastrear():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[ERRO] Playwright nao encontrado.")
        print("  Execute: pip install playwright && playwright install chromium")
        sys.exit(1)

    load_dotenv()
    sigaa_url = os.getenv("SIGAA_URL", "")
    if not sigaa_url:
        print("[AVISO] SIGAA_URL nao definida no .env. O browser abrirá em branco.")

    print("=" * 65)
    print("  AGENTE RASTREADOR DO SIGAA — MATRÍCULA ACC")
    print("=" * 65)
    print()
    print("  O browser será aberto. Faça o processo COMPLETO de matrícula")
    print("  de um aluno em ACC normalmente.")
    print()
    print("  Tudo será registrado automaticamente em:")
    print(f"    {JSONL_PATH}")
    print()
    print("  Quando terminar, feche o browser ou pressione Ctrl+C aqui.")
    print("=" * 65)
    input("  Pressione ENTER para abrir o browser...")
    print()

    eventos = []
    requests_capturadas = []
    paginas_visitadas = []

    with open(JSONL_PATH, "w", encoding="utf-8") as f_jsonl:

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=["--window-size=1400,900"]
            )
            context = await browser.new_context(viewport={"width": 1400, "height": 900})

            # Injetar script de captura em cada nova página
            await context.add_init_script(_JS_CAPTURE)

            page = await context.new_page()

            # ── Capturar requests ──────────────────────────────────────────
            def on_request(request):
                if request.method == "POST" or "jsf" in request.url or "sigaa" in request.url:
                    ev = {
                        "eventType": "request",
                        "ts": datetime.now().isoformat(),
                        "method": request.method,
                        "url": request.url,
                        "postData": (request.post_data or "")[:2000],
                    }
                    requests_capturadas.append(ev)
                    _salvar_evento(f_jsonl, ev)

            def on_response(response):
                if response.status >= 300 or "jsf" in response.url:
                    ev = {
                        "eventType": "response",
                        "ts": datetime.now().isoformat(),
                        "url": response.url,
                        "status": response.status,
                    }
                    _salvar_evento(f_jsonl, ev)

            def on_framenavigated(frame):
                if frame == page.main_frame:
                    ev = {
                        "eventType": "navigation",
                        "ts": datetime.now().isoformat(),
                        "url": frame.url,
                    }
                    paginas_visitadas.append(frame.url)
                    _salvar_evento(f_jsonl, ev)
                    print(f"  [NAV] {frame.url[:80]}")

            page.on("request", on_request)
            page.on("response", on_response)
            page.on("framenavigated", on_framenavigated)

            # Abrir SIGAA
            if sigaa_url:
                await page.goto(sigaa_url, wait_until="domcontentloaded")
            else:
                await page.goto("about:blank")

            print()
            print("  [INFO] Browser aberto. Navegue e faça a matrícula completa.")
            print("  [INFO] Os eventos são registrados automaticamente.")
            print()

            # ── Loop de coleta: checar eventos JS periodicamente ──────────
            ultimo_idx = 0
            screenshot_count = 0

            try:
                while True:
                    await asyncio.sleep(1)

                    # Coletar eventos JS acumulados
                    try:
                        novos = await page.evaluate("""() => {
                            var evs = window.__sigaaEventos || [];
                            window.__sigaaEventos = [];
                            return evs;
                        }""")
                        for ev in novos:
                            ev["ts"] = datetime.now().isoformat()
                            eventos.append(ev)
                            _salvar_evento(f_jsonl, ev)

                            tipo = ev.get("eventType")
                            if tipo == "click":
                                tag = ev.get("tag", "?")
                                eid = ev.get("id") or ev.get("name") or ""
                                txt = (ev.get("text") or "")[:40]
                                print(f"  [CLICK] <{tag}> id={eid!r} texto={txt!r}")
                            elif tipo == "submit":
                                fd = ev.get("formData") or {}
                                print(f"  [SUBMIT] form={fd.get('id')!r} action={fd.get('action', '')[:60]!r}")

                    except Exception:
                        pass  # página pode estar carregando

            except asyncio.CancelledError:
                pass
            except KeyboardInterrupt:
                pass
            finally:
                # Captura screenshot final
                try:
                    screenshot_path = f"/tmp/sigaa_final_{TIMESTAMP}.png"
                    await page.screenshot(path=screenshot_path)
                    print(f"\n  [INFO] Screenshot final salvo em {screenshot_path}")
                except Exception:
                    pass

                # Registrar metadata final
                meta = {
                    "eventType": "meta",
                    "ts": datetime.now().isoformat(),
                    "totalEventos": len(eventos),
                    "totalRequests": len(requests_capturadas),
                    "paginasVisitadas": list(dict.fromkeys(paginas_visitadas)),
                }
                _salvar_evento(f_jsonl, meta)

                try:
                    await context.close()
                    await browser.close()
                except Exception:
                    pass

    print()
    print(f"  [OK] Mapeamento salvo em: {JSONL_PATH}")
    print(f"       Total de eventos: {len(eventos)}")
    print(f"       Total de requests: {len(requests_capturadas)}")
    print(f"       Páginas visitadas: {len(set(paginas_visitadas))}")
    print()

    return eventos, requests_capturadas, paginas_visitadas


# ── Analisar mapeamento e gerar relatório ─────────────────────────────────────

def analisar_mapeamento(jsonl_path: Path) -> dict:
    """Lê o JSONL e extrai o fluxo estruturado de navegação."""
    eventos = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    eventos.append(json.loads(line))
                except Exception:
                    pass

    fluxo = {
        "paginas": [],
        "cliques": [],
        "submits": [],
        "requests_post": [],
        "elementos_por_pagina": {},
    }

    pagina_atual = ""
    for ev in eventos:
        tipo = ev.get("eventType")

        if tipo == "navigation":
            url = ev.get("url", "")
            if url and url != "about:blank" and url != pagina_atual:
                pagina_atual = url
                fluxo["paginas"].append({"url": url, "ts": ev.get("ts")})
                fluxo["elementos_por_pagina"][url] = []

        elif tipo == "click":
            item = {
                "pagina": ev.get("pageUrl", pagina_atual),
                "tag": ev.get("tag"),
                "id": ev.get("id"),
                "name": ev.get("name"),
                "type": ev.get("type"),
                "texto": ev.get("text", "")[:80],
                "xpath": ev.get("xpath"),
                "onclick": ev.get("onclick", "")[:100],
                "formAction": ev.get("formAction"),
                "rowText": ev.get("rowText", "")[:100],
                "ts": ev.get("ts"),
            }
            fluxo["cliques"].append(item)
            pag = ev.get("pageUrl", pagina_atual)
            if pag not in fluxo["elementos_por_pagina"]:
                fluxo["elementos_por_pagina"][pag] = []
            fluxo["elementos_por_pagina"][pag].append(item)

        elif tipo == "submit":
            fluxo["submits"].append({
                "pagina": ev.get("pageUrl", pagina_atual),
                "formData": ev.get("formData"),
                "ts": ev.get("ts"),
            })

        elif tipo == "request" and ev.get("method") == "POST":
            fluxo["requests_post"].append({
                "url": ev.get("url"),
                "postData": ev.get("postData", "")[:500],
                "ts": ev.get("ts"),
            })

    return fluxo


def gerar_relatorio(fluxo: dict) -> str:
    linhas = [
        "=" * 70,
        "  RELATÓRIO DE MAPEAMENTO — MATRÍCULA ACC NO SIGAA",
        "=" * 70,
        "",
        f"  Total de páginas visitadas : {len(fluxo['paginas'])}",
        f"  Total de cliques capturados: {len(fluxo['cliques'])}",
        f"  Total de submits de form   : {len(fluxo['submits'])}",
        f"  Total de requests POST     : {len(fluxo['requests_post'])}",
        "",
        "─" * 70,
        "  SEQUÊNCIA DE PÁGINAS",
        "─" * 70,
    ]
    for i, pag in enumerate(fluxo["paginas"], 1):
        linhas.append(f"  {i:2}. {pag['url']}")

    linhas += ["", "─" * 70, "  CLIQUES DETALHADOS", "─" * 70]
    for i, clk in enumerate(fluxo["cliques"], 1):
        linhas.append(f"\n  Clique #{i}")
        linhas.append(f"    Página : {clk.get('pagina', '')[:70]}")
        linhas.append(f"    Elemento: <{clk.get('tag')}> "
                      f"id={clk.get('id')!r} name={clk.get('name')!r} "
                      f"type={clk.get('type')!r}")
        if clk.get("texto"):
            linhas.append(f"    Texto  : {clk['texto'][:60]}")
        if clk.get("xpath"):
            linhas.append(f"    XPath  : {clk['xpath'][:80]}")
        if clk.get("rowText"):
            linhas.append(f"    Linha  : {clk['rowText'][:80]}")

    linhas += ["", "─" * 70, "  FORMULÁRIOS SUBMETIDOS", "─" * 70]
    for i, sub in enumerate(fluxo["submits"], 1):
        fd = sub.get("formData") or {}
        linhas.append(f"\n  Submit #{i}")
        linhas.append(f"    Página : {sub.get('pagina', '')[:70]}")
        linhas.append(f"    Form   : id={fd.get('id')!r} action={fd.get('action','')[:60]!r}")
        for campo, valor in (fd.get("fields") or {}).items():
            linhas.append(f"    Campo  : {campo} = {str(valor)[:80]}")

    linhas += ["", "─" * 70, "  REQUESTS POST", "─" * 70]
    for i, req in enumerate(fluxo["requests_post"], 1):
        linhas.append(f"\n  POST #{i}: {req.get('url', '')[:80]}")
        if req.get("postData"):
            linhas.append(f"    Dados: {req['postData'][:200]}")

    linhas.append("")
    linhas.append("=" * 70)
    return "\n".join(linhas)


# ── Aplicar correções nos scripts ─────────────────────────────────────────────

def extrair_seletores_criticos(fluxo: dict) -> dict:
    """
    Analisa o fluxo e extrai os seletores/IDs dos elementos críticos
    que serão usados para corrigir sigaa_Matricular.py e sigaa_Consolidar.py.
    """
    seletores = {
        "login_field": None,
        "senha_field": None,
        "submit_login": None,
        "periodo_link": None,
        "portal_link": None,
        "curso_dropdown": None,
        "menu_atividades": None,
        "menu_matricular": None,
        "check_matricula": None,
        "campo_matricula": None,
        "btn_buscar": None,
        "seta_discente": None,
        "tipo_atividade": None,
        "btn_buscar_atividades": None,
        "seta_atividade": None,
        "btn_proximo": None,
        "campo_senha_confirmacao": None,
        "btn_confirmar": None,
    }

    # Analisar cliques por ordem e tentar mapear
    for clk in fluxo["cliques"]:
        eid = clk.get("id") or ""
        name = clk.get("name") or ""
        tipo = clk.get("type") or ""
        texto = (clk.get("texto") or "").lower()
        pagina = (clk.get("pagina") or "").lower()

        # Login
        if "user.login" in name or "login" in eid.lower():
            seletores["login_field"] = f"input[name='{name}']" if name else f"input[id='{eid}']"
        if "user.senha" in name or ("senha" in eid.lower() and "conf" not in pagina):
            seletores["senha_field"] = f"input[name='{name}']" if name else f"input[id='{eid}']"

        # Busca discente
        if "checkmatricula" in eid.lower() or "checkmatricula" in name.lower():
            seletores["check_matricula"] = f"[id='{eid}']" if eid else f"[name='{name}']"
        if "matriculadiscente" in eid.lower() or "matriculadiscente" in name.lower():
            seletores["campo_matricula"] = f"[id='{eid}']" if eid else f"[name='{name}']"
        if "buscar" in eid.lower() and tipo in ("submit", "button"):
            seletores["btn_buscar"] = f"[id='{eid}']"

        # Seleção de tipo de atividade
        if "idtipoatividade" in eid.lower() or "tipoatividade" in eid.lower():
            seletores["tipo_atividade"] = f"[id='{eid}']"
        if "atividades" in eid.lower() and tipo in ("submit", "button") and "busca" not in pagina.split("/")[-1]:
            seletores["btn_buscar_atividades"] = f"[id='{eid}']"

        # Próximo passo
        if "proximo" in texto or "próximo" in texto or "btnconfirmacao" in eid.lower():
            seletores["btn_proximo"] = f"[id='{eid}']" if eid else None

        # Campo senha de confirmação
        if "senha" in eid.lower() and "dados_registro" in pagina:
            seletores["campo_senha_confirmacao"] = f"[id='{eid}']"
        if "botaoconfirmarregistro" in eid.lower() or "confirmarregistro" in eid.lower():
            seletores["btn_confirmar"] = f"[id='{eid}']"

        # Seta discente (input type=image em busca_discente)
        if tipo == "image" and "busca_discente" in pagina:
            seletores["seta_discente"] = {
                "id": eid,
                "name": name,
                "tipo": "input[type='image']",
                "rowText": clk.get("rowText", ""),
            }

        # Seta atividade
        if tipo == "image" and "busca_atividade" in pagina:
            seletores["seta_atividade"] = {
                "id": eid,
                "name": name,
                "rowText": clk.get("rowText", ""),
            }

    return seletores


def gerar_correcoes(fluxo: dict, seletores: dict) -> str:
    """Gera um relatório com as correções sugeridas."""
    linhas = [
        "=" * 70,
        "  CORREÇÕES IDENTIFICADAS PARA sigaa_Matricular.py",
        "=" * 70,
        "",
    ]

    for nome, valor in seletores.items():
        if valor:
            linhas.append(f"  {nome}:")
            if isinstance(valor, dict):
                for k, v in valor.items():
                    linhas.append(f"    {k}: {v}")
            else:
                linhas.append(f"    seletor: {valor}")
            linhas.append("")

    # Analisar páginas para detectar padrões de URL
    urls = [p["url"] for p in fluxo["paginas"]]
    linhas.append("─" * 70)
    linhas.append("  URLs-CHAVE DETECTADAS")
    linhas.append("─" * 70)
    urls_chave = {}
    for url in urls:
        for chave in ["login", "calendarios", "coordenador", "busca_discente",
                      "busca_atividade", "dados_registro", "consolidar"]:
            if chave in url.lower():
                urls_chave[chave] = url
    for chave, url in urls_chave.items():
        linhas.append(f"  {chave}: {url}")

    return "\n".join(linhas)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print()

    # Fase 1: Rastreamento interativo
    _, _, _ = await rastrear()

    if not JSONL_PATH.exists():
        print("[ERRO] Arquivo de mapeamento não foi criado.")
        return

    print("[2/3] Analisando mapeamento...")
    fluxo = analisar_mapeamento(JSONL_PATH)

    # Gerar relatório legível
    relatorio = gerar_relatorio(fluxo)
    RELATORIO_PATH.write_text(relatorio, encoding="utf-8")
    print(f"  [OK] Relatório salvo em: {RELATORIO_PATH}")
    print()

    # Extrair seletores e gerar correções
    seletores = extrair_seletores_criticos(fluxo)
    correcoes = gerar_correcoes(fluxo, seletores)

    CORRECOES_PATH = Path("correcoes_sugeridas.txt")
    CORRECOES_PATH.write_text(correcoes, encoding="utf-8")
    print(f"  [OK] Correções sugeridas em: {CORRECOES_PATH}")
    print()

    # Mostrar resumo
    print("─" * 65)
    print("  RESUMO DO MAPEAMENTO")
    print("─" * 65)
    print(f"  Páginas visitadas : {len(fluxo['paginas'])}")
    print(f"  Cliques capturados: {len(fluxo['cliques'])}")
    print(f"  Submits de form   : {len(fluxo['submits'])}")
    print(f"  Requests POST     : {len(fluxo['requests_post'])}")
    print()

    if fluxo["cliques"]:
        print("  Últimos 5 cliques capturados:")
        for clk in fluxo["cliques"][-5:]:
            print(f"    <{clk.get('tag')}> id={clk.get('id')!r} "
                  f"texto={clk.get('texto','')[:40]!r}")

    print()

    # Fase 3: Aplicar correções
    resposta = input("[3/3] Deseja aplicar as correções automaticamente? [s/N] ").strip().lower()
    if resposta in ("s", "sim", "y", "yes"):
        aplicar_correcoes(fluxo, seletores)
    else:
        print()
        print("  Correções NÃO aplicadas.")
        print(f"  Revise {CORRECOES_PATH} e {RELATORIO_PATH} manualmente.")
        print()

    print("=" * 65)
    print("  RASTREAMENTO CONCLUÍDO")
    print("=" * 65)
    print()
    print(f"  Arquivos gerados:")
    print(f"    {JSONL_PATH}  ← eventos brutos")
    print(f"    {RELATORIO_PATH}  ← relatório legível")
    print(f"    {CORRECOES_PATH}  ← correções sugeridas")
    print()


def aplicar_correcoes(fluxo: dict, seletores: dict):
    """
    Aplica as correções identificadas em sigaa_Matricular.py.
    Apenas substitui seletores onde temos informação confiável do mapeamento.
    """
    script_path = Path("sigaa_Matricular.py")
    if not script_path.exists():
        print(f"  [ERRO] {script_path} não encontrado.")
        return

    conteudo = script_path.read_text(encoding="utf-8")
    modificacoes = 0

    # Aplicar seletores críticos que foram mapeados
    substituicoes = {}

    eid_check = seletores.get("check_matricula")
    if eid_check and "formulario:checkMatricula" not in eid_check:
        substituicoes['"formulario:checkMatricula"'] = f'"{eid_check.lstrip("[").rstrip("]").replace(chr(39), chr(34))}"'

    eid_mat = seletores.get("campo_matricula")
    if eid_mat and "formulario:matriculaDiscente" not in eid_mat:
        substituicoes['"formulario:matriculaDiscente"'] = f'"{eid_mat.lstrip("[").rstrip("]").replace(chr(39), chr(34))}"'

    eid_buscar = seletores.get("btn_buscar")
    if eid_buscar and "formulario:buscar" not in eid_buscar:
        substituicoes['"formulario:buscar"'] = f'"{eid_buscar.lstrip("[").rstrip("]").replace(chr(39), chr(34))}"'

    eid_tipo = seletores.get("tipo_atividade")
    if eid_tipo and "form:idTipoAtividade" not in eid_tipo:
        substituicoes['"form:idTipoAtividade"'] = f'"{eid_tipo.lstrip("[").rstrip("]").replace(chr(39), chr(34))}"'

    eid_senha = seletores.get("campo_senha_confirmacao")
    if eid_senha and "form:senha" not in eid_senha:
        substituicoes['"form:senha"'] = f'"{eid_senha.lstrip("[").rstrip("]").replace(chr(39), chr(34))}"'

    eid_confirmar = seletores.get("btn_confirmar")
    if eid_confirmar and "form:botaoConfirmarRegistro" not in eid_confirmar:
        substituicoes['"form:botaoConfirmarRegistro"'] = f'"{eid_confirmar.lstrip("[").rstrip("]").replace(chr(39), chr(34))}"'

    for antigo, novo in substituicoes.items():
        if antigo in conteudo and antigo != novo:
            conteudo = conteudo.replace(antigo, novo)
            modificacoes += 1
            print(f"  [FIX] Substituiu {antigo} → {novo}")

    if modificacoes > 0:
        # Fazer backup antes de modificar
        backup_path = script_path.with_suffix(f".bak_{TIMESTAMP}.py")
        backup_path.write_text(script_path.read_text(encoding="utf-8"), encoding="utf-8")
        script_path.write_text(conteudo, encoding="utf-8")
        print(f"  [OK] {modificacoes} correção(ões) aplicada(s) em {script_path}")
        print(f"  [OK] Backup salvo em {backup_path}")
    else:
        print("  [INFO] Nenhuma substituição automática identificada.")
        print("        Revise o relatório e corrija manualmente se necessário.")


if __name__ == "__main__":
    asyncio.run(main())
