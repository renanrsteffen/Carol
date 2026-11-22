"""Converte o trabalho em andamento (lançamentos, extratos, notas, razão) de/para JSON."""
from __future__ import annotations

from dataclasses import asdict, fields

from .ecac import Comprovante
from .modelos import Conta, ItemFinanceiro, Lancamento, Movimento, MovRazao, Titulo
from .razao import Razao


def _lista(cls, itens):
    nomes = {f.name for f in fields(cls)}
    return [cls(**{k: v for k, v in i.items() if k in nomes}) for i in itens or []]


def empacotar(*, lancs, movs, titulos_inicio, titulos, razao: Razao | None, totais, financeiro, editados=None, forcados=None, comprovantes=None) -> dict:
    return {
        "lancs": [asdict(l) for l in lancs],
        "movs": [asdict(m) for m in movs],
        "titulos_inicio": [asdict(t) for t in titulos_inicio],
        "titulos": [asdict(t) for t in titulos],
        "contas": [asdict(c) for c in (razao.contas.values() if razao else [])],
        "razao_movs": [asdict(m) for m in (razao.movimentos if razao else [])],
        "saldos_anteriores": {k: list(v) for k, v in (razao.saldos_anteriores.items() if razao else [])},
        "totais": {k: list(v) for k, v in (totais or {}).items()},
        "financeiro": [asdict(i) for i in financeiro or []],
        "editados": sorted(editados or []),
        "forcados": forcados or {},
        "comprovantes": [c.para_dict() for c in comprovantes or []],
    }


def desempacotar(d: dict) -> dict:
    razao = Razao()
    for c in _lista(Conta, d.get("contas")):
        razao.contas[c.codigo] = c
    razao.movimentos = _lista(MovRazao, d.get("razao_movs"))
    razao.saldos_anteriores = {k: tuple(v) for k, v in (d.get("saldos_anteriores") or {}).items()}
    return {
        "lancs": _lista(Lancamento, d.get("lancs")),
        "movs": _lista(Movimento, d.get("movs")),
        "titulos_inicio": _lista(Titulo, d.get("titulos_inicio")),
        "titulos": _lista(Titulo, d.get("titulos")),
        "razao": razao,
        "totais": {k: tuple(v) for k, v in (d.get("totais") or {}).items()},
        "financeiro_itens": _lista(ItemFinanceiro, d.get("financeiro")),
        "editados": set(d.get("editados") or []),
        "forcados": d.get("forcados") or {},
        "comprovantes": [Comprovante.de_dict(c) for c in d.get("comprovantes") or []],
    }


def competencia(lancs) -> str:
    """Mês predominante dos lançamentos, no formato AAAA-MM."""
    from collections import Counter
    if not lancs:
        return ""
    c = Counter(f"{l.data.year}-{l.data.month:02d}" for l in lancs)
    return c.most_common(1)[0][0]
