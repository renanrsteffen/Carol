"""Gerador de Lançamentos Contábeis — interface (Streamlit).

Rodar:  streamlit run app.py
"""
from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from core import armazenamento as arm
from core.classificador import Classificador, Contexto
from core.estado import competencia, desempacotar, empacotar
from core.exportar import ROTULO_STATUS, gerar_conferencia, gerar_siger, validar
from core.extratos import ler_extrato
from core.financeiro import ler_financeiro
from core.modelos import Lancamento
from core.perfil import MODELOS_PADRAO, Perfil, Regra
from core.razao import Razao, apurar_titulos, ler_plano
from core.util import fmt_data, fmt_valor

st.set_page_config(page_title="Gerador de Lançamentos", page_icon="📒", layout="wide")

STATUS_OPCOES = ["OK", "Revisar", "Pendente", "Já lançado", "Ignorar"]
STATUS_DE = {v: k for k, v in ROTULO_STATUS.items()}
SS = st.session_state


# ------------------------------------------------------------------------------------ estado
def init():
    SS.setdefault("perfil", None)
    SS.setdefault("lancs", [])
    SS.setdefault("titulos", [])
    SS.setdefault("titulos_inicio", [])
    SS.setdefault("razao", None)
    SS.setdefault("movs", [])
    SS.setdefault("totais", {})
    SS.setdefault("versao", 0)
    SS.setdefault("editados", set())
    SS.setdefault("forcados", {})
    SS.setdefault("sel_mov", None)
    SS.setdefault("salvo_em", None)
    if "backup_feito" not in SS:
        SS.backup_feito = True
        try:
            arm.backup_automatico()
        except OSError as e:
            st.warning(f"Não foi possível fazer o backup automático: {e}")


init()


def salvar_trabalho_atual():
    """Grava o trabalho do mês em dados/trabalhos (chamado após cada alteração)."""
    if not SS.perfil or not SS.lancs:
        return
    try:
        arm.salvar_trabalho(SS.perfil.nome, competencia(SS.lancs), empacotar(
            lancs=SS.lancs, movs=SS.movs, titulos_inicio=SS.titulos_inicio, titulos=SS.titulos, razao=SS.razao,
            totais=SS.totais, financeiro=SS.get("financeiro_itens", []), forcados=SS.forcados))
        SS.salvo_em = datetime.now()
    except OSError as e:
        st.error(f"Erro ao salvar o trabalho: {e}")


def abrir_trabalho(nome_cliente: str, comp: str):
    d = arm.carregar_trabalho(nome_cliente, comp)
    if not d:
        st.error("Não consegui abrir esse trabalho.")
        return
    SS.update(desempacotar(d))
    SS.salvo_em = d.get("_salvo_em")
    SS.versao += 1


def descricao_curta_ui(t: str, n: int = 48) -> str:
    t = " ".join(str(t).split())
    return t if len(t) <= n else t[: n - 1] + "…"


def rotulo(cod: str) -> str:
    r: Razao | None = SS.razao
    if not cod:
        return ""
    if r and cod in r.contas:
        return f"{cod} - {r.contas[cod].nome}"
    return str(cod)


def cod_de(rot: str) -> str:
    return str(rot or "").split(" - ", 1)[0].strip()


def opcoes_contas() -> list[str]:
    r: Razao | None = SS.razao
    if not r:
        return []
    return [""] + [f"{c.codigo} - {c.nome}" for c in sorted(r.contas.values(), key=lambda c: (c.classificacao or c.codigo))]


# ---------------------------------------------------------------------------------- barra lateral
with st.sidebar:
    st.title("📒 Lançamentos")
    st.caption("Extratos + razão → lançamentos para importar no SIGER")
    existentes = Perfil.listar()
    escolha = st.selectbox("Cliente", ["(novo cliente)"] + existentes,
                           index=(existentes.index(SS.perfil.nome) + 1) if SS.perfil and SS.perfil.nome in existentes else 0)
    if escolha == "(novo cliente)":
        novo = st.text_input("Nome do novo cliente")
        if st.button("Criar cliente", width="stretch"):
            if not novo.strip():
                st.warning("Digite o nome do cliente.")
            else:
                SS.perfil = Perfil.carregar(novo.strip())
                SS.perfil.salvar()
                st.rerun()
    elif SS.perfil is None or SS.perfil.nome != escolha:
        SS.perfil = Perfil.carregar(escolha)
        SS.lancs, SS.titulos, SS.titulos_inicio, SS.razao, SS.movs, SS.salvo_em = [], [], [], None, [], None
        SS.forcados, SS.sel_mov = {}, None
        trabalhos = arm.listar_trabalhos(escolha)
        if trabalhos:  # reabre automaticamente o trabalho mais recente
            abrir_trabalho(escolha, trabalhos[0][0])

    perfil: Perfil | None = SS.perfil
    if perfil:
        st.divider()
        perfil.lote = st.text_input("Número do lote", perfil.lote)
        perfil.descricao_lote = st.text_input("Descrição do lote", perfil.descricao_lote,
                                              placeholder="BANCO BB NOV/2026")
        if st.button("💾 Salvar cliente", width="stretch"):
            perfil.salvar()
            st.toast("Cliente salvo")
        st.caption(f"{len(perfil.memoria)} históricos memorizados · {len(perfil.apelidos)} nomes · {len(perfil.cnpj_conta)} CNPJs")
        trabalhos = arm.listar_trabalhos(perfil.nome)
        if trabalhos:
            st.divider()
            rot = {f"{c[5:7]}/{c[:4]}  (salvo {dt:%d/%m %H:%M})": c for c, dt in trabalhos}
            esc_t = st.selectbox("Trabalhos salvos", list(rot))
            if st.button("📂 Abrir trabalho", width="stretch"):
                abrir_trabalho(perfil.nome, rot[esc_t])
                st.rerun()
        if SS.salvo_em:
            st.caption(f"💾 Trabalho salvo automaticamente às {SS.salvo_em:%H:%M:%S}")

