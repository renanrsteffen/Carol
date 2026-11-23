"""Leitura do razão contábil e do plano de contas, e apuração das notas em aberto.

O leitor é tolerante: aceita o razão "por conta" (linhas "Conta: 123 - NOME" seguidas dos
movimentos) e o razão "plano" (uma coluna Conta em cada linha). As colunas são localizadas
pelo texto do cabeçalho (Data, Histórico, Débito, Crédito, Lote, Lançamento, Contrapartida...).
"""
from __future__ import annotations

import io
import re
from collections import defaultdict

import pandas as pd

from .modelos import Conta, MovRazao, Titulo
from .util import extrair_nfs, normalizar, parse_data, parse_valor

RE_BANCO = re.compile(
    r"\b(BANCO|BCO|ITAU|BRADESCO|SANTANDER|CAIXA ECON|CEF|SICREDI|SICOOB|UNICRED|CRESOL|BANRISUL|"
    r"INTER|NUBANK|C6|BTG|SAFRA|MERCADO PAGO|PAGSEGURO|PAGBANK|STONE|CORA|ASAAS|C/C|CONTA CORRENTE|"
    r"CONTA MOVIMENTO|BB)\b"
)
RE_GENERICA = re.compile(
    r"\b(ESTOQUE|MERCADORIA|COMPRAS?|CUSTO|DESPESA|RECEITA|VENDAS?|IMPOSTO|TRIBUTO|ICMS|IPI|PIS|COFINS|ISS|"
    r"IRPJ|CSLL|INSS|FGTS|SALARIO|PROVISAO|CAPITAL|LUCRO|PREJUIZO|RESULTADO|DEVOLUC|IMOBILIZADO|DEPRECIAC|"
    r"ADIANTAMENTO|CAIXA|APLICACAO|EMPRESTIMO|FINANCIAMENTO|SIMPLES NACIONAL|SERVICOS PRESTADOS)\b"
)
RE_NAO_BANCO = re.compile(r"\b(APLIC|APLICACAO|APLICACOES|INVEST|POUPANCA|CDB|EMPREST|FINANCIAMENTO|TARIFA|JUROS|DESP)")


# --------------------------------------------------------------------------------------- leitura
def ler_tabela(arquivo, nome: str = "") -> list[pd.DataFrame]:
    """Lê xlsx/xls/csv em uma lista de DataFrames crus (sem cabeçalho), um por aba."""
    nome = (nome or getattr(arquivo, "name", "") or "").lower()
    if hasattr(arquivo, "read"):
        dados = arquivo.read()
        if hasattr(arquivo, "seek"):
            arquivo.seek(0)
    else:
        with open(arquivo, "rb") as f:
            dados = f.read()
    if nome.endswith((".csv", ".txt")):
        for enc in ("utf-8-sig", "latin-1"):
            try:
                texto = dados.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        sep = ";" if texto.count(";") >= texto.count(",") else ","
        return [pd.read_csv(io.StringIO(texto), sep=sep, header=None, dtype=str, keep_default_na=False)]
    abas = pd.read_excel(io.BytesIO(dados), sheet_name=None, header=None, dtype=object)
    return list(abas.values())


