"""Busca do pagamento/recebimento nas notas em aberto de clientes e fornecedores.

Dado um valor que saiu/entrou no banco, testa várias hipóteses e devolve a mais provável,
com a explicação. Hipóteses (da mais forte para a mais fraca):

  nf        nº da nota citado no extrato/relatório financeiro
  exato     uma nota com saldo igual ao valor
  soma      várias notas cuja soma é o valor (pagamento de várias notas de uma vez)
  retencao  nota menos impostos retidos (IRRF, PIS/COFINS/CSLL, ISS, INSS)
  parcela   valor = nota dividida em N parcelas iguais (2 a 24)
  juros     valor acima do saldo da nota (juros/multa por atraso)
  desconto  valor um pouco abaixo do saldo (desconto/abatimento)
  parcial   valor abaixo do saldo da nota mais antiga (pagamento parcial)
  peps      valor quita as notas mais antigas e abate parte da seguinte
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date

from .modelos import Titulo

TOL = 0.011  # um centavo de arredondamento


@dataclass
class Baixa:
    titulo: Titulo
    valor: float          # quanto do saldo da nota este movimento baixa (inclui retenções/desconto)


@dataclass
class Candidato:
    conta: str
    tipo: str
    baixas: list[Baixa]
    pontos: int
    explicacao: str
    juros: float = 0.0                                     # valor acima das notas
    desconto: float = 0.0                                  # valor abaixo, tratado como desconto
    retencoes: list[tuple[str, float]] = field(default_factory=list)
    parcela: str = ""                                      # "2/3"

    @property
    def nfs(self) -> list[str]:
        out = []
        for b in self.baixas:
            if b.titulo.nf and b.titulo.nf not in out:
                out.append(b.titulo.nf)
        return out

    @property
    def nf_texto(self) -> str:
        return ", ".join(self.nfs)

    def aplicar(self) -> None:
        """Baixa os títulos para que não sejam usados de novo."""
        for b in self.baixas:
            b.titulo.saldo = round(max(0.0, b.titulo.saldo - b.valor), 2)


# ------------------------------------------------------------------------------- retenções
# Alíquotas usuais de retenção na fonte sobre serviços. Cada combinação é testada.
_IR = [(0, ""), (1.0, "IRRF 1%"), (1.5, "IRRF 1,5%"), (4.8, "IRRF 4,8%")]
_CSRF = [(0, ""), (4.65, "PIS/COFINS/CSLL 4,65%")]
_ISS = [(0, "")] + [(p, f"ISS {p:g}%".replace('.', ',')) for p in (2.0, 2.5, 3.0, 3.5, 4.0, 5.0)]
_INSS = [(0, ""), (11.0, "INSS 11%")]


def combinacoes_retencao() -> list[list[tuple[str, float]]]:
    out = []
    for ir, cs, iss, inss in itertools.product(_IR, _CSRF, _ISS, _INSS):
        comps = [(n, p) for p, n in (ir, cs, iss, inss) if p]
        if comps:
            out.append(comps)
    return out


_COMBOS = combinacoes_retencao()


def _valores_retencao(base: float, comps: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Separa PIS/COFINS/CSLL em três valores, como sai na guia."""
    out = []
    for nome, p in comps:
        if nome.startswith("PIS/COFINS/CSLL"):
            out += [("PIS 0,65%", round(base * 0.0065, 2)), ("COFINS 3%", round(base * 0.03, 2)),
                    ("CSLL 1%", round(base * 0.01, 2))]
        else:
            out.append((nome, round(base * p / 100, 2)))
    return out