if not SS.perfil:
    st.header("Gerador de Lançamentos Contábeis")
    st.info("Escolha ou crie um cliente na barra lateral para começar.")
    st.markdown(
        """
**Como funciona**
1. Envie o **razão** (de preferência com alguns meses anteriores, para o programa conhecer as notas em aberto e como você costuma lançar).
2. Envie os **extratos** (OFX é o mais confiável; também aceita Excel/CSV e PDF) e, se tiver, o **relatório financeiro** do cliente.
3. Revise as sugestões — verde é OK, amarelo é revisar, vermelho é pendente — e marque **Memorizar** no que corrigir.
4. Baixe o arquivo de importação do **SIGER** e a planilha de conferência.
"""
    )
    st.stop()

perfil: Perfil = SS.perfil
aba_arq, aba_lanc, aba_notas, aba_exp, aba_cfg, aba_bkp = st.tabs(
    ["1 · Arquivos", "2 · Lançamentos", "3 · Notas em aberto", "4 · Exportar", "⚙️ Configurações", "🛟 Backup"])


# ------------------------------------------------------------------------------------ 1. arquivos
def reclassificar():
    """Refaz a classificação a partir das notas iniciais, aplicando as decisões do operador.
    Assim, notas escolhidas num movimento não são usadas de novo em outro."""
    if not SS.movs:
        return
    titulos = copy.deepcopy(SS.titulos_inicio)
    itens = [copy.copy(i) for i in SS.get("financeiro_itens", [])]
    for i in itens:
        i.usado = False
    clf = Classificador(Contexto(perfil=perfil, razao=SS.razao, titulos=titulos, financeiro=itens, forcados=SS.forcados))
    novos = clf.classificar(SS.movs)
    novos.sort(key=lambda l: (l.data, l.conta_banco, l.mov_id))
    SS.update(lancs=novos, titulos=clf.titulos, contas_regra=clf.contas_regra)
    SS.versao += 1
    salvar_trabalho_atual()


def processar(razoes, plano, extratos_cfg, financeiros):
    razao = Razao.carregar(razoes) if razoes else Razao()
    if plano:
        razao.aplicar_plano(ler_plano(plano))
    for cod, tipo in perfil.tipos_conta.items():
        if cod in razao.contas:
            razao.contas[cod].tipo = tipo
    titulos = apurar_titulos(razao, perfil.tipos_conta)
    itens = []
    for f in financeiros or []:
        itens.extend(ler_financeiro(f))
    movs, totais = [], {}
    for movs_arq, conta in extratos_cfg:
        for m in movs_arq:
            m.conta_banco = conta
        movs.extend(movs_arq)
        e = sum(m.valor for m in movs_arq if m.valor > 0)
        s = -sum(m.valor for m in movs_arq if m.valor < 0)
        te, ts = totais.get(conta, (0.0, 0.0))
        totais[conta] = (te + e, ts + s)
    ids = {m.id for m in movs}
    SS.forcados = {k: v for k, v in SS.forcados.items() if k in ids}  # decisões anteriores dos mesmos movimentos
    SS.update(titulos_inicio=titulos, razao=razao, movs=movs, totais=totais, financeiro_itens=itens, sel_mov=None)
    reclassificar()


