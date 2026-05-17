"""
rastreador_tcc.py — Agente de rastreamento interativo do SIGAA para TCC.

COMO USAR:
  python rastreador_tcc.py

O script abre o browser VISÍVEL. Navegue normalmente e faça o processo
COMPLETO de matrícula E consolidação de TCC de um aluno. O agente registra:
  - Cada página visitada (URL, título)
  - Cada clique (elemento, id, name, tipo, texto, xpath)
  - Cada formulário submetido (campos e valores)
  - Requests HTTP relevantes (URL, método, corpo POST)

ATENÇÃO — realize os dois fluxos em sequência:
  1. Matrícula de TCC (inclui campo orientador)
  2. Consolidação de TCC (inclui seleção de conceito)

Ao fechar o browser (ou pressionar Ctrl+C), salva em:
  mapeamento_tcc_<timestamp>.jsonl
  relatorio_tcc.txt
  correcoes_tcc.txt

Em seguida, pergunta se deseja aplicar as correções automaticamente.
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

# ── Arquivos de saída ──────────────────────────────────────────────────────────
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
JSONL_PATH = Path(f"mapeamento_tcc_{TIMESTAMP}.jsonl")
RELATORIO_PATH = Path("relatorio_tcc.txt")
CORRECOES_PATH = Path("correcoes_tcc.txt")

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
        // Para autocomplete do orientador: capturar item de sugestão
        var autocompleteItem = el.closest('li[id*="suggest"]') || el.closest('div[class*="autocomplete"]');
        return {
            tag: el.tagName,
            id: el.id || null,
            name: el.getAttribute('name') || null,
            type: el.getAttribute('type') || null,
            value: (el.value || '').substring(0, 300),
            text: (el.textContent || el.innerText || '').trim().substring(0, 300),
            href: el.getAttribute('href') || null,
            onclick: (el.getAttribute('onclick') || '').substring(0, 300),
            src: el.getAttribute('src') || null,
            alt: el.getAttribute('alt') || null,
            title: el.getAttribute('title') || null,
            className: el.className || null,
            xpath: getXPath(el),
            formId: form ? form.id : null,
            formAction: form ? form.action : null,
            rowText: tr ? tr.textContent.trim().substring(0, 300) : null,
            autocompleteText: autocompleteItem ? autocompleteItem.textContent.trim().substring(0, 200) : null,
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

    // Também capturar keydown (ArrowDown/Enter usados no autocomplete do orientador)
    document.addEventListener('keydown', function(e) {
        if (!['ArrowDown', 'ArrowUp', 'Enter', 'Tab'].includes(e.key)) return;
        var el = e.target;
        var det = getDetails(el);
        det.pageUrl = window.location.href;
        det.pageTitle = document.title;
        det.ts = Date.now();
        det.eventType = 'keydown';
        det.key = e.key;
        window.__sigaaEventos.push(det);
    }, true);

    document.addEventListener('click', function(e) {
        var el = e.target;
        for (var i = 0; i < 5; i++) {
            if (!el) break;
            if (['A','INPUT','BUTTON','IMG','LI'].includes(el.tagName)) break;
            el = el.parentElement;
        }
        if (!el) el = e.target;

        var det = getDetails(el);
        det.pageUrl = window.location.href;
        det.pageTitle = document.title;
        det.ts = Date.now();
        det.eventType = 'click';
        det.viewState = (document.querySelector('input[name="javax.faces.ViewState"]') || {}).value;

        var form = el.closest('form');
        if (el.type === 'submit' || el.type === 'image' || (el.tagName === 'A' && el.onclick)) {
            det.formData = getFormData(form);
        }

        window.__sigaaEventos.push(det);
    }, true);

    // Capturar mudanças de valor em inputs (orientador, conceito, etc.)
    document.addEventListener('change', function(e) {
        var el = e.target;
        if (!el.name && !el.id) return;
        var det = getDetails(el);
        det.pageUrl = window.location.href;
        det.pageTitle = document.title;
        det.ts = Date.now();
        det.eventType = 'change';
        window.__sigaaEventos.push(det);
    }, true);

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
    print("  AGENTE RASTREADOR DO SIGAA — MATRÍCULA E CONSOLIDAÇÃO TCC")
    print("=" * 65)
    print()
    print("  INSTRUÇÕES:")
    print()
    print("  FLUXO 1 — MATRÍCULA TCC:")
    print("    Login → Período → Portal Coord. Graduação → Curso/Polo")
    print("    → Atividades > Matricular → Buscar Discente")
    print("    → Tipo de Atividade → Campo ORIENTADOR (autocomplete)")
    print("    → Buscar → Selecionar → Próximo Passo → Senha → Confirmar")
    print()
    print("  FLUXO 2 — CONSOLIDAÇÃO TCC (na mesma sessão ou nova):")
    print("    Portal Coord. Graduação → Atividades > Consolidar Matrículas")
    print("    → Buscar Discente → Selecionar Conceito → Próximo Passo → Confirmar")
    print()
    print("  Tudo será registrado automaticamente em:")
    print(f"    {JSONL_PATH}")
    print()
    print("  Quando terminar AMBOS os fluxos, feche o browser ou Ctrl+C.")
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

            await context.add_init_script(_JS_CAPTURE)

            page = await context.new_page()

            def on_request(request):
                if request.method == "POST" or "jsf" in request.url or "sigaa" in request.url:
                    ev = {
                        "eventType": "request",
                        "ts": datetime.now().isoformat(),
                        "method": request.method,
                        "url": request.url,
                        "postData": (request.post_data or "")[:3000],
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

            if sigaa_url:
                await page.goto(sigaa_url, wait_until="domcontentloaded")
            else:
                await page.goto("about:blank")

            print()
            print("  [INFO] Browser aberto. Realize os dois fluxos (matrícula + consolidação).")
            print("  [INFO] Preste atenção especial ao campo ORIENTADOR no fluxo de matrícula.")
            print()

            ultimo_idx = 0

            try:
                while True:
                    await asyncio.sleep(1)

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
                            elif tipo == "keydown":
                                eid = ev.get("id") or ev.get("name") or ""
                                print(f"  [KEY] {ev.get('key')!r} em id={eid!r}")
                            elif tipo == "change":
                                eid = ev.get("id") or ev.get("name") or ""
                                val = (ev.get("value") or ev.get("text") or "")[:40]
                                print(f"  [CHANGE] id={eid!r} valor={val!r}")
                            elif tipo == "submit":
                                fd = ev.get("formData") or {}
                                print(f"  [SUBMIT] form={fd.get('id')!r} action={fd.get('action', '')[:60]!r}")

                    except Exception:
                        pass

            except asyncio.CancelledError:
                pass
            except KeyboardInterrupt:
                pass
            finally:
                try:
                    screenshot_path = f"/tmp/sigaa_tcc_final_{TIMESTAMP}.png"
                    await page.screenshot(path=screenshot_path)
                    print(f"\n  [INFO] Screenshot final salvo em {screenshot_path}")
                except Exception:
                    pass

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


# ── Analisar mapeamento ────────────────────────────────────────────────────────

def analisar_mapeamento(jsonl_path: Path) -> dict:
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
        "keydowns": [],
        "changes": [],
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
                "value": ev.get("value", ""),
                "texto": ev.get("text", "")[:100],
                "xpath": ev.get("xpath"),
                "onclick": ev.get("onclick", "")[:100],
                "formAction": ev.get("formAction"),
                "rowText": ev.get("rowText", "")[:100],
                "autocompleteText": ev.get("autocompleteText"),
                "ts": ev.get("ts"),
            }
            fluxo["cliques"].append(item)
            pag = ev.get("pageUrl", pagina_atual)
            if pag not in fluxo["elementos_por_pagina"]:
                fluxo["elementos_por_pagina"][pag] = []
            fluxo["elementos_por_pagina"][pag].append(item)

        elif tipo == "keydown":
            fluxo["keydowns"].append({
                "pagina": ev.get("pageUrl", pagina_atual),
                "key": ev.get("key"),
                "id": ev.get("id"),
                "name": ev.get("name"),
                "value": ev.get("value", ""),
                "ts": ev.get("ts"),
            })

        elif tipo == "change":
            fluxo["changes"].append({
                "pagina": ev.get("pageUrl", pagina_atual),
                "id": ev.get("id"),
                "name": ev.get("name"),
                "value": ev.get("value", "")[:200],
                "text": ev.get("text", "")[:200],
                "ts": ev.get("ts"),
            })

        elif tipo == "submit":
            fluxo["submits"].append({
                "pagina": ev.get("pageUrl", pagina_atual),
                "formData": ev.get("formData"),
                "ts": ev.get("ts"),
            })

        elif tipo == "request" and ev.get("method") == "POST":
            fluxo["requests_post"].append({
                "url": ev.get("url"),
                "postData": ev.get("postData", "")[:1000],
                "ts": ev.get("ts"),
            })

    return fluxo


def gerar_relatorio(fluxo: dict) -> str:
    linhas = [
        "=" * 70,
        "  RELATÓRIO DE MAPEAMENTO — TCC NO SIGAA",
        "=" * 70,
        "",
        f"  Total de páginas visitadas : {len(fluxo['paginas'])}",
        f"  Total de cliques capturados: {len(fluxo['cliques'])}",
        f"  Total de keydowns          : {len(fluxo['keydowns'])}",
        f"  Total de changes           : {len(fluxo['changes'])}",
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
            linhas.append(f"    Texto  : {clk['texto'][:80]}")
        if clk.get("rowText"):
            linhas.append(f"    Linha  : {clk['rowText'][:80]}")
        if clk.get("autocompleteText"):
            linhas.append(f"    Autocomplete: {clk['autocompleteText'][:80]}")

    if fluxo["keydowns"]:
        linhas += ["", "─" * 70, "  TECLAS ESPECIAIS (autocomplete)", "─" * 70]
        for kd in fluxo["keydowns"]:
            linhas.append(f"  [{kd.get('key')}] em id={kd.get('id')!r} name={kd.get('name')!r} "
                          f"valor={kd.get('value','')[:40]!r}")

    if fluxo["changes"]:
        linhas += ["", "─" * 70, "  MUDANÇAS DE VALOR (selects, inputs)", "─" * 70]
        for ch in fluxo["changes"]:
            linhas.append(f"  id={ch.get('id')!r} name={ch.get('name')!r} "
                          f"→ {ch.get('value','')[:60]!r}  [{ch.get('text','')[:40]!r}]")

    linhas += ["", "─" * 70, "  FORMULÁRIOS SUBMETIDOS", "─" * 70]
    for i, sub in enumerate(fluxo["submits"], 1):
        fd = sub.get("formData") or {}
        linhas.append(f"\n  Submit #{i}")
        linhas.append(f"    Página : {sub.get('pagina', '')[:70]}")
        linhas.append(f"    Form   : id={fd.get('id')!r} action={fd.get('action','')[:60]!r}")
        for campo, valor in (fd.get("fields") or {}).items():
            linhas.append(f"    Campo  : {campo} = {str(valor)[:100]}")

    linhas += ["", "─" * 70, "  REQUESTS POST", "─" * 70]
    for i, req in enumerate(fluxo["requests_post"], 1):
        linhas.append(f"\n  POST #{i}: {req.get('url', '')[:80]}")
        if req.get("postData"):
            linhas.append(f"    Dados: {req['postData'][:400]}")

    linhas.append("")
    linhas.append("=" * 70)
    return "\n".join(linhas)


# ── Extração de seletores críticos ────────────────────────────────────────────

def extrair_seletores_criticos(fluxo: dict) -> dict:
    """
    Extrai os IDs e seletores dos elementos críticos do fluxo TCC.
    Inclui campos específicos de TCC: orientador, conceito na consolidação.
    """
    seletores = {
        # Busca discente (matrícula)
        "check_matricula": None,
        "campo_matricula": None,
        "btn_buscar_discente": None,
        "seta_discente_matricula": None,

        # Tipo de atividade
        "radio_tipo_atividade": None,
        "dropdown_tipo_atividade": None,

        # Orientador (autocomplete — específico TCC)
        "campo_orientador": None,
        "campo_orientador_id_hidden": None,  # id oculto após selecionar

        # Busca de atividade
        "btn_buscar_atividade": None,
        "campo_nome_atividade": None,
        "seta_atividade": None,

        # Próximo passo / confirmação
        "btn_proximo": None,
        "campo_senha_confirmacao": None,
        "btn_confirmar": None,

        # Consolidação
        "campo_matricula_consolidar": None,
        "btn_buscar_discente_consolidar": None,
        "seta_discente_consolidar": None,
        "select_conceito": None,
        "btn_proximo_consolidar": None,
        "btn_confirmar_consolidar": None,
    }

    # Analisar mudanças de valor para capturar orientador e conceito
    for ch in fluxo.get("changes", []):
        eid = ch.get("id") or ""
        name = ch.get("name") or ""
        valor = ch.get("value") or ""
        pagina = (ch.get("pagina") or "").lower()

        if "orientador" in eid.lower() or "orientador" in name.lower():
            seletores["campo_orientador"] = f"[id='{eid}']" if eid else f"[name='{name}']"

        if "conceito" in eid.lower() or "conceito" in name.lower():
            seletores["select_conceito"] = f"[id='{eid}']" if eid else f"[name='{name}']"

    # Analisar cliques para mapear elementos da UI
    for clk in fluxo["cliques"]:
        eid = clk.get("id") or ""
        name = clk.get("name") or ""
        tipo = clk.get("type") or ""
        texto = (clk.get("texto") or "").lower()
        pagina = (clk.get("pagina") or "").lower()
        row = (clk.get("rowText") or "").lower()

        # Radio tipo atividade
        if "tipoatividade" in eid.lower() and tipo == "radio":
            seletores["radio_tipo_atividade"] = f"[id='{eid}']" if eid else f"[name='{name}']"

        # Dropdown tipo atividade
        if "idtipoatividade" in eid.lower():
            seletores["dropdown_tipo_atividade"] = f"[id='{eid}']"

        # Campo matrícula discente (busca_discente.jsf)
        if "checkmatricula" in eid.lower() or "checkmatricula" in name.lower():
            seletores["check_matricula"] = f"[id='{eid}']" if eid else f"[name='{name}']"
        if "matriculadiscente" in eid.lower() or "matriculadiscente" in name.lower():
            seletores["campo_matricula"] = f"[id='{eid}']" if eid else f"[name='{name}']"

        # Botão buscar discente
        if ("buscar" in eid.lower() or "buscar" in texto) and tipo in ("submit", "button"):
            if "busca_discente" in pagina or "discente" in pagina:
                seletores["btn_buscar_discente"] = f"[id='{eid}']" if eid else None
            elif "consolidar" in pagina:
                seletores["btn_buscar_discente_consolidar"] = f"[id='{eid}']" if eid else None

        # Seta selecionar discente (input type=image)
        if tipo == "image":
            if "busca_discente" in pagina or "selecionardiscente" in name.lower():
                if "consolidar" in pagina:
                    seletores["seta_discente_consolidar"] = {"id": eid, "name": name, "rowText": row}
                else:
                    seletores["seta_discente_matricula"] = {"id": eid, "name": name, "rowText": row}

        # Campo orientador (input de texto para autocomplete)
        if "orientador" in eid.lower() or "orientador" in name.lower():
            if tipo in ("text", None, ""):
                seletores["campo_orientador"] = f"[id='{eid}']" if eid else f"[name='{name}']"

        # Campo nome atividade
        if "nomeatividade" in eid.lower() or "nomeatividade" in name.lower():
            seletores["campo_nome_atividade"] = f"[id='{eid}']" if eid else f"[name='{name}']"

        # Botão buscar atividade
        if "buscar" in eid.lower() and tipo in ("submit", "button") and "busca_atividade" in pagina:
            seletores["btn_buscar_atividade"] = f"[id='{eid}']"

        # Seta selecionar atividade
        if tipo == "image" and "busca_atividade" in pagina:
            seletores["seta_atividade"] = {"id": eid, "name": name, "rowText": row}

        # Próximo passo
        if "proximo" in texto or "próximo" in texto or "btnconfirmacao" in eid.lower():
            if "consolidar" in pagina:
                seletores["btn_proximo_consolidar"] = f"[id='{eid}']" if eid else None
            else:
                seletores["btn_proximo"] = f"[id='{eid}']" if eid else None

        # Select conceito (consolidação)
        if "conceito" in eid.lower() or "conceito" in name.lower():
            seletores["select_conceito"] = f"[id='{eid}']" if eid else f"[name='{name}']"

        # Campo senha confirmação
        if "senha" in eid.lower() and "dados_registro" in pagina:
            seletores["campo_senha_confirmacao"] = f"[id='{eid}']"

        # Botão confirmar
        if "confirmarregistro" in eid.lower() or "botaoconfirmar" in eid.lower():
            if "consolidar" in pagina:
                seletores["btn_confirmar_consolidar"] = f"[id='{eid}']"
            else:
                seletores["btn_confirmar"] = f"[id='{eid}']"

    # Analisar POST data para descobrir campos ocultos do orientador
    for req in fluxo.get("requests_post", []):
        pd = req.get("postData", "")
        # Procurar por campos orientador na POST data
        for part in pd.split("&"):
            if "orientador" in part.lower() and "=" in part:
                campo, _, valor = part.partition("=")
                if campo and "id" in campo.lower():
                    seletores["campo_orientador_id_hidden"] = f"[name='{campo}']"

    return seletores


def gerar_correcoes(fluxo: dict, seletores: dict) -> str:
    linhas = [
        "=" * 70,
        "  CORREÇÕES IDENTIFICADAS PARA sigaa_Matricular_TCC.py e sigga_Consolidar_TCC.py",
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

    urls = [p["url"] for p in fluxo["paginas"]]
    linhas.append("─" * 70)
    linhas.append("  URLs-CHAVE DETECTADAS")
    linhas.append("─" * 70)
    urls_chave = {}
    for url in urls:
        for chave in ["login", "calendarios", "coordenador", "busca_discente",
                      "busca_atividade", "dados_registro", "consolidar", "resumo"]:
            if chave in url.lower():
                urls_chave[chave] = url
    for chave, url in urls_chave.items():
        linhas.append(f"  {chave}: {url}")

    # Análise especial: campo orientador nos POSTs
    linhas += ["", "─" * 70, "  ANÁLISE DE CAMPOS TCC NOS POSTs", "─" * 70]
    for i, req in enumerate(fluxo["requests_post"], 1):
        pd = req.get("postData", "")
        if "orientador" in pd.lower() or "tcc" in pd.lower():
            linhas.append(f"\n  POST #{i} (contém dados TCC): {req.get('url','')[:70]}")
            for part in pd.split("&"):
                if any(k in part.lower() for k in ["orientador", "tcc", "tipo", "atividade", "conceito"]):
                    linhas.append(f"    {part[:120]}")

    return "\n".join(linhas)


# ── Aplicar correções nos scripts TCC ─────────────────────────────────────────

def aplicar_correcoes(fluxo: dict, seletores: dict):
    """
    Aplica as correções identificadas em sigaa_Matricular_TCC.py
    e sigga_Consolidar_TCC.py.
    """
    print()

    # ── sigaa_Matricular_TCC.py ───────────────────────────────────────────────
    script_mat = Path("sigaa_Matricular_TCC.py")
    if script_mat.exists():
        conteudo = script_mat.read_text(encoding="utf-8")
        modificacoes = 0
        substituicoes = {}

        if seletores.get("seta_discente_matricula"):
            info = seletores["seta_discente_matricula"]
            if info.get("name") and info["name"] not in conteudo:
                substituicoes['"form:selecionarDiscente"'] = f'"form:selecionarDiscente"'

        if seletores.get("campo_nome_atividade"):
            sel = seletores["campo_nome_atividade"]
            eid = sel.strip("[']").replace("id='", "").replace("'", "")
            if eid and eid not in conteudo:
                substituicoes['"form:nomeAtividadeInput"'] = f'"{eid}"'

        if seletores.get("radio_tipo_atividade"):
            sel = seletores["radio_tipo_atividade"]
            eid = sel.strip("[']").replace("id='", "").replace("'", "")
            if eid and eid not in conteudo:
                substituicoes['"form:tipoAtividade"'] = f'"{eid}"'

        for antigo, novo in substituicoes.items():
            if antigo in conteudo and antigo != novo:
                conteudo = conteudo.replace(antigo, novo)
                modificacoes += 1
                print(f"  [FIX matrícula TCC] {antigo} → {novo}")

        if modificacoes > 0:
            backup = script_mat.with_suffix(f".bak_{TIMESTAMP}.py")
            backup.write_text(script_mat.read_text(encoding="utf-8"), encoding="utf-8")
            script_mat.write_text(conteudo, encoding="utf-8")
            print(f"  [OK] {modificacoes} fix(es) em {script_mat} (backup: {backup.name})")
        else:
            print(f"  [INFO] Nenhuma substituição automática em {script_mat}.")

    # ── sigga_Consolidar_TCC.py ───────────────────────────────────────────────
    script_con = Path("sigga_Consolidar_TCC.py")
    if script_con.exists():
        conteudo = script_con.read_text(encoding="utf-8")
        modificacoes = 0

        # Correção crítica: hardcoded CONCEITO_PADRAO="E" → usar args.conceito
        if 'CONCEITO_PADRAO = "E"' in conteudo:
            # Checar se a função já usa args.conceito
            if "args.conceito" not in conteudo:
                conteudo = conteudo.replace(
                    'CONCEITO_PADRAO = "E"',
                    'CONCEITO_PADRAO = "E"  # substituído dinamicamente por args.conceito'
                )
                modificacoes += 1
                print(f"  [FIX consolidar TCC] Marcado CONCEITO_PADRAO para revisão manual.")

        if seletores.get("seta_discente_consolidar"):
            info = seletores["seta_discente_consolidar"]
            name = info.get("name", "")
            if name and name not in conteudo:
                modificacoes += 1
                print(f"  [FIX consolidar TCC] seta_discente name={name!r} (ajuste manual necessário).")

        if seletores.get("select_conceito"):
            sel = seletores["select_conceito"]
            eid = sel.strip("[']").replace("id='", "").replace("'", "")
            if eid and eid not in conteudo:
                modificacoes += 1
                print(f"  [INFO consolidar TCC] select_conceito id={eid!r} — ajuste manual necessário.")

        if modificacoes > 0:
            backup = script_con.with_suffix(f".bak_{TIMESTAMP}.py")
            backup.write_text(script_con.read_text(encoding="utf-8"), encoding="utf-8")
            script_con.write_text(conteudo, encoding="utf-8")
            print(f"  [OK] {modificacoes} fix(es) em {script_con} (backup: {backup.name})")
        else:
            print(f"  [INFO] Nenhuma substituição automática em {script_con}.")

    print()
    print("  IMPORTANTE: Após analisar o relatório, ajuste manualmente:")
    print("    1. sigaa_Matricular_TCC.py — radio form:tipoAtividade + selecionarDiscente")
    print("    2. sigga_Consolidar_TCC.py — args.conceito em vez de CONCEITO_PADRAO")
    print(f"  Consulte: {RELATORIO_PATH} e {CORRECOES_PATH}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print()

    _, _, _ = await rastrear()

    if not JSONL_PATH.exists():
        print("[ERRO] Arquivo de mapeamento não foi criado.")
        return

    print("[2/3] Analisando mapeamento TCC...")
    fluxo = analisar_mapeamento(JSONL_PATH)

    relatorio = gerar_relatorio(fluxo)
    RELATORIO_PATH.write_text(relatorio, encoding="utf-8")
    print(f"  [OK] Relatório salvo em: {RELATORIO_PATH}")

    seletores = extrair_seletores_criticos(fluxo)
    correcoes = gerar_correcoes(fluxo, seletores)
    CORRECOES_PATH.write_text(correcoes, encoding="utf-8")
    print(f"  [OK] Correções sugeridas em: {CORRECOES_PATH}")
    print()

    print("─" * 65)
    print("  RESUMO DO MAPEAMENTO TCC")
    print("─" * 65)
    print(f"  Páginas visitadas : {len(fluxo['paginas'])}")
    print(f"  Cliques capturados: {len(fluxo['cliques'])}")
    print(f"  Keydowns          : {len(fluxo['keydowns'])}")
    print(f"  Changes           : {len(fluxo['changes'])}")
    print(f"  Submits de form   : {len(fluxo['submits'])}")
    print(f"  Requests POST     : {len(fluxo['requests_post'])}")
    print()

    print("  Seletores identificados:")
    for nome, valor in seletores.items():
        if valor:
            print(f"    {nome}: {valor}")
    print()

    if fluxo["requests_post"]:
        print("  POSTs capturados:")
        for i, req in enumerate(fluxo["requests_post"][:5], 1):
            print(f"    #{i} {req.get('url','')[:70]}")
    print()

    resposta = input("[3/3] Deseja aplicar as correções automaticamente? [s/N] ").strip().lower()
    if resposta in ("s", "sim", "y", "yes"):
        aplicar_correcoes(fluxo, seletores)
    else:
        print()
        print("  Correções NÃO aplicadas.")
        print(f"  Revise {CORRECOES_PATH} e {RELATORIO_PATH} manualmente.")

    print()
    print("=" * 65)
    print("  RASTREAMENTO TCC CONCLUÍDO")
    print("=" * 65)
    print()
    print(f"  Arquivos gerados:")
    print(f"    {JSONL_PATH}  ← eventos brutos")
    print(f"    {RELATORIO_PATH}  ← relatório legível")
    print(f"    {CORRECOES_PATH}  ← correções sugeridas")
    print()


if __name__ == "__main__":
    asyncio.run(main())
