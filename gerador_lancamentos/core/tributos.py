"""Identificação do tributo de cada linha do comprovante e da conta contábil correspondente.

Regras do escritório:
  - DAS: tudo na conta do Simples Nacional.
  - Impostos normais (apurados e provisionados na despesa): o pagamento debita o passivo "a recolher".
  - Taxas e impostos esporádicos: direto na despesa.
  - Multa e juros: contas próprias.
"""
from __future__ import annotations

import re

from .modelos import Conta
from .util import normalizar

# tributo -> (palavras na denominação, códigos de receita conhecidos, recorrente?)
TRIBUTOS: dict[str, tuple[list[str], list[str], bool]] = {
    "SIMPLES": ([r"SIMPLES NACIONAL", r"\bDAS\b"], [], True),
    "CSRF": ([r"PIS/COFINS/CSLL", r"\bCSRF\b", r"RETENCAO.*CONTRIBUI", r"CONTRIBUICOES RETIDAS", r"PIS.?COFINS.?CSLL"], ["5952"], True),
    "IRRF": ([r"\bIRRF\b", r"RETIDO NA FONTE", r"\bIR FONTE\b", r"IMPOSTO SOBRE A RENDA RETIDO", r"RENDIMENTOS DO TRABALHO",
              r"REMUNERACAO DE SERVICOS", r"ALUGUE"], ["0561", "0588", "1708", "3208", "8045"], True),
    "INSS": ([r"PREVIDENCI", r"\bCP\b", r"\bINSS\b", r"SEGURADOS", r"PATRONAL", r"TERCEIROS", r"\bRAT\b", r"GILRAT",
              r"SALARIO.EDUCACAO", r"\bSENAI\b|\bSESI\b|\bSESC\b|\bSENAC\b|\bSEBRAE\b|\bINCRA\b"], [], True),
    "PARCELAMENTO": ([r"PARCELAMENTO", r"\bPARCEL"], [], True),
    "COFINS": ([r"\bCOFINS\b"], ["2172", "5856"], True),
    "PIS": ([r"\bPIS\b", r"PIS/PASEP", r"\bPASEP\b"], ["8109", "6912"], True),
    "IRPJ": ([r"\bIRPJ\b", r"RENDA.{0,20}PESSOA JURIDICA"], ["2089", "0220", "2362"], True),
    "CSLL": ([r"\bCSLL\b", r"CONTRIBUICAO SOCIAL SOBRE O LUCRO"], ["2372", "2484", "6012"], True),
    "IPI": ([r"\bIPI\b", r"PRODUTOS INDUSTRIALIZADOS"], [], True),
}

NOMES = {
    "SIMPLES": "SIMPLES NACIONAL (DAS)", "CSRF": "PIS/COFINS/CSLL RETIDOS", "IRRF": "IRRF", "INSS": "INSS",
    "PARCELAMENTO": "PARCELAMENTO", "COFINS": "COFINS", "PIS": "PIS", "IRPJ": "IRPJ", "CSLL": "CSLL", "IPI": "IPI",
    "OUTROS": "TAXAS/IMPOSTOS",
}

# padrões para achar a conta de passivo de cada tributo no plano/razão
PASSIVO = {
    "SIMPLES": r"SIMPLES",
    "CSRF": r"(CSRF|PIS.?COFINS.?CSLL|CONTRIBUICOES RETIDAS|RETENCOES|4[,.]65)",
    "IRRF": r"(IRRF|IR RETIDO|IMPOSTO DE RENDA RETIDO|IR S/|IR FONTE)",
    "INSS": r"(INSS|PREVIDENCIA)",
    "PARCELAMENTO": r"PARCELAMENTO",
    "COFINS": r"\bCOFINS\b",
    "PIS": r"\bPIS\b",
    "IRPJ": r"(IRPJ|IMPOSTO DE RENDA)",
    "CSLL": r"(CSLL|CONTRIBUICAO SOCIAL)",
    "IPI": r"\bIPI\b",
}
RE_A_RECOLHER = re.compile(r"RECOLHER|A PAGAR|\bPAGAR\b|PARCELAMENTO|OBRIGACOES")
RE_NAO_PASSIVO = re.compile(r"RECUPERAR|COMPENSAR|ANTECIPAD|DESPESA|\bS/ ?(VENDAS|FATURAMENTO|RECEITA)")