with aba_arq:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Razão e plano de contas")
        razoes = st.file_uploader("Razão (Excel/CSV) — pode enviar mais de um", type=["xlsx", "xls", "csv"],
                                  accept_multiple_files=True, key="up_razao")
        plano = st.file_uploader("Plano de contas (opcional)", type=["xlsx", "xls", "csv"], key="up_plano")
        financeiros = st.file_uploader("Relatório financeiro do cliente (opcional) — contas pagas/recebidas",
                                       type=["xlsx", "xls", "csv"], accept_multiple_files=True, key="up_fin")
    with c2:
        st.subheader("Extratos bancários")
        extratos = st.file_uploader("Extratos (OFX, Excel, CSV ou PDF)", type=["ofx", "qfx", "xlsx", "xls", "csv", "pdf"],
                                    accept_multiple_files=True, key="up_ext")

    razao_prev = None
    if razoes:
        try:
            razao_prev = Razao.carregar(razoes)
            if plano:
                razao_prev.aplicar_plano(ler_plano(plano))
            SS.razao = razao_prev
            n_tipos = {t: len(razao_prev.contas_do_tipo(t)) for t in ("banco", "cliente", "fornecedor")}
            st.success(f"Razão: {len(razao_prev.movimentos)} movimentos em {len(razao_prev.contas)} contas — "
                       f"{n_tipos['banco']} bancos, {n_tipos['cliente']} clientes, {n_tipos['fornecedor']} fornecedores.")
            if not razao_prev.movimentos:
                st.warning("Não encontrei movimentos no razão. Confira se o arquivo tem colunas Data, Histórico, Débito e Crédito.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Não consegui ler o razão: {e}")

    extratos_cfg = []
    if extratos:
        st.subheader("Conta contábil de cada extrato")
        bancos = [f"{c.codigo} - {c.nome}" for c in (razao_prev.contas_do_tipo("banco") if razao_prev else [])]
        todas = opcoes_contas() if razao_prev else []
        for arq in extratos:
            try:
                movs_arq, info = ler_extrato(arq)
            except Exception as e:  # noqa: BLE001
                st.error(f"{arq.name}: erro na leitura ({e})")
                continue
            ident = info.get("conta") or Path(arq.name).stem
            atual = perfil.contas_banco.get(ident, "")
            ent = sum(m.valor for m in movs_arq if m.valor > 0)
            sai = -sum(m.valor for m in movs_arq if m.valor < 0)
            per = f"{fmt_data(min(m.data for m in movs_arq))} a {fmt_data(max(m.data for m in movs_arq))}" if movs_arq else "—"
            cc1, cc2 = st.columns([3, 2])
            with cc1:
                st.markdown(f"**{arq.name}** — {len(movs_arq)} movimentos · {per}  \n"
                            f"Entradas R$ {fmt_valor(ent)} · Saídas R$ {fmt_valor(sai)}"
                            + (f" · conta no arquivo: `{info['conta']}`" if info.get("conta") else ""))
            with cc2:
                ops = bancos + [o for o in todas if o not in bancos] if todas else []
                if ops:
                    padrao = rotulo(atual) if atual else (bancos[0] if len(bancos) == 1 else "")
                    idx = ops.index(padrao) if padrao in ops else 0
                    if "" not in ops:
                        ops = [""] + ops
                        idx = ops.index(padrao) if padrao in ops else 0
                    esc = st.selectbox("Conta do banco", ops, index=idx, key=f"bco_{arq.name}")
                    cod = cod_de(esc)
                else:
                    cod = st.text_input("Código da conta do banco", atual, key=f"bco_{arq.name}")
            if cod:
                perfil.contas_banco[ident] = cod
                extratos_cfg.append((movs_arq, cod))
            if not movs_arq:
                st.warning(f"{arq.name}: nenhum movimento encontrado. Se for PDF escaneado, prefira OFX ou Excel.")

    pronto = bool(extratos_cfg) and len(extratos_cfg) == len(extratos or [])
    if extratos and not pronto:
        st.info("Escolha a conta contábil de todos os extratos para continuar.")
    if st.button("▶️ Gerar lançamentos", type="primary", disabled=not pronto, width="stretch"):
        with st.spinner("Lendo, cruzando notas e classificando..."):
            processar(razoes, plano, extratos_cfg, financeiros)
            perfil.salvar()
        st.success(f"{len(SS.lancs)} lançamentos gerados e trabalho salvo no computador. Vá para a aba **2 · Lançamentos**.")


# ----------------------------------------------------------------------------------- 2. lançamentos
ORDEM = {"pendente": 0, "revisar": 1, "ok": 2, "ja_lancado": 3, "ignorar": 4}
ICONE = {"pendente": "🔴", "revisar": "🟡", "ok": "🟢", "ja_lancado": "📗", "ignorar": "⚪"}
TITULO_STATUS = {"pendente": "Precisa de decisão", "revisar": "Conferir", "ok": "Pronto",
                 "ja_lancado": "Já está no razão", "ignorar": "Ignorado"}


def agrupar(lancs: list[Lancamento]) -> list[list[Lancamento]]:
    """Um grupo por movimento do extrato (linha principal + juros/retenções/desconto)."""
    grupos: dict[str, list[Lancamento]] = {}
    for l in lancs:
        grupos.setdefault(l.mov_id, []).append(l)
    for g in grupos.values():
        g.sort(key=lambda l: not l.principal)
    return sorted(grupos.values(), key=lambda g: (g[0].data, g[0].conta_banco, g[0].mov_id))


def status_grupo(g: list[Lancamento]) -> str:
    return min((l.status for l in g), key=lambda s: ORDEM.get(s, 9))


def nome_curto(cod: str, n: int = 34) -> str:
    if not cod:
        return "—"
    nome = SS.razao.contas[cod].nome if SS.razao and cod in SS.razao.contas else cod
    return nome if len(nome) <= n else nome[: n - 1] + "…"


def valor_sinal(l: Lancamento) -> str:
    return ("+ " if l.natureza == "entrada" else "− ") + fmt_valor(l.valor)


def decidir(mov_id: str, decisao: dict | None, memorizar: bool = False):
    if decisao is None:
        SS.forcados.pop(mov_id, None)
    else:
        SS.forcados[mov_id] = decisao
    reclassificar()
    if memorizar:
        g = [l for l in SS.lancs if l.mov_id == mov_id]
        if g and g[0].conta_contra:
            mov = next((m for m in SS.movs if m.id == mov_id), None)
            perfil.memorizar(g[0].descricao_extrato, g[0].conta_contra, g[0].historico,
                             cnpj=mov.cnpj if mov else None, parte=g[0].parte)
            perfil.salvar()
    SS.sel_mov = None   # a fila segue para o próximo


