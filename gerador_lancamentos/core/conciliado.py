"""Leitura do "Extrato Bancário" conciliado exportado do sistema financeiro do cliente (PDF).

Formato: cabeçalho com "Banco: 748 SICREDI ... Agência: 0000 ... Código Contábil: 6", linhas de data
(dd/mm/aaaa) e, abaixo, os lançamentos: "<conta> <custo> <histórico> <entrada | saída>". O histórico
pode continuar na linha seguinte. Cada lançamento traz a conta do plano do cliente e um histórico
detalhado (ex.: "PAGO NF Nº 12345 - FORNECEDOR EXEMPLO LTDA").
"""
from __future__ import annotations

import io
import re

from .modelos import ItemFinanceiro
from .util import normalizar, parse_data, parse_valor

RE_DATA_LINHA = re.compile(r"^\s*(\d{2}/\d{2}/\d{4})\s*$")
RE_LANC = re.compile(r"^\s*(\d{1,6})\s+(\d{1,4})\s+(\S.*?)\s+(-?\d{1,3}(?:\.\d{3})*,\d{2})\s*$")
RE_NF = re.compile(r"NF\s*N[º°O]?\.?\s*([A-Z]?\d[\w.\-/]*)", re.I)


def e_conciliado(texto: str) -> bool:
    n = normalizar(texto[:3000])
    return "EXTRATO BANCARIO" in n and "CODIGO CONTABIL" in n and "CONTA" in n and "CUSTO" in n


def ler_conciliado(arquivo) -> list[ItemFinanceiro]:
    nome = getattr(arquivo, "name", str(arquivo))
    if hasattr(arquivo, "read"):
        dados = arquivo.read()
        if hasattr(arquivo, "seek"):
            arquivo.seek(0)
    else:
        with open(arquivo, "rb") as f:
            dados = f.read()
    import pdfplumber
    paginas = []
    with pdfplumber.open(io.BytesIO(dados)) as pdf:
        for p in pdf.pages:
            paginas.append(_linhas_com_posicao(p))
    itens, avisos = interpretar_conciliado(paginas)
    return itens


def _linhas_com_posicao(pagina) -> list[tuple[str, float | None, dict]]:
    """Agrupa as palavras por linha. Retorna (texto, x final da última palavra, posições do cabeçalho)."""
    palavras = pagina.extract_words(keep_blank_chars=False, x_tolerance=1.5, y_tolerance=2)
    linhas: list[list[dict]] = []
    for w in sorted(palavras, key=lambda w: (round(w["top"]), w["x0"])):
        if linhas and abs(linhas[-1][0]["top"] - w["top"]) <= 2.5:
            linhas[-1].append(w)
        else:
            linhas.append([w])
    out = []
    for ws in linhas:
        ws.sort(key=lambda w: w["x0"])
        texto = " ".join(w["text"] for w in ws)
        cab = {}
        for w in ws:
            t = normalizar(w["text"])
            if t == "ENTRADAS":
                cab["ent"] = w["x1"]
            elif t == "SAIDAS":
                cab["sai"] = w["x1"]
        out.append((texto, ws[-1]["x1"], cab))
    return out


def interpretar_conciliado(paginas) -> tuple[list[ItemFinanceiro], list[str]]:
    """paginas: lista de linhas (texto, x_final, cabeçalho) ou de textos simples."""
    itens: list[ItemFinanceiro] = []
    avisos: list[str] = []
    banco = ""
    data = None
    pos_ent = pos_sai = None
    dia: list[ItemFinanceiro] = []
    for pagina in paginas:
        linhas = pagina if isinstance(pagina, list) else [(l, None, {}) for l in pagina.splitlines()]
        ultimo: ItemFinanceiro | None = None
        for linha, x_fim, cab in linhas:
            if not linha.strip():
                continue
            n = normalizar(linha)
            mb = re.search(r"BANCO\s*:?\s*(\d{1,3})\b", n)
            if mb and "AGENCIA" in n:
                banco = mb[1].zfill(3)
                continue
            if "ent" in cab and "sai" in cab:
                pos_ent, pos_sai = cab["ent"], cab["sai"]
                continue
            if "HISTORICO" in n and "ENTRADAS" in n:
                continue
            md = RE_DATA_LINHA.match(linha)
            if md:
                data = parse_data(md[1])
                ultimo = None
                dia = []
                continue
            if "TOTAL DO DIA" in n:
                vals = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", linha)
                if len(vals) >= 2 and dia:
                    te, ts = parse_valor(vals[-2]), parse_valor(vals[-1])
                    se = round(sum(i.valor for i in dia if i.tipo == "receber"), 2)
                    ss = round(sum(i.valor for i in dia if i.tipo == "pagar"), 2)
                    if abs(se - te) > 0.01 or abs(ss - ts) > 0.01:
                        avisos.append(f"{data}: totais do dia não conferem (lido E {se} S {ss}; impresso E {te} S {ts})")
                ultimo = None
                continue
            if n.startswith(("SALDO ANTERIOR", "TOTAL GERAL", "EXTRATO BANCARIO", "PERIODO", "NUMERO DA CONTA", "FOLHA")) \
                    or "EMISSAO EM" in n:
                ultimo = None
                continue
            ml = RE_LANC.match(linha)
            if ml and data:
                valor = parse_valor(ml[4]) or 0.0
                if x_fim is not None and pos_ent is not None and pos_sai is not None:
                    entrada = abs(x_fim - pos_ent) < abs(x_fim - pos_sai)
                else:
                    entrada = True
                hist = re.sub(r"\s{2,}", " ", ml[3]).strip()
                ultimo = ItemFinanceiro(data=data, valor=round(abs(valor), 2), descricao=hist,
                                        tipo="receber" if entrada else "pagar", conta_cliente=ml[1], banco=banco)
                itens.append(ultimo)
                dia.append(ultimo)
                continue
            if ultimo is not None and not re.search(r"\d,\d{2}\s*$", linha):
                ultimo.descricao = f"{ultimo.descricao} {linha.strip()}"
    for it in itens:
        _completar(it)
    return itens, avisos


def _completar(it: ItemFinanceiro) -> None:
    d = re.sub(r"\s{2,}", " ", it.descricao).strip()
    it.descricao = d
    m = RE_NF.search(d) or re.search(r"(?:PAGO|REF)\s+(\d{3,10})/\d{1,2}\b", d) or re.match(r"^(\d{3,10})/\d{1,2}\s+-", d)
    if m:
        nf = m[1].strip(".-/")
        it.nf = re.sub(r"-\d{1,2}$", "", nf) if "PEDIDO" not in d.upper() else nf
    # nome da parte: o que vem depois do último " - " (ex.: "... - FORNECEDOR EXEMPLO LTDA")
    if " - " in d:
        nome = d.rsplit(" - ", 1)[1].strip()
        if re.search(r"[A-Za-z]{3}", nome) and not re.fullmatch(r"[\d\W]+", nome):
            it.nome = nome
