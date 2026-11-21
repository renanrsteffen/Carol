"""Fluxo completo sem interface: útil para testes e uso por linha de comando."""
from __future__ import annotations

from dataclasses import dataclass, field

from .classificador import Classificador, Contexto
from .extratos import ler_extrato
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
              plano=None) -> Resultado:
    """extratos: lista de (arquivo, código da conta banco)."""
    razao = Razao.carregar(razoes) if razoes else Razao()
    if plano is not None:
        razao.aplicar_plano(ler_plano(plano))
    for cod, tipo in perfil.tipos_conta.items():
        if cod in razao.contas:
            razao.contas[cod].tipo = tipo
    titulos = apurar_titulos(razao, perfil.tipos_conta)
    itens = []
    for f in financeiros or []:
        itens.extend(ler_financeiro(f))
    movs: list[Movimento] = []
    totais: dict = {}
    for arq, conta in extratos:
        lidos, _info = ler_extrato(arq)
        for m in lidos:
            m.conta_banco = conta
        movs.extend(lidos)
        e = sum(m.valor for m in lidos if m.valor > 0)
        s = -sum(m.valor for m in lidos if m.valor < 0)
        te, ts = totais.get(conta, (0.0, 0.0))
        totais[conta] = (te + e, ts + s)
    clf = Classificador(Contexto(perfil=perfil, razao=razao, titulos=titulos, financeiro=itens))
    lancs = clf.classificar(movs)
    return Resultado(lancs, titulos, razao, movs, totais)