def painel(g: list[Lancamento]):
    p = g[0]
    st_g = status_grupo(g)
    mov_id = p.mov_id
    forcado = SS.forcados.get(mov_id)
    with st.container(border=True):
        cor = "red" if p.natureza == "saida" else "green"
        st.markdown(f"##### {ICONE[st_g]} :{cor}[{valor_sinal(p) if len(g) == 1 else ('+ ' if p.natureza == 'entrada' else '− ') + fmt_valor(sum(l.valor for l in g if l.conta_banco == p.conta_banco))}]"
                    f" &nbsp;·&nbsp; {fmt_data(p.data)}")
        st.caption(f"Extrato ({rotulo(p.conta_banco)}): **{p.descricao_extrato}**")

        # por que precisa de atenção
        expl = p.observacao.split(" | ")[0] if p.observacao else ""
        detalhes = [d for d in (p.observacao.split(" | ")[1:] if p.observacao else []) if d]
        if st_g == "pendente":
            st.error(f"**{p.motivo or 'Precisa de decisão'}**" + (f"  \n{expl}" if expl and expl != p.motivo else ""))
        elif st_g == "revisar":
            st.warning(f"**{p.motivo or 'Conferir'}**" + (f"  \n{expl}" if expl and expl != p.motivo else ""))
        elif st_g == "ja_lancado":
            st.info(f"Já consta no razão ({expl}). Não será exportado.")
        elif st_g == "ignorar":
            st.info("Ignorado: não será exportado.")
        else:
            st.success("Pronto para exportar" + (" — decidido pelo operador" if forcado else ""))

        if detalhes and st_g in ("pendente", "revisar"):
            st.caption("  \n".join("• " + d for d in detalhes))

        # lançamento sugerido
        if st_g != "ignorar":
            st.markdown("**Lançamento**")
            for l in g:
                dc_banco = "D" if l.natureza == "entrada" else "C"
                if l.principal or l.conta_banco == p.conta_banco:
                    linha = f"{dc_banco} {nome_curto(l.conta_banco, 26)} · {'C' if dc_banco == 'D' else 'D'} **{nome_curto(l.conta_contra, 40)}**"
                else:
                    linha = f"{'D' if l.natureza == 'entrada' else 'C'} {nome_curto(l.conta_banco, 26)} · {'C' if l.natureza == 'entrada' else 'D'} **{nome_curto(l.conta_contra, 40)}** _(sem banco)_"
                st.markdown(f"{linha} &nbsp; `{fmt_valor(l.valor)}`  \n<span style='color:#6b7280;font-size:0.85em'>{l.historico or '—'}</span>",
                            unsafe_allow_html=True)

        if st_g == "ja_lancado":
            if st.button("Não está no razão — lançar mesmo assim", key=f"nl_{mov_id}"):
                decidir(mov_id, {"conta": p.conta_contra, "aceito": True})
                st.rerun()
            return

        # decisão
        st.markdown("**Decisão**")
        alts = p.alternativas or []
        opcoes = ["Manter a sugestão"] if p.conta_contra else []
        opcoes += [f"{a['texto']}" for a in alts]
        opcoes += ["Escolher outra conta"]
        escolha = st.radio("Decisão", opcoes, key=f"dec_{mov_id}_{SS.versao}", label_visibility="collapsed")
        conta_outra = ""
        if escolha == "Escolher outra conta":
            ops = opcoes_contas()
            conta_outra = cod_de(st.selectbox("Conta de contrapartida", ops, index=None, placeholder="Digite para buscar a conta…",
                                              key=f"ct_{mov_id}_{SS.versao}"))
        with st.expander("Ajustar histórico ou nota"):
            hist = st.text_input("Histórico", p.historico, key=f"h_{mov_id}_{SS.versao}")
            nf = st.text_input("Nº da nota", p.nf, key=f"nf_{mov_id}_{SS.versao}")
        memo = st.checkbox("Usar sempre para este texto do extrato", key=f"m_{mov_id}_{SS.versao}",
                           help="Nos próximos extratos deste cliente, o mesmo texto vai direto para esta conta.")

        b1, b2, b3 = st.columns([2, 1, 1])
        if b1.button("✔ Confirmar", type="primary", key=f"ok_{mov_id}", width="stretch",
                     disabled=escolha == "Escolher outra conta" and not conta_outra):
            dec = dict(forcado or {})
            dec["aceito"] = True
            if escolha == "Escolher outra conta":
                dec.update(conta=conta_outra, nfs=[])
                dec.pop("historico", None)
            elif escolha != "Manter a sugestão":
                a = alts[opcoes.index(escolha) - (1 if p.conta_contra else 0)]
                dec.update(conta=a["conta"], nfs=a.get("nfs") or [])
                dec.pop("historico", None)
            if hist.strip() and hist.strip().upper() != p.historico and escolha == "Manter a sugestão":
                dec["historico"] = hist.strip().upper()
            if nf != p.nf and escolha == "Manter a sugestão":
                dec["nf_texto"] = nf.strip()
            decidir(mov_id, dec, memo)
            st.rerun()
        if b2.button("Ignorar", key=f"ig_{mov_id}", width="stretch", help="Não exportar este movimento"):
            decidir(mov_id, {"ignorar": True, "conta": p.conta_contra})
            st.rerun()
        if forcado and b3.button("↺ Desfazer", key=f"un_{mov_id}", width="stretch", help="Voltar à sugestão do programa"):
            decidir(mov_id, None)
            st.rerun()