def _txt(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


CABECALHOS = {
    "data": [r"^DATA$", r"^DT\.?$", r"^DATA LANC", r"^DATA DO LANC", r"^DATA MOV"],
    "historico": [r"HISTORICO", r"^COMPLEMENTO", r"^DESCRICAO"],
    "debito": [r"^DEBITO", r"^VALOR DEBITO", r"^DEB\.?$"],
    "credito": [r"^CREDITO", r"^VALOR CREDITO", r"^CRED\.?$"],
    "saldo": [r"^SALDO"],
    "lote": [r"^LOTE"],
    "lancamento": [r"^LANC", r"^NUM.*LANC", r"^N.? LANC"],
    "contrapartida": [r"CONTRAPARTIDA", r"^C\.?\s?PARTIDA", r"^CONTRA"],
    "conta": [r"^CONTA$", r"^CONTA CONTABIL", r"^COD.*CONTA", r"^CONTA ANALITICA"],
    "nome_conta": [r"^NOME DA CONTA", r"^DESCRICAO DA CONTA"],
    "documento": [r"^DOC", r"^NUMERO DO DOC", r"^N\.?\s?DOC", r"^NF$", r"^NOTA"],
}


def _mapear_cabecalho(linha: list[str]) -> dict[str, int]:
    mapa: dict[str, int] = {}
    for i, cel in enumerate(linha):
        t = normalizar(cel)
        if not t:
            continue
        for campo, pads in CABECALHOS.items():
            if campo in mapa:
                continue
            if any(re.search(p, t) for p in pads):
                # "Descrição da conta" não é histórico
                if campo == "historico" and "CONTA" in t and "nome_conta" not in mapa:
                    mapa["nome_conta"] = i
                    break
                mapa[campo] = i
                break
    return mapa


RE_LINHA_CONTA = re.compile(r"^CONTA\s*(?:CONTABIL)?\s*:?\s*(.*)$")


def _parse_rotulo_conta(texto: str) -> tuple[str, str, str]:
    """'15 - 1.1.1.02.001 - BANCO ITAU' -> ('15', '1.1.1.02.001', 'BANCO ITAU')."""
    t = texto.strip(" -:")
    partes = [p for p in re.split(r"\s+-\s+|\s{2,}|\t", t) if p.strip()]
    codigo = classif = ""
    resto: list[str] = []
    for p in partes:
        p = p.strip()
        sub = p.split()
        while sub and re.fullmatch(r"[\d.\-/]+", sub[0]):
            tok = sub.pop(0)
            if "." in tok and not classif:
                classif = tok
            elif not codigo and re.fullmatch(r"\d+", tok):
                codigo = tok
            elif not classif:
                classif = tok
        if sub:
            resto.append(" ".join(sub))
    nome = " - ".join(resto).strip(" -")
    if not codigo:
        # SIGER: classificação completa 1.1.1.02.02.00205 -> código reduzido 205 (último grupo)
        m = re.fullmatch(r"\d+(?:\.\d+){3,}\.(\d{3,})", classif)
        codigo = str(int(m[1])) if m else classif
    return codigo, classif, nome


RE_CONTRA = re.compile(r"C(?:ONTRA)?\.?\s?P(?:ARTIDA)?\s*:?\s*([\d][\d.\-]*)\s*-?\s*([^|]*)", re.I)
RE_IGNORAR = re.compile(r"^(SALDO ANTERIOR|SALDO INICIAL|SALDO ATUAL|SALDO FINAL|TOTAL|TOTAIS|TRANSPORTE|A TRANSPORTAR)")


class Razao:
    def __init__(self) -> None:
        self.contas: dict[str, Conta] = {}
        self.movimentos: list[MovRazao] = []
        self.avisos: list[str] = []
        self.saldos_anteriores: dict[str, tuple[float, bool, object]] = {}  # conta -> (valor, sinal explícito, data)
        self.saldo_devedor_positivo = False

    # ------------------------------------------------------------------ carga
    @classmethod
    def carregar(cls, arquivos) -> "Razao":
        r = cls()
        for arq in arquivos:
            nome = getattr(arq, "name", str(arq))
            for df in ler_tabela(arq, nome):
                r._ler_grade(df.values.tolist())
        r._completar_contrapartidas()
        r._classificar_contas()
        return r

    def _conta(self, codigo: str, nome: str = "", classif: str = "", grupo: str = "") -> Conta:
        c = self.contas.get(codigo)
        if c is None:
            c = Conta(codigo=codigo, nome=nome or codigo, classificacao=classif, grupo=grupo)
            self.contas[codigo] = c
        else:
            if grupo:
                c.grupo = grupo
            if nome and (c.nome == c.codigo or not c.nome):
                c.nome = nome
            if classif and not c.classificacao:
                c.classificacao = classif
        return c

    def _ler_grade(self, linhas: list[list]) -> None:
        mapa: dict[str, int] = {}
        conta_atual = ""
        grupo_atual = ""
        ultimo: MovRazao | None = None
        ano_ref = None
        for bruta in linhas:
            linha = [_txt(c) for c in bruta]
            ne = [c for c in linha if c]
            if not ne:
                continue
            junto = " ".join(ne)
            jn = normalizar(junto)

            # grupo do plano (SIGER): "Grupo: 1.1.1.02.02 BANCOS CONTA MOVIMENTO"
            mg = re.match(r"^GRUPO\s*:?\s*[\d.]+\s+(.*)$", jn)
            if mg:
                grupo_atual = mg[1].strip()
                continue
            if "LDO EXERCICIO" in jn or "SALDO EXERCICIO" in jn:
                self.saldo_devedor_positivo = True   # SIGER: saldo = débitos - créditos

            # cabeçalho de conta: "Conta: 15 - 1.1.1.02.001 - BANCO ITAU"
            m = RE_LINHA_CONTA.match(jn)
            if m and ":" in junto[:20] and not parse_data(linha[mapa["data"]] if "data" in mapa and mapa["data"] < len(linha) else None):
                bruto = junto.split(":", 1)[1]
                cod, classif, nome = _parse_rotulo_conta(bruto)
                if cod:
                    conta_atual = cod
                    self._conta(cod, nome, classif, grupo_atual)
                    ultimo = None
                continue

            # linha de cabeçalho de colunas
            cand = _mapear_cabecalho(linha)
            if "data" in cand and ("debito" in cand or "credito" in cand) and len(cand) >= 3:
                mapa = cand
                ultimo = None
                continue
            if not mapa:
                continue

            def col(campo):
                i = mapa.get(campo)
                return linha[i] if i is not None and i < len(linha) else ""

            hist = col("historico")
            data = parse_data(bruta[mapa["data"]] if mapa["data"] < len(bruta) else None, ano_ref)
            jh = normalizar(hist) or jn
            if re.match(r"^(SALDO ANTERIOR|SALDO INICIAL)", jh) or re.search(r"\bSALDO ANTERIOR\b", jn[:40]):
                bruto_saldo = col("saldo") or col("debito") or col("credito") or next(
                    (c for c in reversed(linha) if parse_valor(c) is not None and re.search(r"\d,\d{2}|\d\.\d{1,2}$|^-?\d+$", c)), "")
                v = parse_valor(bruto_saldo)
                if conta_atual and v:
                    explicito = bool(re.search(r"[DC]\s*$|^-|-$|\(", str(bruto_saldo).strip().upper()))
                    if col("credito") and not col("saldo") and not col("debito"):
                        v, explicito = abs(v), True
                    elif col("debito") and not col("saldo"):
                        v, explicito = -abs(v), True
                    if self.saldo_devedor_positivo and not re.search(r"[DC]\s*$", str(bruto_saldo).strip().upper()):
                        v, explicito = -v, True   # converte para a convenção interna (credor positivo)
                    self.saldos_anteriores[conta_atual] = (v, explicito, data)
                continue
            deb = parse_valor(col("debito")) or 0.0
            cred = parse_valor(col("credito")) or 0.0

            if data is None:
                # continuação do histórico em várias linhas
                if ultimo is not None and hist and not deb and not cred and not RE_IGNORAR.match(normalizar(hist)):
                    ultimo.historico = f"{ultimo.historico} {hist}".strip()
                    self._extrair_contra(ultimo)
                continue
            ano_ref = data.year
            if RE_IGNORAR.match(normalizar(hist)):
                continue
            if not deb and not cred:
                continue
            conta = conta_atual
            if "conta" in mapa and col("conta"):
                cod, classif, nome = _parse_rotulo_conta(col("conta"))
                nome = nome or col("nome_conta")
                if cod:
                    conta = cod
                    self._conta(cod, nome, classif)
            if not conta:
                continue
            mov = MovRazao(
                conta=conta, data=data, historico=hist, debito=abs(deb), credito=abs(cred),
                lote=_num(col("lote")), lancamento=_num(col("lancamento")),
            )
            if "contrapartida" in mapa and col("contrapartida"):
                cod, classif, nome = _parse_rotulo_conta(col("contrapartida"))
                if cod:
                    mov.contrapartida = cod
                    self._conta(cod, nome, classif)
            self._extrair_contra(mov)
            if col("documento"):
                mov.nfs = [n for n in [re.sub(r"\D", "", col("documento")).lstrip("0")] if n]
            self.movimentos.append(mov)
            ultimo = mov

    def _extrair_contra(self, mov: MovRazao) -> None:
        m = RE_CONTRA.search(mov.historico)
        if m:
            cod, classif, nome = _parse_rotulo_conta(f"{m[1]} {m[2]}")
            if cod and not mov.contrapartida:
                mov.contrapartida = cod
                self._conta(cod, nome, classif)
            mov.historico = (mov.historico[: m.start()]).strip(" -|")
        nfs = extrair_nfs(mov.historico)
        for n in nfs:
            if n not in mov.nfs:
                mov.nfs.append(n)

    def _completar_contrapartidas(self) -> None:
        """Sem contrapartida no histórico, deduz pelo par lote+lançamento."""
        grupos: dict[tuple, list[MovRazao]] = defaultdict(list)
        for m in self.movimentos:
            if m.lote or m.lancamento:
                grupos[(m.lote, m.lancamento, m.data)].append(m)
        for ms in grupos.values():
            deb = [m for m in ms if m.debito]
            cred = [m for m in ms if m.credito]
            if len(deb) == 1:
                for m in cred:
                    m.contrapartida = m.contrapartida or deb[0].conta
            if len(cred) == 1:
                for m in deb:
                    m.contrapartida = m.contrapartida or cred[0].conta

    def _classificar_contas(self) -> None:
        por_conta: dict[str, list[MovRazao]] = defaultdict(list)
        for m in self.movimentos:
            por_conta[m.conta].append(m)
        for cod, c in self.contas.items():
            nome = normalizar(c.nome)
            ms = por_conta.get(cod, [])
            if c.grupo:  # o nome do grupo do plano decide (razão do SIGER)
                g = normalizar(c.grupo)
                if re.search(r"^BANCOS?\b|CONTA MOVIMENTO|CONTAS? CORRENTES?", g):
                    c.tipo = "banco"
                elif re.match(r"^(CLIENTES|DUPLICATAS A RECEBER|CONTAS A RECEBER)", g):
                    c.tipo = "cliente"
                elif re.match(r"^(FORNECEDORES|CONTAS A PAGAR)", g):
                    c.tipo = "fornecedor"
                else:
                    c.tipo = "outra"
                continue
            if RE_BANCO.search(nome) and not RE_NAO_BANCO.search(nome):
                c.tipo = "banco"
                continue
            cl = c.classificacao
            com_nf_d = sum(m.debito for m in ms if m.nfs)
            com_nf_c = sum(m.credito for m in ms if m.nfs)
            if cl.startswith("1.1.2") or re.search(r"\bCLIENTE", nome):
                c.tipo = "cliente"
            elif cl.startswith("2.1.1") or re.search(r"\bFORNECEDOR", nome):
                c.tipo = "fornecedor"
            elif RE_GENERICA.search(nome) or cl[:1] in ("3", "4", "5", "6"):
                c.tipo = "outra"
            elif com_nf_d or com_nf_c:
                if cl[:1] == "1":
                    c.tipo = "cliente"
                elif cl[:1] == "2":
                    c.tipo = "fornecedor"
                elif not cl and len(ms) >= 2:
                    c.tipo = "cliente" if com_nf_d > com_nf_c else "fornecedor"

    def aplicar_plano(self, plano: dict[str, Conta]) -> None:
        for cod, c in plano.items():
            atual = self.contas.get(cod)
            if atual is None:
                self.contas[cod] = c
            else:
                atual.nome = c.nome or atual.nome
                atual.classificacao = c.classificacao or atual.classificacao
        self._classificar_contas()

    # ------------------------------------------------------------------ consultas
    def contas_do_tipo(self, tipo: str) -> list[Conta]:
        return [c for c in self.contas.values() if c.tipo == tipo]

    def movimentos_banco(self, codigos_banco: set[str]) -> list[MovRazao]:
        return [m for m in self.movimentos if m.conta in codigos_banco and m.contrapartida]


def _num(v: str) -> str:
    v = _txt(v)
    f = parse_valor(v)
    if f is not None and float(f).is_integer():
        return str(int(f))
    return v


# --------------------------------------------------------------------------------- plano de contas
def ler_plano(arquivo) -> dict[str, Conta]:
    """Lê o plano de contas: colunas código (reduzido), classificação e descrição/nome."""
    contas: dict[str, Conta] = {}
    for df in ler_tabela(arquivo, getattr(arquivo, "name", str(arquivo))):
        linhas = [[_txt(c) for c in l] for l in df.values.tolist()]
        mapa = {}
        for i, l in enumerate(linhas[:30]):
            norm = [normalizar(c) for c in l]
            m = {}
            for j, t in enumerate(norm):
                if re.search(r"^(COD|CODIGO|REDUZIDO|CONTA$|COD\. REDUZIDO|CODIGO REDUZIDO)", t) and "codigo" not in m:
                    m["codigo"] = j
                elif re.search(r"CLASSIF|ESTRUTURA|MASCARA", t):
                    m["classificacao"] = j
                elif re.search(r"^(DESCRICAO|NOME|TITULO|DENOMINACAO)", t):
                    m["nome"] = j
            if "nome" in m and ("codigo" in m or "classificacao" in m):
                mapa = m
                inicio = i + 1
                break
        if not mapa:
            continue
        for l in linhas[inicio:]:
            def g(k):
                j = mapa.get(k)
                return l[j] if j is not None and j < len(l) else ""
            cod = _num(g("codigo")) or g("classificacao")
            if not cod or not g("nome"):
                continue
            contas[cod] = Conta(codigo=cod, nome=g("nome"), classificacao=g("classificacao"))
    return contas


# ------------------------------------------------------------------------------ notas em aberto
def apurar_titulos(razao: Razao, tipos_override: dict[str, str] | None = None) -> list[Titulo]:
    """Monta os títulos (notas) de clientes e fornecedores e abate as baixas do razão.

    Fornecedor: crédito = nota lançada; débito = pagamento.  Cliente: o inverso.
    A baixa usa o nº da nota no histórico; sem número, procura título de mesmo valor e,
    por último, abate do mais antigo (PEPS).
    """
    tipos_override = tipos_override or {}
    titulos: list[Titulo] = []
    por_conta: dict[str, list[MovRazao]] = defaultdict(list)
    for m in razao.movimentos:
        por_conta[m.conta].append(m)

    for cod in razao.saldos_anteriores:
        por_conta.setdefault(cod, [])
    for cod, movs in por_conta.items():
        conta = razao.contas.get(cod)
        tipo = tipos_override.get(cod) or (conta.tipo if conta else "")
        if tipo not in ("cliente", "fornecedor"):
            continue
        movs = sorted(movs, key=lambda x: x.data)
        lista: list[Titulo] = []
        if cod in razao.saldos_anteriores:
            v, explicito, d = razao.saldos_anteriores[cod]
            # saldo devedor (D, negativo) é natural de cliente; credor (C, positivo) de fornecedor
            natural = (v > 0) if tipo == "fornecedor" else (v < 0)
            if abs(v) > 0.009 and (natural or not explicito):
                data_ini = d or (movs[0].data if movs else None)
                if data_ini:
                    lista.append(Titulo(cod, conta.nome if conta else cod, tipo, "", data_ini, abs(v), abs(v),
                                        "SALDO ANTERIOR (SEM DETALHE DAS NOTAS)"))
        baixas: list[MovRazao] = []
        for m in movs:
            constitui = m.credito if tipo == "fornecedor" else m.debito
            baixa = m.debito if tipo == "fornecedor" else m.credito
            if constitui:
                nfs = m.nfs or [""]
                # várias notas no mesmo lançamento: título único com as notas juntas
                nf = nfs[0] if len(nfs) == 1 else "/".join(nfs)
                lista.append(Titulo(cod, conta.nome if conta else cod, tipo, nf, m.data, constitui, constitui, m.historico))
            if baixa:
                baixas.append(m)
        for b in baixas:
            restante = b.debito if tipo == "fornecedor" else b.credito
            restante = _abater(lista, b, restante)
        titulos.extend(lista)
    return titulos


def _abater(lista: list[Titulo], b: MovRazao, restante: float) -> float:
    abertos = [t for t in lista if t.aberto and t.data <= b.data] or [t for t in lista if t.aberto]
    # 1) pelo número da nota
    if b.nfs:
        for nf in b.nfs:
            for t in abertos:
                if restante <= 0.009:
                    break
                if nf in t.nf.split("/") and t.aberto:
                    usado = min(t.saldo, restante)
                    t.saldo = round(t.saldo - usado, 2)
                    restante = round(restante - usado, 2)
        if restante <= 0.009:
            return 0.0
    # 2) título com o mesmo valor
    for t in abertos:
        if t.aberto and abs(t.saldo - restante) < 0.01:
            t.saldo = 0.0
            return 0.0
    # 3) PEPS
    for t in abertos:
        if restante <= 0.009:
            break
        if t.aberto:
            usado = min(t.saldo, restante)
            t.saldo = round(t.saldo - usado, 2)
            restante = round(restante - usado, 2)
    return restante