# --------------------------------------------------------------------------------- busca
def buscar(abertos: list[Titulo], valor: float, data: date | None = None, nfs: list[str] | None = None,
           tol_pct: float = 10.0, max_notas: int = 6, usar_retencao: bool = True) -> list[Candidato]:
    """Devolve as hipóteses ordenadas da melhor para a pior (pode ser vazio)."""
    valor = round(abs(valor), 2)
    if data:
        # nota emitida depois do pagamento não pode ser a paga (tolerância de 5 dias p/ lançamento atrasado)
        abertos = [t for t in abertos if (t.data - data).days <= 5]
    abertos = sorted((t for t in abertos if t.aberto), key=lambda t: (t.data, t.nf))
    if not abertos or valor <= 0:
        return []
    conta = abertos[0].conta
    cands: list[Candidato] = []
    tol = tol_pct / 100

    def add(tipo, baixas, pontos, expl, **kw):
        cands.append(Candidato(conta, tipo, baixas, pontos, expl, **kw))

    # 1) nota(s) citadas
    if nfs:
        citadas = [t for t in abertos if set(t.nf.split("/")) & set(nfs)]
        if citadas:
            soma = round(sum(t.saldo for t in citadas), 2)
            if abs(soma - valor) < TOL:
                add("nf", [Baixa(t, t.saldo) for t in citadas], 99, f"NF {', '.join(t.nf for t in citadas)} citada(s) e valor confere")
            elif valor < soma:
                c = _parcela_ou_parcial(citadas, valor, 90, "citada")
                if c:
                    cands.append(c)
            elif valor - soma <= max(1.0, soma * tol):
                add("juros", [Baixa(t, t.saldo) for t in citadas], 92, f"NF citada; {valor - soma:.2f} de juros/multa",
                    juros=round(valor - soma, 2))
            else:
                add("nf", [Baixa(t, t.saldo) for t in citadas], 70,
                    f"NF citada, mas o valor ({valor:.2f}) é maior que o saldo ({soma:.2f})", juros=round(valor - soma, 2))

    # 2) uma nota com o mesmo saldo
    iguais = [t for t in abertos if abs(t.saldo - valor) < TOL]
    if iguais:
        t = iguais[0]
        if len(iguais) == 1:
            add("exato", [Baixa(t, t.saldo)], 95, f"Saldo da NF {t.nf or 's/n'} igual ao valor")
        else:
            add("exato", [Baixa(t, t.saldo)], 86,
                f"{len(iguais)} notas com esse valor; usada a mais antiga (NF {t.nf or 's/n'})")

    # 3) soma de várias notas
    if not iguais:
        base = abertos[:20]
        achou = False
        for n in range(2, min(max_notas, len(base)) + 1):
            for comb in itertools.combinations(base, n):
                if abs(sum(t.saldo for t in comb) - valor) < TOL:
                    consecutivas = _consecutivas(comb, base)
                    add("soma", [Baixa(t, t.saldo) for t in comb], 92 if consecutivas else 87,
                        f"Soma de {n} notas ({', '.join(t.nf or 's/n' for t in comb)})")
                    achou = True
                    break
            if achou:
                break

    # 4) retenção de impostos (nota cheia menos tributos retidos)
    if usar_retencao and not iguais:
        for t in abertos[:30]:
            if abs(t.saldo - t.valor) > TOL:   # só nota ainda sem baixa
                continue
            for comps in _COMBOS:
                ret = _valores_retencao(t.valor, comps)
                liquido = round(t.valor - sum(v for _, v in ret), 2)
                if abs(liquido - valor) < 0.025:
                    nomes = " + ".join(n for n, _ in comps)
                    add("retencao", [Baixa(t, t.saldo)], 88 - len(comps), f"NF {t.nf or 's/n'} com retenção de {nomes}",
                        retencoes=ret)
                    break

    # 5) parcela de nota
    for t in abertos[:30]:
        c = _parcela_ou_parcial([t], valor, 82, "parcela", so_parcela=True)
        if c:
            cands.append(c)

    # 6) juros (valor acima da nota)
    for t in abertos:
        dif = round(valor - t.saldo, 2)
        if 0 < dif <= max(1.0, t.saldo * tol):
            pontos = 80 - int(10 * dif / max(t.saldo, 1))
            add("juros", [Baixa(t, t.saldo)], pontos, f"NF {t.nf or 's/n'} + {dif:.2f} de juros/multa", juros=dif)
    # juros sobre soma de notas consecutivas (as mais antigas)
    acum = 0.0
    for i, t in enumerate(abertos[:10]):
        acum += t.saldo
        dif = round(valor - acum, 2)
        if i and 0 < dif <= max(1.0, acum * tol / 2):
            add("juros", [Baixa(x, x.saldo) for x in abertos[: i + 1]], 72,
                f"Notas mais antigas ({i + 1}) + {dif:.2f} de juros", juros=dif)

    # 7) desconto pequeno (valor um pouco abaixo da nota)
    for t in abertos:
        dif = round(t.saldo - valor, 2)
        if 0 < dif <= max(0.5, t.saldo * min(tol, 0.05)):
            add("desconto", [Baixa(t, t.saldo)], 70 - int(10 * dif / max(t.saldo, 1)),
                f"NF {t.nf or 's/n'} com {dif:.2f} a menos (desconto/abatimento?)", desconto=dif)

    # 8) pagamento parcial / PEPS
    primeiro = abertos[0]
    if valor < primeiro.saldo - TOL:
        add("parcial", [Baixa(primeiro, valor)], 55,
            f"Pagamento parcial da NF {primeiro.nf or 's/n'} (mais antiga); fica {primeiro.saldo - valor:.2f} em aberto")
    else:
        restante, baixas = valor, []
        for t in abertos:
            if restante < TOL:
                break
            usado = round(min(t.saldo, restante), 2)
            baixas.append(Baixa(t, usado))
            restante = round(restante - usado, 2)
        if restante < TOL and len(baixas) >= 2:
            ult = baixas[-1]
            parcial = ult.valor < ult.titulo.saldo - TOL
            add("peps", baixas, 50, "Baixa das notas mais antigas"
                + (f"; NF {ult.titulo.nf or 's/n'} parcial" if parcial else ""))

    cands.sort(key=lambda c: (-c.pontos, len(c.baixas), min(b.titulo.data for b in c.baixas)))
    # remove repetidos (mesmo tipo e mesmas notas)
    vistos, unicos = set(), []
    for c in cands:
        k = (c.tipo, tuple(id(b.titulo) for b in c.baixas))
        if k not in vistos:
            vistos.add(k)
            unicos.append(c)
    return unicos