with aba_lanc:
    lancs: list[Lancamento] = SS.lancs
    if not lancs:
        st.info("Gere os lançamentos na aba **1 · Arquivos**.")
    else:
        grupos = agrupar(lancs)
        cont = {k: 0 for k in ORDEM}
        for g in grupos:
            cont[status_grupo(g)] += 1
        atencao = cont["pendente"] + cont["revisar"]

        # resumo em uma linha
        if atencao:
            st.markdown(f"#### {atencao} movimento(s) pedem sua atenção")
        else:
            st.markdown("#### ✅ Tudo conferido — pronto para exportar")
        st.caption(f"🔴 {cont['pendente']} precisam de decisão · 🟡 {cont['revisar']} para conferir · "
                   f"🟢 {cont['ok']} prontos · 📗 {cont['ja_lancado']} já no razão · ⚪ {cont['ignorar']} ignorados")
        total_dec = len(grupos) - cont["ja_lancado"]
        st.progress((total_dec - atencao) / total_dec if total_dec else 1.0)

        f1, f2 = st.columns([3, 2])
        filtros = {f"⚠️ Atenção ({atencao})": ("pendente", "revisar"), f"🟢 Prontos ({cont['ok']})": ("ok",),
                   f"📗 No razão ({cont['ja_lancado']})": ("ja_lancado",), f"⚪ Ignorados ({cont['ignorar']})": ("ignorar",),
                   f"Todos ({len(grupos)})": tuple(ORDEM)}
        chaves = list(filtros)
        padrao = chaves[0] if atencao else chaves[-1]
        filtro = f1.segmented_control("Mostrar", chaves, default=padrao, key=f"filtro_{atencao == 0}",
                                      label_visibility="collapsed") or padrao
        busca = f2.text_input("Buscar", placeholder="🔎 Buscar por texto, conta ou valor", label_visibility="collapsed")

        vis = [g for g in grupos if status_grupo(g) in filtros[filtro]]
        if busca:
            b = busca.upper()
            vis = [g for g in vis if b in (g[0].descricao_extrato + " " + g[0].historico + " " + nome_curto(g[0].conta_contra, 99)).upper()
                   or b.replace(".", "").replace(",", ".") in f"{g[0].valor:.2f}"]
        if filtros[filtro] == ("pendente", "revisar"):
            vis.sort(key=lambda g: (ORDEM[status_grupo(g)], g[0].data))

        if not vis:
            st.success("Nada aqui. 🎉" if filtro == chaves[0] else "Nenhum movimento neste filtro.")
        else:
            col_t, col_p = st.columns([11, 8], gap="medium")
            with col_t:
                df = pd.DataFrame([{
                    " ": ICONE[status_grupo(g)],
                    "Data": g[0].data.strftime("%d/%m"),
                    "Valor": valor_sinal(g[0]) if len(g) == 1 else ("+ " if g[0].natureza == "entrada" else "− ")
                    + fmt_valor(sum(l.valor for l in g if l.conta_banco == g[0].conta_banco)),
                    "Atenção": g[0].motivo if status_grupo(g) in ("pendente", "revisar") else "",
                    "Extrato": descricao_curta_ui(g[0].descricao_extrato, 22),
                    "Lançar em": nome_curto(g[0].conta_contra, 17) + (f"  +{len(g) - 1}" if len(g) > 1 else ""),
                } for g in vis])
                if not any(df["Atenção"]):
                    df = df.drop(columns=["Atenção"])
                estilo = df.style.map(lambda v: "color:#b42318" if str(v).startswith("−") else "color:#067647", subset=["Valor"])
                if "Atenção" in df:
                    estilo = estilo.map(lambda v: "color:#b54708;font-weight:600" if v else "", subset=["Atenção"])
                ev = st.dataframe(estilo, hide_index=True, width="stretch", height=min(640, 38 + 35 * len(df)),
                                  on_select="rerun", selection_mode="single-row",
                                  key=f"tab_{SS.versao}_{filtro}_{busca}",
                                  column_config={" ": st.column_config.TextColumn(width=28),
                                                 "Data": st.column_config.TextColumn(width=52),
                                                 "Valor": st.column_config.TextColumn(width=88),
                                                 "Atenção": st.column_config.TextColumn(width=150),
                                                 "Extrato": st.column_config.TextColumn(width=125),
                                                 "Lançar em": st.column_config.TextColumn(width=115)})
                linhas_sel = ev.selection.rows if ev and ev.selection else []
                if linhas_sel:
                    SS.sel_mov = vis[linhas_sel[0]][0].mov_id
                st.caption("Clique numa linha para ver os detalhes e decidir.")
            with col_p:
                atual = next((g for g in vis if g[0].mov_id == SS.get("sel_mov")), vis[0])
                painel(atual)


