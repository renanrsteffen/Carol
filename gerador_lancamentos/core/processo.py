"""Fluxo completo sem interface: útil para testes e uso por linha de comando."""
from __future__ import annotations

from dataclasses import dataclass, field

from .classificador import Classificador, Contexto
from .ecac import ler_comprovantes
from .extratos import ler_extrato
from .conciliado import ler_conciliado
from .financeiro import ler_financeiro
from .modelos import Lancamento, Movimento, Titulo
from .perfil import Perfil
from .razao import Razao, apurar_titulos, ler_plano


@dataclass
class Resultado:
    lancamentos: list[Lancamento]
    titulos: list[Titulo]
    razao: Razao
    movimentos: list[Movimento]
    totais_extrato: dict = field(default_factory=dict)


def processar(perfil: Perfil, razoes: list, extratos: list[tuple], financeiros: list | None = None,
              plano=None, comprovantes: list | None = None, razao: Razao | None = None) -> Resultado:
    """extratos: lista de (arquivo, código da conta banco)."""
    razao = razao or (Razao.carregar(razoes) if razoes else Razao())
    if plano is not None:
        razao.aplicar_plano(ler_plano(plano))
    for cod, tipo in perfil.tipos_conta.items():
        if cod in razao.contas:
            razao.contas[cod].tipo = tipo
    titulos = apurar_titulos(razao, perfil.tipos_conta)
    itens = []
    for f in financeiros or []:
        nome = str(getattr(f, "name", f)).lower()
        itens.extend(ler_conciliado(f) if nome.endswith(".pdf") else ler_financeiro(f))
    movs: list[Movimento] = []
    totais: dict = {}
    for arq, conta in extratos:
        lidos, _info = ler_extrato(arq)
        if _info.get("banco"):
            perfil.bancos_comprovante.setdefault(_info["banco"], conta)
        for m in lidos:
            m.conta_banco = conta
        movs.extend(lidos)
        e = sum(m.valor for m in lidos if m.valor > 0)
        s = -sum(m.valor for m in lidos if m.valor < 0)
        te, ts = totais.get(conta, (0.0, 0.0))
        totais[conta] = (te + e, ts + s)
    comps = []
    for f in comprovantes or []:
        comps.extend(ler_comprovantes(f))
    clf = Classificador(Contexto(perfil=perfil, razao=razao, titulos=titulos, financeiro=itens, comprovantes=comps))
    lancs = clf.classificar(movs)
    res = Resultado(lancs, titulos, razao, movs, totais)
    res.classificador = clf
    return res
