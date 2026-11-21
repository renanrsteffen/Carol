"""Leitura de extratos bancários: OFX (preferencial), Excel/CSV e PDF (melhor esforço)."""
from __future__ import annotations

import hashlib
import io
import re
from datetime import date

from .modelos import Movimento
from .razao import _txt, ler_tabela
from .util import extrair_cnpj_cpf, normalizar, parse_data, parse_valor

RE_SALDO = re.compile(r"^(SALDO|S A L D O|SDO\b|SALDO ANTERIOR|SALDO DO DIA|SALDO TOTAL|SALDO EM CONTA|A CONTA NAO FOI MOVIMENTADA|SALDO BLOQ|SALDO DISPON)")


def _id(*partes) -> str:
    return hashlib.md5("|".join(str(p) for p in partes).encode()).hexdigest()[:12]


def _ler_bytes(arquivo) -> tuple[bytes, str]:
    nome = getattr(arquivo, "name", str(arquivo))
    if hasattr(arquivo, "read"):
        dados = arquivo.read()
        if hasattr(arquivo, "seek"):
            arquivo.seek(0)
    else:
        with open(arquivo, "rb") as f:
            dados = f.read()
    return dados, nome


def ler_extrato(arquivo) -> tuple[list[Movimento], dict]:
    """Retorna (movimentos, info). info traz banco/conta detectados e saldos, quando houver."""
    dados, nome = _ler_bytes(arquivo)
    n = nome.lower()
    if n.endswith((".ofx", ".qfx")) or b"<OFX>" in dados[:4000].upper() or b"OFXHEADER" in dados[:200]:
        movs, info = ler_ofx(dados)
    elif n.endswith(".pdf"):
        movs, info = ler_pdf(dados)
    else:
        movs, info = ler_planilha(io.BytesIO(dados), nome)
    for m in movs:
        m.arquivo = nome
        m.cnpj = m.cnpj or extrair_cnpj_cpf(m.descricao)
    info["arquivo"] = nome
    return movs, info