# ---------------------------------------------------------------------------------- 3. notas em aberto
with aba_notas:
    if not SS.titulos_inicio:
        st.info("As notas em aberto aparecem depois de processar o razão.")
    else:
        st.caption("Notas de clientes e fornecedores apuradas no razão (lançamento da nota menos baixas). "
                   "‘Saldo após extrato’ mostra o que continua em aberto depois dos lançamentos sugeridos.")
        depois = {(t.conta, t.nf, t.data, t.valor): t.saldo for t in SS.titulos}
        linhas = [{
            "Tipo": t.tipo.capitalize(), "Conta": t.conta, "Nome": t.nome_conta, "NF": t.nf or "s/n",
            "Data": fmt_data(t.data), "Valor da nota": t.valor, "Saldo no razão": t.saldo,
            "Saldo após extrato": depois.get((t.conta, t.nf, t.data, t.valor), t.saldo),
        } for t in SS.titulos_inicio if t.aberto]
        df = pd.DataFrame(linhas)
        if df.empty:
            st.success("Nenhuma nota em aberto no razão.")
        else:
            tipo = st.radio("Mostrar", ["Todos", "Fornecedor", "Cliente"], horizontal=True)
            if tipo != "Todos":
                df = df[df["Tipo"] == tipo]
            st.dataframe(df, hide_index=True, width="stretch", column_config={
                "Valor da nota": st.column_config.NumberColumn(format="%.2f"),
                "Saldo no razão": st.column_config.NumberColumn(format="%.2f"),
                "Saldo após extrato": st.column_config.NumberColumn(format="%.2f"),
            })
            c = st.columns(3)
            c[0].metric("Notas em aberto", len(df))
            c[1].metric("Saldo no razão", f"R$ {fmt_valor(df['Saldo no razão'].sum())}")
            c[2].metric("Baixado pelos extratos", f"R$ {fmt_valor(df['Saldo no razão'].sum() - df['Saldo após extrato'].sum())}")


# ---------------------------------------------------------------------------------------- 4. exportar
with aba_exp:
    lancs = SS.lancs
    if not lancs:
        st.info("Nada para exportar ainda.")
    else:
        nomes = {c.codigo: c.nome for c in (SS.razao.contas.values() if SS.razao else [])}
        st.subheader("Conferência por banco")
        linhas = []
        for banco, (te, ts) in SS.totais.items():
            ls = [l for l in lancs if l.conta_banco == banco and l.status != "ignorar"]
            ent = sum(l.valor for l in ls if l.natureza == "entrada")
            sai = sum(l.valor for l in ls if l.natureza == "saida")
            ign = sum((l.valor if l.natureza == "entrada" else -l.valor) for l in lancs if l.conta_banco == banco and l.status == "ignorar")
            linhas.append({"Banco": f"{banco} - {nomes.get(banco, '')}", "Entradas extrato": te, "Saídas extrato": ts,
                           "Entradas lançadas": ent, "Saídas lançadas": sai, "Ignorados (líquido)": ign,
                           "Diferença": round((te - ts) - (ent - sai) - ign, 2)})
        st.dataframe(pd.DataFrame(linhas), hide_index=True, width="stretch",
                     column_config={k: st.column_config.NumberColumn(format="%.2f") for k in
                                    ["Entradas extrato", "Saídas extrato", "Entradas lançadas", "Saídas lançadas", "Ignorados (líquido)", "Diferença"]})
        if any(abs(l["Diferença"]) > 0.009 for l in linhas):
            st.warning("Há diferença entre o extrato e os lançamentos (valor editado manualmente?). Confira antes de importar.")

        erros = validar(lancs)
        exp = [l for l in lancs if l.status not in ("ja_lancado", "ignorar")]
        st.subheader("Arquivo de importação SIGER")
        st.write(f"**{len(exp)}** lançamentos · lote **{perfil.lote}** · {perfil.descricao_lote or '(sem descrição do lote)'}")
        so_ok = False
        if erros:
            st.error(f"{len(erros)} problema(s) — resolva na aba Lançamentos ou exporte só as linhas completas:")
            st.code("\n".join(erros[:30]) + ("\n..." if len(erros) > 30 else ""), language=None)
            so_ok = st.checkbox("Exportar apenas as linhas completas (as pendentes ficam de fora)")
        revisar = sum(1 for l in exp if l.status == "revisar")
        if revisar:
            st.warning(f"{revisar} lançamento(s) ainda marcados como **Revisar** serão exportados como estão.")
        if not perfil.descricao_lote:
            st.info("Preencha a descrição do lote na barra lateral (ex.: BANCO BB NOV/2026).")
        para_exportar = [l for l in exp if l.conta_contra and l.conta_banco] if so_ok else exp
        pode = not erros or so_ok
        periodo = max(l.data for l in lancs).strftime("%m_%Y")
        nome_cli = arm.nome_seguro(perfil.nome)
        c1, c2 = st.columns(2)
        nome_siger = f"Importacao_SIGER_{nome_cli}_{periodo}.xlsx"
        nome_conf = f"Conferencia_{nome_cli}_{periodo}.xlsx"
        dados_siger = gerar_siger(para_exportar, perfil.lote, perfil.descricao_lote) if pode else b""
        dados_conf = gerar_conferencia(lancs, SS.titulos, nomes, SS.totais)
        c1.download_button("⬇️ Baixar importação SIGER (.xlsx)", data=dados_siger, file_name=nome_siger,
                           disabled=not pode, type="primary", width="stretch",
                           on_click=arm.guardar_exportado, args=(perfil.nome, nome_siger, dados_siger))
        c2.download_button("⬇️ Baixar planilha de conferência", data=dados_conf, file_name=nome_conf,
                           width="stretch", on_click=arm.guardar_exportado, args=(perfil.nome, nome_conf, dados_conf))
        st.caption(f"Uma cópia de cada arquivo baixado fica guardada em `{arm.PASTA_EXPORTADOS / arm.nome_seguro(perfil.nome)}`.")


