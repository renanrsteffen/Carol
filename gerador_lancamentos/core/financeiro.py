"""Leitura do relatório financeiro do cliente (contas pagas / recebidas com detalhamento)."""
from __future__ import annotations

import re

from .modelos import ItemFinanceiro
from .razao import _txt, ler_tabela
from .util import extrair_nfs, nf_chave, normalizar, parse_data, parse_valor

CAB = {
    "data": [r"^DATA (DE |DO )?(PAGTO|PAGAMENTO|PAGO|BAIXA|RECEB|LIQUID|CREDITO|QUITACAO)", r"^DT\.? ?(PAGTO|PGTO|BAIXA|RECEB|LIQ)", r"^PAGO EM", r"^RECEBIDO EM", r"^DATA$"],
    "valor": [r"^VALOR (PAGO|RECEBIDO|LIQUIDO|BAIXADO|TOTAL PAGO)", r"^VLR\.? (PAGO|RECEB|LIQ)", r"^TOTAL PAGO", r"^VALOR$", r"^VLR\.?$", r"^VALOR TOTAL"],
    "nome": [r"^(FORNECEDOR|CLIENTE|FAVORECIDO|PAGADOR|SACADO|CEDENTE|BENEFICIARIO|RAZAO SOCIAL|NOME|PESSOA|PARCEIRO|CONTATO|EMITENTE|DESTINATARIO)"],
    "nf": [r"^(NF|NFE|NFS|NFSE|NOTA|N\.? ?NOTA|NUM.*NOTA|NUMERO DA NOTA|DOCUMENTO|DOC|N.? DOC|TITULO|DUPLICATA)"],
    "descricao": [r"^(HISTORICO|DESCRICAO|CATEGORIA|PLANO DE CONTAS|CONTA|NATUREZA|CLASSIFICACAO|OBSERV)"],
    "tipo": [r"^(TIPO|OPERACAO|MOVIMENTO)$"],
    "saida": [r"^(SAIDA|SAIDAS|DEBITO|PAGAMENTOS?)$"],
    "entrada": [r"^(ENTRADA|ENTRADAS|CREDITO|RECEBIMENTOS?)$"],
}


def _mapa(linha):
    m = {}
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


def ler_financeiro(arquivo) -> list[ItemFinanceiro]:
    """Aceita planilhas de contas pagas/recebidas. Se o nome do arquivo ou o título indicar
    'pagar'/'receber', usa isso como tipo; senão usa colunas Saída/Entrada ou o sinal."""
    nome = getattr(arquivo, "name", str(arquivo))
    tipo_arq = ""
    nn = normalizar(nome)
    if re.search(r"PAG|FORNEC|DESPES", nn):
        tipo_arq = "pagar"
    elif re.search(r"RECEB|CLIENT|VENDA", nn):
        tipo_arq = "receber"
    itens: list[ItemFinanceiro] = []
    for df in ler_tabela(arquivo, nome):
        mapa = {}
        tipo_aba = tipo_arq
        for linha_b in df.values.tolist():
            linha = [_txt(c) for c in linha_b]
            if not mapa:
                jn = normalizar(" ".join(linha))
                if not tipo_aba:
                    if "CONTAS A PAGAR" in jn or "CONTAS PAGAS" in jn:
                        tipo_aba = "pagar"
                    elif "CONTAS A RECEBER" in jn or "CONTAS RECEBIDAS" in jn:
                        tipo_aba = "receber"
                cand = _mapa(linha)
                if "data" in cand and ({"valor", "saida", "entrada"} & cand.keys()) and len(cand) >= 3:
                    mapa = cand
                continue

            def g(c):
                i = mapa.get(c)
                return linha_b[i] if i is not None and i < len(linha_b) else None

            d = parse_data(g("data"))
            if d is None:
                continue
            tipo = tipo_aba
            if "saida" in mapa or "entrada" in mapa:
                s, e = parse_valor(g("saida")) or 0.0, parse_valor(g("entrada")) or 0.0
                valor = abs(e) or abs(s)
                tipo = "receber" if e else "pagar"
            else:
                v = parse_valor(g("valor"))
                if v is None:
                    continue
                valor = abs(v)
                t = normalizar(_txt(g("tipo")))
                if t.startswith(("D", "S", "P")):
                    tipo = "pagar"
                elif t.startswith(("C", "E", "R")):
                    tipo = "receber"
                elif not tipo:
                    tipo = "pagar" if v < 0 else ""
            if valor < 0.005:
                continue
            nf_txt = _txt(g("nf"))
            nf = nf_chave(nf_txt.split("/")[0].split("-")[0]) if nf_txt else ""
            desc = _txt(g("descricao"))
            if not nf:
                achadas = extrair_nfs(desc)
                nf = achadas[0] if achadas else ""
            itens.append(ItemFinanceiro(data=d, valor=round(valor, 2), nome=_txt(g("nome")), nf=nf, descricao=desc, tipo=tipo))
    return itens