# ----------------------------------------------------------------------------------------- OFX
def ler_ofx(dados: bytes) -> tuple[list[Movimento], dict]:
    texto = None
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            texto = dados.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    texto = texto or dados.decode("latin-1", "ignore")

    def tag(bloco: str, nome: str) -> str:
        m = re.search(rf"<{nome}>([^<\r\n]*)", bloco, re.I)
        return m[1].strip() if m else ""

    info = {
        "banco": tag(texto, "BANKID") or tag(texto, "ORG"),
        "conta": tag(texto, "ACCTID"),
        "saldo_final": parse_valor(tag(texto, "BALAMT")) if tag(texto, "BALAMT") else None,
    }
    movs: list[Movimento] = []
    for i, bloco in enumerate(re.findall(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|</BANKTRANLIST>)", texto, re.S | re.I)):
        valor = parse_valor(tag(bloco, "TRNAMT"))
        dt = tag(bloco, "DTPOSTED")[:8]
        if valor is None or len(dt) < 8:
            continue
        d = date(int(dt[:4]), int(dt[4:6]), int(dt[6:8]))
        memo, nome = tag(bloco, "MEMO"), tag(bloco, "NAME")
        desc = memo if not nome or normalizar(nome) in normalizar(memo) else (f"{nome} - {memo}" if memo else nome)
        if not valor:
            continue
        fitid = tag(bloco, "FITID")
        doc = tag(bloco, "CHECKNUM") or tag(bloco, "REFNUM")
        movs.append(Movimento(id=_id("ofx", fitid or i, d, valor, desc), data=d, descricao=desc, valor=round(valor, 2), documento=doc))
    return movs, info


# -------------------------------------------------------------------------------- Excel / CSV
CAB = {
    "data": [r"^DATA", r"^DT\b"],
    "historico": [r"^HISTORICO", r"^DESCRICAO", r"^LANCAMENTO", r"^MEMO", r"^DETALHE"],
    "complemento": [r"^COMPLEMENTO", r"^FAVORECIDO", r"^PAGADOR", r"^CONTRAPARTE", r"^NOME"],
    "documento": [r"^DOC", r"^NUM.*DOC"],
    "saida": [r"^SAIDA", r"^DEBITO", r"^VALOR DEBITO", r"^SAIDAS"],
    "entrada": [r"^ENTRADA", r"^CREDITO", r"^VALOR CREDITO", r"^ENTRADAS"],
    "valor": [r"^VALOR", r"^VLR", r"^MONTANTE", r"^QUANTIA"],
    "dc": [r"^D/C$", r"^C/D$", r"^TIPO$", r"^NATUREZA"],
    "saldo": [r"^SALDO"],
}


def _mapa(linha: list[str]) -> dict[str, int]:
    m: dict[str, int] = {}
    for i, c in enumerate(linha):
        t = normalizar(c)
        if not t:
            continue
        for campo, pads in CAB.items():
            if campo in m:
                continue
            if any(re.search(p, t) for p in pads):
                m[campo] = i
                break
    return m


def ler_planilha(arquivo, nome: str) -> tuple[list[Movimento], dict]:
    movs: list[Movimento] = []
    info: dict = {}
    for df in ler_tabela(arquivo, nome):
        bruto = df.values.tolist()
        mapa: dict[str, int] = {}
        for k, linha_b in enumerate(bruto):
            linha = [_txt(c) for c in linha_b]
            if not mapa:
                cand = _mapa(linha)
                if "data" in cand and "historico" in cand and ({"valor", "saida", "entrada"} & cand.keys()):
                    mapa = cand
                continue

            def g(c):
                i = mapa.get(c)
                return linha_b[i] if i is not None and i < len(linha_b) else None

            d = parse_data(g("data"))
            hist = _txt(g("historico"))
            comp = _txt(g("complemento"))
            if comp and normalizar(comp) not in normalizar(hist):
                hist = f"{hist} - {comp}"
            if d is None:
                continue
            if RE_SALDO.match(normalizar(hist)):
                s = parse_valor(g("saldo"))
                if s is not None:
                    info.setdefault("saldo_inicial", s)
                continue
            if "saida" in mapa or "entrada" in mapa:
                sai = parse_valor(g("saida")) or 0.0
                ent = parse_valor(g("entrada")) or 0.0
                valor = abs(ent) - abs(sai)
            else:
                valor = parse_valor(g("valor")) or 0.0
                dc = normalizar(_txt(g("dc")))
                if dc.startswith("D") and valor > 0:
                    valor = -valor
                elif dc.startswith("C"):
                    valor = abs(valor)
            if abs(valor) < 0.005:
                continue
            saldo = parse_valor(g("saldo"))
            doc = _txt(g("documento"))
            movs.append(Movimento(id=_id("xls", nome, k, d, valor, hist), data=d, descricao=hist, valor=round(valor, 2), documento=doc, saldo=saldo))
        if movs:
            break
    return movs, info


# ----------------------------------------------------------------------------------------- PDF
RE_VALOR = re.compile(r"-?\(?\d{1,3}(?:\.\d{3})*,\d{2}\)?\s?[-+]?(?:\s?[DC]\b)?")
RE_INICIO_DATA = re.compile(r"^\s*(\d{2}/\d{2}(?:/\d{2,4})?)\s+(.*)$")
SAIDA_PAL = re.compile(r"\b(ENVIAD|PAGAMENTO|PAGTO|PGTO|PAG\b|TARIFA|TAR\b|DEBITO|DEB\b|SAQUE|COMPRA|APLIC|IOF|JUROS|TRANSF.*ENVI|TED ENVI|DOC ENVI|SISPAG|BOLETO PAGO|CONS PARCELA|PARCELA|DARF|DAS\b|GPS|FGTS)")
ENTRADA_PAL = re.compile(r"\b(RECEBID|CREDITO|CRED\b|DEPOSITO|DEP\b|RESGATE|REND|ESTORNO|DEVOLUC|TRANSF.*RECEB|TED RECEB|LIQUIDACAO|COBRANCA)")


def ler_pdf(dados: bytes) -> tuple[list[Movimento], dict]:
    import pdfplumber

    linhas: list[str] = []
    with pdfplumber.open(io.BytesIO(dados)) as pdf:
        for p in pdf.pages:
            linhas.extend((p.extract_text(layout=False) or "").splitlines())
    return interpretar_linhas_pdf(linhas)


def interpretar_linhas_pdf(linhas: list[str]) -> tuple[list[Movimento], dict]:
    todo = "\n".join(linhas)
    anos = re.findall(r"\d{2}/\d{2}/(\d{4})", todo)
    ano = max(set(anos), key=anos.count) if anos else date.today().year
    ano = int(ano)
    info: dict = {}
    movs: list[Movimento] = []
    saldo_ant: float | None = None
    data_atual: date | None = None
    for k, bruta in enumerate(linhas):
        s = bruta.strip()
        if not s:
            continue
        m = RE_INICIO_DATA.match(s)
        resto = s
        d_linha = None
        if m:
            d_linha = parse_data(m[1], ano)
            if d_linha:
                resto = m[2]
        valores = list(RE_VALOR.finditer(resto))
        if d_linha and valores:
            data_atual = d_linha
        elif d_linha and not valores:
            resto = s  # data dentro do detalhe (ex.: "15/05 14:31 CNPJ NOME" no BB)
        norm = normalizar(resto)
        if RE_SALDO.match(norm) or "SALDO" in norm.split(" ")[:3]:
            if valores:
                v = _valor_pdf(valores[-1][0])
                if saldo_ant is None:
                    info.setdefault("saldo_inicial", v)
                saldo_ant = v
            continue
        if not valores or data_atual is None:
            # linha de detalhe (favorecido/pagador) abaixo do lançamento
            if movs and not valores and len(s) > 2 and k > 0:
                ult = movs[-1]
                if len(ult.descricao) < 160 and not re.search(r"(PAGINA|EXTRATO|AGENCIA|CONTA:|PERIODO)", normalizar(s)):
                    ult.descricao = f"{ult.descricao} - {s}"
            continue
        desc = resto[: valores[0].start()].strip(" -")
        desc = re.sub(r"\s+\d{3,}\s*$", "", desc).strip() or desc
        if not desc:
            continue
        v_txt = valores[0][0]
        valor = _valor_pdf(v_txt)
        saldo = _valor_pdf(valores[-1][0]) if len(valores) >= 2 else None
        explicito = bool(re.search(r"[-)]|\bD\b|D$", v_txt.strip()))
        credito_expl = bool(re.search(r"C$", v_txt.strip()))
        if not explicito and not credito_expl:
            if saldo is not None and saldo_ant is not None and abs(abs(saldo - saldo_ant) - abs(valor)) < 0.01:
                valor = abs(valor) if saldo > saldo_ant else -abs(valor)
            elif SAIDA_PAL.search(normalizar(desc)) and not ENTRADA_PAL.search(normalizar(desc)):
                valor = -abs(valor)
            else:
                valor = abs(valor)
        if saldo is not None:
            saldo_ant = saldo
        elif saldo_ant is not None:
            saldo_ant = round(saldo_ant + valor, 2)
        movs.append(Movimento(id=_id("pdf", k, data_atual, valor, desc), data=data_atual, descricao=desc, valor=round(valor, 2), saldo=saldo))
    if saldo_ant is not None:
        info["saldo_final"] = saldo_ant
    return movs, info


def _valor_pdf(t: str) -> float:
    t = t.strip()
    neg = t.startswith("-") or t.endswith("-") or t.startswith("(") or t.rstrip().endswith("D")
    v = parse_valor(re.sub(r"[()\s+DC-]", "", t)) or 0.0
    return -abs(v) if neg else abs(v)