# ------------------------------------------------------------------------------------ configurações
with aba_cfg:
    st.subheader("Contas para juros e descontos")
    st.caption("Quando o valor pago/recebido for diferente do saldo da nota (até a tolerância), o programa separa a diferença nestas contas.")
    ops = opcoes_contas()

    def sel_conta(rot, atual, key):
        if ops:
            r = rotulo(atual) if atual else ""
            lista = ops if r in ops else ops + [r]
            return cod_de(st.selectbox(rot, lista, index=lista.index(r), key=key))
        return st.text_input(rot, atual, key=key)

    a, b = st.columns(2)
    with a:
        perfil.conta_juros_pagos = sel_conta("Juros/multas pagos", perfil.conta_juros_pagos, "cfg_jp")
        perfil.conta_desconto_obtido = sel_conta("Descontos obtidos", perfil.conta_desconto_obtido, "cfg_do")
    with b:
        perfil.conta_juros_recebidos = sel_conta("Juros recebidos", perfil.conta_juros_recebidos, "cfg_jr")
        perfil.conta_desconto_concedido = sel_conta("Descontos concedidos", perfil.conta_desconto_concedido, "cfg_dc")
    st.markdown("**Impostos retidos** — quando o cliente paga a nota descontando IRRF, PIS/COFINS/CSLL, ISS ou INSS "
                "(ou quando a empresa retém ao pagar o fornecedor), o programa reconhece o valor líquido e baixa a nota cheia. "
                "Informe as contas para ele gerar os lançamentos das retenções.")
    ret_cols = st.columns(3)
    for i, imp in enumerate(["IRRF", "PIS", "COFINS", "CSLL", "ISS", "INSS"]):
        cfg = dict(perfil.contas_retencao.get(imp) or {})
        with ret_cols[i % 3]:
            cfg["cliente"] = sel_conta(f"{imp} retido pelo cliente (a recuperar)", cfg.get("cliente", ""), f"ret_c_{imp}")
            cfg["fornecedor"] = sel_conta(f"{imp} retido do fornecedor (a recolher)", cfg.get("fornecedor", ""), f"ret_f_{imp}")
        perfil.contas_retencao[imp] = cfg
    perfil.tolerancia_juros_pct = st.slider("Tolerância de diferença tratada como juros/desconto (%)", 0.0, 30.0,
                                            float(perfil.tolerancia_juros_pct), 0.5)

    st.subheader("Regras de classificação")
    st.caption("Padrão = palavras do extrato (expressão regular, em maiúsculas sem acento; use | para ‘ou’). "
               "Conta vazia = o programa descobre no razão qual conta você costuma usar.")
    aprendidas = SS.get("contas_regra", {})
    df_r = pd.DataFrame([{**r, "conta": rotulo(r["conta"]) if r["conta"] else "",
                          "aprendida no razão": rotulo(aprendidas.get(r["nome"], ("", 0))[0]) if not r["conta"] else ""}
                         for r in perfil.regras])
    ed = st.data_editor(df_r, num_rows="dynamic", hide_index=True, width="stretch", key="cfg_regras",
                        column_config={
                            "nome": st.column_config.TextColumn("Nome", required=True),
                            "padrao": st.column_config.TextColumn("Padrão no extrato", width="large"),
                            "natureza": st.column_config.SelectboxColumn("Natureza", options=["saida", "entrada", "ambos"], required=True),
                            "conta": (st.column_config.SelectboxColumn("Conta fixa", options=ops, width="medium") if ops
                                      else st.column_config.TextColumn("Conta fixa")),
                            "historico": st.column_config.TextColumn("Histórico", width="large"),
                            "ativa": st.column_config.CheckboxColumn("Ativa"),
                            "aprendida no razão": st.column_config.TextColumn(disabled=True),
                        })

    st.subheader("Modelos de histórico")
    st.caption("Campos disponíveis: {nf} número(s) da nota · {parte} cliente/fornecedor · {descricao} texto do extrato · {mes} mês/ano")
    modelos = {}
    cols = st.columns(2)
    for i, (k, v) in enumerate(MODELOS_PADRAO.items()):
        modelos[k] = cols[i % 2].text_input(k.replace("_", " ").capitalize(), perfil.modelos.get(k, v), key=f"mod_{k}")

    if SS.razao:
        st.subheader("Tipo das contas")
        st.caption("Confira se clientes, fornecedores e bancos foram reconhecidos corretamente.")
        df_t = pd.DataFrame([{"Conta": c.codigo, "Classificação": c.classificacao, "Nome": c.nome,
                              "Tipo": perfil.tipos_conta.get(c.codigo, c.tipo)}
                             for c in sorted(SS.razao.contas.values(), key=lambda c: (c.classificacao or c.codigo))])
        ed_t = st.data_editor(df_t, hide_index=True, width="stretch", key="cfg_tipos",
                              column_config={"Tipo": st.column_config.SelectboxColumn(options=["banco", "cliente", "fornecedor", "outra"], required=True),
                                             "Conta": st.column_config.TextColumn(disabled=True),
                                             "Classificação": st.column_config.TextColumn(disabled=True),
                                             "Nome": st.column_config.TextColumn(disabled=True)})
    else:
        ed_t = None

    with st.expander(f"Memória do cliente ({len(perfil.memoria)} históricos, {len(perfil.apelidos)} nomes)"):
        if perfil.memoria:
            df_m = pd.DataFrame([{"Texto do extrato": k, "Conta": rotulo(v.get("conta", "")), "Histórico": v.get("historico", ""), "Apagar": False}
                                 for k, v in perfil.memoria.items()])
            ed_m = st.data_editor(df_m, hide_index=True, width="stretch", key="cfg_mem",
                                  column_config={"Texto do extrato": st.column_config.TextColumn(disabled=True),
                                                 "Conta": st.column_config.TextColumn(disabled=True)})
        else:
            ed_m = None
            st.write("Nada memorizado ainda.")

    if st.button("💾 Salvar configurações", type="primary"):
        regras = []
        for _, r in ed.iterrows():
            if not str(r.get("nome") or "").strip():
                continue
            regras.append(asdict(Regra(nome=str(r["nome"]), padrao=str(r.get("padrao") or ""),
                                       natureza=str(r.get("natureza") or "ambos"), conta=cod_de(r.get("conta")),
                                       historico=str(r.get("historico") or ""), ativa=bool(r.get("ativa", True)))))
        perfil.regras = regras
        perfil.modelos = modelos
        if ed_t is not None:
            for _, r in ed_t.iterrows():
                orig = SS.razao.contas.get(r["Conta"])
                if orig and r["Tipo"] != orig.tipo:
                    perfil.tipos_conta[r["Conta"]] = r["Tipo"]
                    orig.tipo = r["Tipo"]
        if ed_m is not None:
            for _, r in ed_m.iterrows():
                if r["Apagar"]:
                    perfil.memoria.pop(r["Texto do extrato"], None)
        perfil.salvar()
        st.success("Configurações salvas. Gere os lançamentos novamente para aplicar.")