def _consecutivas(comb, base) -> bool:
    idx = sorted(base.index(t) for t in comb)
    return idx == list(range(idx[0], idx[0] + len(idx)))


def _parcela_ou_parcial(titulos: list[Titulo], valor: float, pontos: int, origem: str, so_parcela=False) -> Candidato | None:
    t = titulos[0]
    if valor >= t.saldo - TOL:
        return None
    for n in range(2, 25):
        parc = round(t.valor / n, 2)
        ultima = round(t.valor - parc * (n - 1), 2)
        if abs(parc - valor) < TOL or abs(ultima - valor) < TOL:
            pagas = round((t.valor - t.saldo) / parc) if parc else 0
            k = pagas + 1
            if k > n:
                continue
            # coerência: o que já foi pago é múltiplo da parcela
            if abs((t.valor - t.saldo) - pagas * parc) > 0.05 * n:
                continue
            return Candidato(t.conta, "parcela", [Baixa(t, valor)], pontos,
                             f"Parcela {k}/{n} da NF {t.nf or 's/n'} ({t.valor:.2f} ÷ {n})", parcela=f"{k}/{n}")
    if so_parcela:
        return None
    return Candidato(t.conta, "parcial", [Baixa(t, valor)], pontos - 15,
                     f"Pagamento parcial da NF {t.nf or 's/n'} ({origem}); fica {t.saldo - valor:.2f} em aberto")


def melhor_entre_contas(por_conta: dict[str, list[Titulo]], valor: float, data: date | None, nfs: list[str] | None,
                        tol_pct: float) -> tuple[Candidato | None, list[Candidato]]:
    """Busca em todos os clientes (ou fornecedores) quando o nome não aparece no extrato.
    Só aceita se a melhor hipótese for forte e claramente melhor que as das outras contas."""
    melhores: list[Candidato] = []
    for conta, tits in por_conta.items():
        cs = [c for c in buscar(tits, valor, data, nfs, tol_pct, max_notas=4)
              if c.tipo in ("nf", "exato", "soma", "retencao", "parcela")]
        if cs:
            melhores.append(cs[0])
    melhores.sort(key=lambda c: -c.pontos)
    if not melhores:
        return None, []
    top = melhores[0]
    if top.pontos >= 80 and (len(melhores) == 1 or top.pontos - melhores[1].pontos >= 8 or top.tipo == "nf"):
        return top, melhores[1:4]
    return None, melhores[:4]