def identificar(codigo: str, denominacao: str, tipo_doc: str = "") -> tuple[str, bool]:
    """Retorna (tributo, recorrente). Não identificado -> ('OUTROS', False) = despesa direta."""
    if tipo_doc == "DAS":
        return "SIMPLES", True
    d = normalizar(denominacao)
    for trib, (pads, codigos, rec) in TRIBUTOS.items():
        if trib == "SIMPLES":
            continue
        if codigo and codigo in codigos:
            return trib, rec
    for trib, (pads, codigos, rec) in TRIBUTOS.items():
        if any(re.search(p, d) for p in pads):
            return trib, rec
    if tipo_doc in ("DCTFWEB", "GPS"):
        return "INSS", True
    return "OUTROS", False


def sugerir_contas(contas: dict[str, Conta]) -> dict[str, str]:
    """Acha no plano/razão a conta de cada tributo, de multa, de juros e de taxas esporádicas."""
    out: dict[str, str] = {}
    lista = sorted(contas.values(), key=lambda c: (c.classificacao or "9", c.codigo))
    for trib, pad in PASSIVO.items():
        cands = []
        for c in lista:
            n = normalizar(c.nome)
            if not re.search(pad, n) or RE_NAO_PASSIVO.search(n):
                continue
            pontos = 0
            if RE_A_RECOLHER.search(n):
                pontos += 2
            if c.classificacao.startswith("2"):
                pontos += 2
            elif c.classificacao[:1] in ("1", "3", "4", "5", "6"):
                continue
            if trib == "PIS" and re.search(r"COFINS|CSLL", n):
                pontos -= 2  # "PIS/COFINS/CSLL retidos" não é a conta do PIS próprio
            if pontos >= 2:
                cands.append((pontos, c.codigo))
        if cands:
            out[trib] = max(cands)[1]
    for chave, pad in (("MULTA", r"\bMULTAS?\b"), ("JUROS", r"\bJUROS\b")):
        cands = []
        for c in lista:
            n = normalizar(c.nome)
            if not re.search(pad, n) or c.classificacao.startswith(("1", "2")) or re.search(r"RECEBID|ATIVO|APLIC", n):
                continue
            pontos = 1
            if re.search(r"FISCA|TRIBUT|IMPOST|MORA", n):
                pontos += 2
            if chave == "JUROS" and re.search(r"BANC|EMPREST|FINANC", n):
                pontos -= 1
            cands.append((pontos, c.codigo))
        if cands:
            out[chave] = max(cands)[1]
    for c in lista:
        n = normalizar(c.nome)
        if re.search(r"IMPOSTOS E TAXAS|TAXAS E IMPOSTOS|IMPOSTOS, TAXAS|IMPOSTOS TAXAS|TAXAS DIVERSAS|TRIBUTOS DIVERSOS|DESPESAS TRIBUTARIAS", n) \
                and not c.classificacao.startswith(("1", "2")):
            out["OUTROS"] = c.codigo
            break
    return out


def conta_banco_do_comprovante(banco: str, agencia: str, perfil, contas: dict[str, Conta], bancos_extrato: set[str]) -> tuple[str, int]:
    """(conta, confiança). Usa o que estiver gravado no cliente; senão procura pelo nome do banco."""
    from .ecac import BANCOS
    for chave in (f"{banco}|{agencia}", banco):
        if chave and chave in perfil.bancos_comprovante:
            return perfil.bancos_comprovante[chave], 100
    nome = BANCOS.get(banco, "")
    contas_banco = [c for c in contas.values() if c.tipo == "banco"]
    if nome:
        palavras = [p for p in nome.split() if len(p) >= 4] or nome.split()
        achadas = [c for c in contas_banco if all(p in normalizar(c.nome) for p in palavras[:2])]
        if agencia:
            com_ag = [c for c in achadas if agencia in c.nome]
            if len(com_ag) == 1:
                return com_ag[0].codigo, 95
        if len(achadas) == 1:
            return achadas[0].codigo, 90
    def outro_banco(c: Conta) -> bool:
        """A conta é claramente de outro banco (ex.: comprovante do Itaú, conta do Banco do Brasil)."""
        n = normalizar(c.nome)
        for cod, nm in BANCOS.items():
            if cod != banco and all(p in n for p in (nm.split()[:2] if len(nm.split()) > 1 else nm.split())):
                return True
        return False
    candidatas = [c for c in contas_banco if not (nome and outro_banco(c))]
    if len(candidatas) == 1:
        return candidatas[0].codigo, 60
    extrato_ok = [b for b in bancos_extrato if b not in contas or not (nome and outro_banco(contas[b]))]
    if len(extrato_ok) == 1 and not contas_banco:
        return extrato_ok[0], 60
    return "", 0