# ------------------------------------------------------------------------------------------ backup
with aba_bkp:
    st.subheader("Onde ficam os dados")
    st.write(f"Tudo fica neste computador, na pasta `{arm.DADOS}`:")
    st.markdown("- **clientes** — contas, regras e tudo que o programa aprendeu de cada cliente\n"
                "- **trabalhos** — o trabalho de cada mês, salvo automaticamente a cada alteração\n"
                "- **exportados** — cópia de cada arquivo baixado para o SIGER\n"
                "- **backups** — cópias de segurança compactadas (.zip)")
    cfg = arm.config()
    st.caption(f"Último backup: {cfg['ultimo_backup'].replace('T', ' ') or 'nenhum'} · "
               "um backup automático é feito por dia, ao abrir o programa.")

    st.subheader("Cópia extra (recomendado)")
    st.write("Escolha uma pasta fora deste computador ou sincronizada na nuvem — por exemplo, a pasta do OneDrive/Google Drive "
             "ou um pendrive. Cada backup também é copiado para lá.")
    extra = st.text_input("Pasta extra de backup", cfg["pasta_backup_extra"], placeholder=r"C:\Users\voce\OneDrive\Backup Lancamentos")
    manter = st.number_input("Quantos backups manter", 5, 1000, int(cfg["manter_backups"]))
    if st.button("Salvar opções de backup"):
        cfg.update(pasta_backup_extra=extra.strip(), manter_backups=int(manter))
        arm.salvar_config(cfg)
        st.success("Opções salvas.")

    st.subheader("Backup agora")
    if st.button("🛟 Fazer backup agora", type="primary"):
        z = arm.fazer_backup("manual")
        st.success(f"Backup criado: {z.name}")
    backups = arm.listar_backups()
    if backups:
        ult = backups[0]
        st.download_button(f"⬇️ Baixar o backup mais recente ({ult.name})", data=ult.read_bytes(), file_name=ult.name)
        st.dataframe(pd.DataFrame([{"Arquivo": b.name, "Data": datetime.fromtimestamp(b.stat().st_mtime).strftime("%d/%m/%Y %H:%M"),
                                    "Tamanho (KB)": round(b.stat().st_size / 1024, 1)} for b in backups]),
                     hide_index=True, width="stretch")

    st.subheader("Restaurar")
    st.write("Envie um arquivo de backup (.zip). Antes de restaurar, o programa guarda um backup do estado atual.")
    arq_bkp = st.file_uploader("Arquivo de backup", type=["zip"], key="up_bkp")
    confirma = st.checkbox("Entendo que os dados atuais serão substituídos pelos do backup")
    if st.button("Restaurar backup", disabled=not (arq_bkp and confirma)):
        n = arm.restaurar_backup(arq_bkp.getvalue())
        SS.perfil = None
        st.success(f"{n} arquivos restaurados. Escolha o cliente novamente na barra lateral.")
