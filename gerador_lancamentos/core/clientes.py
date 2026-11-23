"""Importação da relação de clientes do escritório (Excel/CSV).

Colunas reconhecidas pelo cabeçalho: razão social (ou nome/empresa), sigla, CNPJ/CPF, regime
tributário (ou tipo de cliente) e cidade. Cada cliente vira um perfil em dados/clientes; perfis que
já existem (mesmo CNPJ ou mesmo nome) são completados, nunca apagados.
"""
from __future__ import annotations

import re

from .perfil import Perfil
from .razao import _txt, ler_tabela
from .util import normalizar

CAB = {
    "nome": [r"^RAZAO SOCIAL", r"^RAZAO_SOCIAL", r"^NOME$", r"^EMPRESA$", r"^CLIENTE$", r"^NOME EMPRESARIAL"],
    "sigla": [r"^SIGLA", r"^APELIDO", r"^CODIGO$", r"^COD$"],
    "cnpj": [r"^CNPJ", r"^CPF", r"^CNPJ.?CPF", r"^DOCUMENTO$", r"^INSCRICAO"],
    "regime": [r"^REGIME", r"^TIPO.CLIENTE", r"^TRIBUTACAO", r"^ENQUADRAMENTO"],
    "cidade": [r"^CIDADE", r"^MUNICIPIO"],
}


def so_digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def ler_relacao_clientes(arquivo) -> list[dict]:
    out: list[dict] = []
    for df in ler_tabela(arquivo, getattr(arquivo, "name", str(arquivo))):
        mapa: dict[str, int] = {}
        for linha in df.values.tolist():
            cel = [_txt(c) for c in linha]
            if not mapa:
                for i, c in enumerate(cel):
                    t = normalizar(c).replace("_", " ")
                    for campo, pads in CAB.items():
                        if campo not in mapa and any(re.search(p.replace("_", " "), t) for p in pads):
                            mapa[campo] = i
                            break
                if "nome" not in mapa:
                    mapa = {}
                continue
            g = lambda k: cel[mapa[k]] if k in mapa and mapa[k] < len(cel) else ""
            nome = g("nome").strip()
            if not nome:
                continue
            out.append({"nome": nome, "sigla": g("sigla").strip(), "cnpj": g("cnpj").strip(),
                        "regime": g("regime").strip(), "cidade": g("cidade").strip()})
        if out:
            break
    return out


def importar_clientes(lista: list[dict]) -> tuple[int, int]:
    """Cria os perfis que faltam e completa os existentes. Retorna (novos, atualizados)."""
    por_cnpj, por_nome = {}, {}
    for p in Perfil.todos():
        if so_digitos(p.cnpj):
            por_cnpj[so_digitos(p.cnpj)] = p
        por_nome[normalizar(p.nome)] = p
    novos = atualizados = 0
    for c in lista:
        p = por_cnpj.get(so_digitos(c["cnpj"])) if so_digitos(c["cnpj"]) else None
        p = p or por_nome.get(normalizar(c["nome"]))
        if p is None:
            p = Perfil(nome=c["nome"], cnpj=c["cnpj"], sigla=c["sigla"], regime=c["regime"], cidade=c["cidade"])
            novos += 1
        else:
            mudou = False
            for campo in ("cnpj", "sigla", "regime", "cidade"):
                if c[campo] and not getattr(p, campo):
                    setattr(p, campo, c[campo])
                    mudou = True
            if not mudou:
                continue
            atualizados += 1
        p.salvar()
        if so_digitos(p.cnpj):
            por_cnpj[so_digitos(p.cnpj)] = p
        por_nome[normalizar(p.nome)] = p
    return novos, atualizados


def cliente_por_cnpj(cnpj: str) -> Perfil | None:
    alvo = so_digitos(cnpj)
    if not alvo:
        return None
    for p in Perfil.todos():
        d = so_digitos(p.cnpj)
        if d and (d == alvo or (len(d) == 14 and len(alvo) == 14 and d[:8] == alvo[:8])):
            return p
    return None
