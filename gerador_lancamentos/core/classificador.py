"""Motor que transforma linhas de extrato em lançamentos contábeis.

Ordem de decisão para cada movimento:
  1. Já lançado no razão (mesma conta banco, data e valor)       -> marcado, fora da exportação
  2. Memória do cliente (correções anteriores memorizadas)
  3. Relatório financeiro do cliente (favorecido + NF por data/valor)
  4. Cliente/fornecedor identificado (CNPJ ou nome) + nota em aberto
  5. Regras de categoria (tarifa, aplicação, telefone, impostos...)
  6. Semelhança com o histórico do razão (aprendizado)
  7. Nota em aberto de valor único (sem identificar o nome)      -> revisar
  8. Pendente
"""
from __future__ import annotations

import itertools
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from rapidfuzz import fuzz

from .modelos import Conta, ItemFinanceiro, Lancamento, Movimento, MovRazao, Titulo
from .perfil import RUIDO_BANCARIO, Perfil, Regra, chave_memoria
from .razao import Razao
from .util import extrair_nfs, nf_chave, normalizar, tokens

MESES = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]


def nome_limpo(texto: str) -> str:
    """Tira do texto do extrato as palavras de banco, números e datas, sobrando o nome."""
    return " ".join(t for t in tokens(texto) if t not in RUIDO_BANCARIO)


@dataclass
class Contexto:
    perfil: Perfil
    razao: Razao | None = None
    titulos: list[Titulo] = field(default_factory=list)
    financeiro: list[ItemFinanceiro] = field(default_factory=list)


class Classificador:
    def __init__(self, ctx: Contexto):
        self.ctx = ctx
        self.perfil = ctx.perfil
        self.razao = ctx.razao or Razao()
        self.contas: dict[str, Conta] = self.razao.contas
        for cod, tipo in self.perfil.tipos_conta.items():
            if cod in self.contas:
                self.contas[cod].tipo = tipo
        self.titulos = ctx.titulos
        self.financeiro = ctx.financeiro
        self.regras = self.perfil.obter_regras()
        self.contas_regra = self._aprender_contas_das_regras()
        self._indice_historico = self._montar_indice()
        self.ja_lancados = self._indexar_razao_banco()

    # ------------------------------------------------------------------ aprendizado
    def _bancos(self) -> set[str]:
        bancos = {c.codigo for c in self.contas.values() if c.tipo == "banco"}
        bancos |= {v for v in self.perfil.contas_banco.values() if v}
        return bancos

    def _aprender_contas_das_regras(self) -> dict[str, tuple[str, int]]:
        """Para regra sem conta, descobre no razão a contrapartida mais usada em históricos
        parecidos (ex.: históricos com TARIFA lançados contra 'Despesas bancárias')."""
        out: dict[str, tuple[str, int]] = {}
        movs = self.razao.movimentos_banco(self._bancos())
        for r in self.regras:
            if r.conta:
                out[r.nome] = (r.conta, 100)
                continue
            cont = Counter()
            for m in movs:
                entrada = m.debito > 0
                if r.casa(normalizar(m.historico), entrada):
                    cont[m.contrapartida] += 1
            if cont:
                conta, n = cont.most_common(1)[0]
                if conta in self.contas and self.contas[conta].tipo not in ("cliente", "fornecedor"):
                    out[r.nome] = (conta, min(92, 77 + 5 * n))
        return out

    def _montar_indice(self):
        """Índice dos históricos de banco do razão: palavras -> contrapartida."""
        idx = []
        for m in self.razao.movimentos_banco(self._bancos()):
            tk = tokens(m.historico)
            if tk:
                idx.append((" ".join(tk), m.contrapartida, m.debito > 0, m.historico))
        return idx

    def _indexar_razao_banco(self):
        d = defaultdict(list)
        for m in self.razao.movimentos:
            if m.conta in self._bancos():
                d[(m.conta, round(m.debito - m.credito, 2))].append(m)
        return d

    # ------------------------------------------------------------------ utilidades
    def nome_conta(self, cod: str) -> str:
        c = self.contas.get(cod)
        return c.nome if c else ""

    def _modelo(self, chave: str, **kw) -> str:
        modelo = self.perfil.modelos.get(chave, "")
        return formatar(modelo, **kw)

    def _partes(self, tipo: str) -> list[Conta]:
        return [c for c in self.contas.values() if c.tipo == tipo]

    def identificar_parte(self, mov: Movimento, nome_extra: str = "") -> tuple[Conta | None, int]:
        """Descobre a conta de cliente/fornecedor pelo CNPJ, apelido memorizado ou nome."""
        tipo_pref = "cliente" if mov.entrada else "fornecedor"
        if mov.cnpj and mov.cnpj in self.perfil.cnpj_conta:
            c = self.contas.get(self.perfil.cnpj_conta[mov.cnpj])
            if c:
                return c, 98
        alvo = normalizar(nome_extra) if nome_extra else nome_limpo(mov.descricao)
        if not alvo:
            return None, 0
        for apelido, cod in self.perfil.apelidos.items():
            if apelido and (apelido == alvo or (len(apelido) >= 6 and apelido in alvo)):
                c = self.contas.get(cod)
                if c:
                    return c, 95
        melhor, melhor_s = None, 0.0
        alvo_tk = set(tokens(alvo))
        for tipo in (tipo_pref, "fornecedor" if tipo_pref == "cliente" else "cliente"):
            for c in self._partes(tipo):
                nome = nome_limpo(c.nome) or normalizar(c.nome)
                if not nome:
                    continue
                comuns = {t for t in alvo_tk & set(tokens(nome)) if len(t) >= 3}
                if not comuns:
                    continue
                s = max(fuzz.token_set_ratio(alvo, nome), fuzz.partial_ratio(alvo, nome) if len(alvo) >= 8 else 0)
                # nomes truncados no extrato: primeira palavra forte igual ajuda
                if alvo.split()[0] == nome.split()[0] and len(alvo.split()[0]) >= 4:
                    s += 5
                if tipo != tipo_pref:
                    s -= 8
                if s > melhor_s:
                    melhor, melhor_s = c, s
            if melhor_s >= 92:
                break
        if melhor and melhor_s >= 80:
            return melhor, int(min(melhor_s, 97))
        return None, 0

    def titulos_abertos(self, conta: str) -> list[Titulo]:
        return sorted((t for t in self.titulos if t.conta == conta and t.aberto), key=lambda t: t.data)

    def casar_titulos(self, conta: str, valor: float, nfs_sugeridas: list[str] | None = None):
        """Retorna (titulos, diferença) que explicam o valor pago/recebido.
        diferença > 0: pago a mais (juros); < 0: pago a menos (desconto/parcial)."""
        abertos = self.titulos_abertos(conta)
        if not abertos:
            return [], valor
        if nfs_sugeridas:
            sel = [t for t in abertos if set(t.nf.split("/")) & set(nfs_sugeridas)]
            if sel:
                return sel, round(valor - sum(t.saldo for t in sel), 2)
        for t in abertos:
            if abs(t.saldo - valor) < 0.01:
                return [t], 0.0
        candidatos = abertos[:14]
        for n in range(2, min(5, len(candidatos)) + 1):
            for comb in itertools.combinations(candidatos, n):
                if abs(sum(t.saldo for t in comb) - valor) < 0.01:
                    return list(comb), 0.0
        tol = self.perfil.tolerancia_juros_pct / 100
        melhor = None
        for t in abertos:
            dif = valor - t.saldo
            if abs(dif) <= max(0.5, t.saldo * tol):
                if melhor is None or abs(dif) < abs(melhor[1]):
                    melhor = ([t], round(dif, 2))
        if melhor:
            return melhor
        return [], valor

    # ------------------------------------------------------------------ principal
    def classificar(self, movimentos: list[Movimento]) -> list[Lancamento]:
        saida: list[Lancamento] = []
        usados_razao: set[int] = set()
        for mov in sorted(movimentos, key=lambda m: (m.data, m.valor)):
            saida.extend(self._classificar_um(mov, usados_razao))
        return saida

    def _base(self, mov: Movimento, **kw) -> Lancamento:
        l = Lancamento(
            mov_id=mov.id, data=mov.data, conta_banco=mov.conta_banco, valor=round(abs(mov.valor), 2),
            natureza="entrada" if mov.entrada else "saida", descricao_extrato=mov.descricao,
        )
        for k, v in kw.items():
            setattr(l, k, v)
        return l

    def _classificar_um(self, mov: Movimento, usados_razao: set[int]) -> list[Lancamento]:
        texto = normalizar(mov.descricao)
        kw_desc = descricao_curta(mov.descricao)

        # 1) já lançado no razão
        for m in self.ja_lancados.get((mov.conta_banco, round(mov.valor, 2)), []):
            if id(m) in usados_razao:
                continue
            if abs((m.data - mov.data).days) <= 1:
                usados_razao.add(id(m))
                self._aprender_de_razao(mov, m)
                return [self._base(mov, conta_contra=m.contrapartida, historico=m.historico, status="ja_lancado",
                                   origem="Já consta no razão", confianca=100,
                                   observacao=f"Lote {m.lote} lanç. {m.lancamento}".strip())]

        # 2) memória
        mem = self.perfil.memoria.get(chave_memoria(mov.descricao))
        if mem and mem.get("conta"):
            conta = mem["conta"]
            c = self.contas.get(conta)
            if c and c.tipo in ("cliente", "fornecedor"):
                r = self._lancar_parte(mov, c, 96, "Memória do cliente")
                if r:
                    return r
            hist = formatar(mem.get("historico") or "", parte=self.nome_conta(conta), descricao=kw_desc,
                            nf="", mes=_mes(mov))
            return [self._base(mov, conta_contra=conta, historico=hist or self._generico(mov),
                               status="ok", origem="Memória do cliente", confianca=96)]

        # 3) relatório financeiro do cliente
        itens = self._casar_financeiro(mov)
        if itens:
            r = self._lancar_financeiro(mov, itens)
            if r:
                return r

        # 4) cliente/fornecedor pelo nome/CNPJ
        parte, score = self.identificar_parte(mov)
        regra = self._regra(mov, texto)
        if parte and (score >= 88 or not regra):
            r = self._lancar_parte(mov, parte, score, "Cliente/fornecedor identificado")
            if r:
                return r

        # 5) regras de categoria
        if regra:
            r, (conta, conf) = regra
            hist = formatar(r.historico, parte=nome_limpo(mov.descricao) or kw_desc, descricao=kw_desc, nf="", mes=_mes(mov)) or self._generico(mov)
            return [self._base(mov, conta_contra=conta, historico=hist, status="ok" if conta and conf >= 80 else ("revisar" if conta else "pendente"),
                               origem=f"Regra: {r.nome}", confianca=conf if conta else 40,
                               observacao="" if conta else f"Informe a conta da regra '{r.nome}' em Configurações")]

        # 6) semelhança com o razão
        sim = self._por_semelhanca(mov)
        if sim:
            conta, conf, hist_razao = sim
            c = self.contas.get(conta)
            if c and c.tipo in ("cliente", "fornecedor"):
                r = self._lancar_parte(mov, c, conf, "Semelhança com o razão")
                if r:
                    return r
            return [self._base(mov, conta_contra=conta, historico=self._generico(mov, self.nome_conta(conta)),
                               status="revisar" if conf < 85 else "ok", origem="Semelhança com o razão",
                               confianca=conf, observacao=f"Parecido com: {hist_razao[:60]}")]

        # 7) título de valor único (sem nome)
        unico = self._titulo_unico_por_valor(mov)
        if unico:
            t = unico
            c = self.contas.get(t.conta)
            r = self._lancar_parte(mov, c, 60, "Valor igual a nota em aberto")
            if r:
                for l in r:
                    l.status = "revisar"
                    l.observacao = (l.observacao + " Nome não identificado no extrato; confira.").strip()
                return r

        if parte:
            r = self._lancar_parte(mov, parte, score, "Cliente/fornecedor identificado")
            if r:
                return r
        return [self._base(mov, historico=self._generico(mov), status="pendente", origem="Não identificado", confianca=0,
                           observacao="Escolha a conta de contrapartida")]

    # ------------------------------------------------------------------ partes / NF
    def _lancar_parte(self, mov: Movimento, conta: Conta, score: int, origem: str, nfs: list[str] | None = None,
                      nome_parte: str = "") -> list[Lancamento] | None:
        if conta is None:
            return None
        valor = round(abs(mov.valor), 2)
        tipo = conta.tipo
        if tipo not in ("cliente", "fornecedor"):
            tipo = "cliente" if mov.entrada else "fornecedor"
        parte = nome_parte or conta.nome
        nfs = nfs or extrair_nfs(mov.descricao)
        sel, dif = self.casar_titulos(conta.codigo, valor, nfs)
        pagando = not mov.entrada
        if not sel:
            chave = "pagamento_fornecedor" if pagando else "recebimento_cliente"
            nf_txt = ", ".join(nfs) if nfs else ""
            if nf_txt:
                hist = self._modelo(chave + "_nf", nf=nf_txt, parte=parte)
            else:
                hist = self._modelo(chave, parte=parte, nf="")
            abertos = self.titulos_abertos(conta.codigo)
            obs = "Nenhuma nota em aberto com esse valor" + (
                f" ({len(abertos)} em aberto: " + ", ".join(f"NF {t.nf or 's/n'} {t.saldo:.2f}" for t in abertos[:4]) + ")" if abertos else "")
            return [self._base(mov, conta_contra=conta.codigo, historico=hist, nf=nf_txt, parte=parte,
                               status="revisar", origem=origem, confianca=min(score, 70), observacao=obs)]

        nf_txt = ", ".join(t.nf for t in sel if t.nf)
        chave = ("pagamento_fornecedor" if pagando else "recebimento_cliente") + ("_nf" if nf_txt else "")
        saldo_sel = round(sum(t.saldo for t in sel), 2)
        conta_dif, chave_dif = self._conta_diferenca(pagando, dif)
        hist = self._modelo(chave, nf=nf_txt, parte=parte)
        out: list[Lancamento] = []
        ok = "ok" if score >= 85 else "revisar"

        if not dif:
            out.append(self._base(mov, conta_contra=conta.codigo, historico=hist, nf=nf_txt, parte=parte,
                                  status=ok, origem=origem, confianca=score))
            for t in sel:
                t.saldo = 0.0
            return out

        if dif > 0:  # pagou/recebeu a mais que o saldo das notas: juros/multa
            if conta_dif:
                out.append(self._base(mov, valor=saldo_sel, conta_contra=conta.codigo, historico=hist, nf=nf_txt,
                                      parte=parte, status=ok, origem=origem, confianca=score))
                out.append(self._base(mov, valor=round(dif, 2), conta_contra=conta_dif,
                                      historico=self._modelo(chave_dif, nf=nf_txt, parte=parte), nf=nf_txt, parte=parte,
                                      status="revisar", origem=origem + " (juros)", confianca=70,
                                      observacao=f"Valor {dif:.2f} acima do saldo da nota: lançado como juros"))
            else:
                out.append(self._base(mov, conta_contra=conta.codigo, historico=hist, nf=nf_txt, parte=parte,
                                      status="revisar", origem=origem, confianca=min(score, 75),
                                      observacao=f"Valor {dif:.2f} acima do saldo da nota (juros?). Configure a conta de juros para separar."))
            for t in sel:
                t.saldo = 0.0
            return out

        # pagou/recebeu a menos: desconto (se configurado) ou pagamento parcial
        falta = round(-dif, 2)
        out.append(self._base(mov, conta_contra=conta.codigo, historico=hist, nf=nf_txt, parte=parte,
                              status="revisar", origem=origem, confianca=min(score, 75),
                              observacao=f"Valor {falta:.2f} abaixo do saldo da nota"
                                         + (": desconto lançado à parte" if conta_dif else " (desconto ou pagamento parcial?)")))
        if conta_dif:
            # lançamento sem banco: fornecedor x desconto obtido / desconto concedido x cliente
            out.append(Lancamento(
                mov_id=mov.id, data=mov.data, conta_banco=conta.codigo, valor=falta,
                natureza="entrada" if pagando else "saida",   # D fornecedor / C cliente
                conta_contra=conta_dif, historico=self._modelo(chave_dif, nf=nf_txt, parte=parte), nf=nf_txt,
                descricao_extrato=mov.descricao, confianca=70, origem=origem + " (desconto)", status="revisar",
                observacao="Lançamento sem movimento bancário (baixa do desconto)", parte=parte))
            for t in sel:
                t.saldo = 0.0
        else:
            restante = abs(mov.valor)
            for t in sel:
                usado = min(t.saldo, restante)
                t.saldo = round(t.saldo - usado, 2)
                restante = round(restante - usado, 2)
        return out

    def _conta_diferenca(self, pagando: bool, dif: float):
        if not dif:
            return None, ""
        p = self.perfil
        if pagando:
            return (p.conta_juros_pagos or None, "juros_pagos") if dif > 0 else (p.conta_desconto_obtido or None, "desconto_obtido")
        return (p.conta_juros_recebidos or None, "juros_recebidos") if dif > 0 else (p.conta_desconto_concedido or None, "desconto_concedido")

    def _titulo_unico_por_valor(self, mov: Movimento) -> Titulo | None:
        tipo = "cliente" if mov.entrada else "fornecedor"
        v = round(abs(mov.valor), 2)
        cands = [t for t in self.titulos if t.aberto and t.tipo == tipo and abs(t.saldo - v) < 0.01]
        contas = {t.conta for t in cands}
        if len(contas) == 1 and len(cands) >= 1:
            return cands[0]
        return None

    # ------------------------------------------------------------------ financeiro
    def _casar_financeiro(self, mov: Movimento) -> list[ItemFinanceiro]:
        if not self.financeiro:
            return []
        v = round(abs(mov.valor), 2)
        tipo = "receber" if mov.entrada else "pagar"
        livres = [i for i in self.financeiro if not i.usado and (not i.tipo or i.tipo == tipo) and i.data]
        for dias in (0, 1, 3):
            perto = [i for i in livres if abs((i.data - mov.data).days) <= dias]
            for i in perto:
                if abs(i.valor - v) < 0.01:
                    i.usado = True
                    return [i]
            if dias == 0 and 2 <= len(perto) <= 16:
                for n in range(2, min(6, len(perto)) + 1):
                    for comb in itertools.combinations(perto, n):
                        if abs(sum(i.valor for i in comb) - v) < 0.01:
                            for i in comb:
                                i.usado = True
                            return list(comb)
        return []

    def _lancar_financeiro(self, mov: Movimento, itens: list[ItemFinanceiro]) -> list[Lancamento]:
        out: list[Lancamento] = []
        for it in itens:
            sub = Movimento(id=mov.id, data=mov.data, descricao=f"{mov.descricao} | {it.nome} {it.descricao}",
                            valor=it.valor if mov.entrada else -it.valor, conta_banco=mov.conta_banco, cnpj=mov.cnpj)
            conta, score = self.identificar_parte(sub, it.nome) if it.nome else (None, 0)
            nfs = [it.nf] if it.nf else []
            if conta:
                r = self._lancar_parte(sub, conta, max(score, 85), "Relatório financeiro", nfs=nfs, nome_parte=conta.nome)
                if r:
                    for l in r:
                        l.descricao_extrato = mov.descricao
                    out.extend(r)
                    continue
            # parte não cadastrada: tenta regra/semelhança pela descrição do financeiro
            texto = normalizar(f"{it.descricao} {it.nome}")
            regra = self._regra(sub, texto)
            nome = it.nome or descricao_curta(mov.descricao)
            if regra:
                r, (conta_r, conf) = regra
                hist = formatar(r.historico, parte=nome, descricao=it.descricao or nome, nf=it.nf, mes=_mes(mov))
                out.append(self._base(sub, conta_contra=conta_r, historico=hist, nf=it.nf, parte=nome,
                                      status="ok" if conta_r else "pendente", origem=f"Relatório financeiro + regra {r.nome}",
                                      confianca=conf if conta_r else 40, descricao_extrato=mov.descricao))
                continue
            chave = "pagamento_fornecedor_nf" if not mov.entrada else "recebimento_cliente_nf"
            if not it.nf:
                chave = chave[:-3]
            out.append(self._base(sub, historico=self._modelo(chave, nf=it.nf, parte=nome), nf=it.nf, parte=nome,
                                  status="pendente", origem="Relatório financeiro", confianca=30, descricao_extrato=mov.descricao,
                                  observacao=f"'{it.nome}' não encontrado no razão; escolha a conta ({it.descricao})".strip()))
        return out

    # ------------------------------------------------------------------ regras / semelhança
    def _regra(self, mov: Movimento, texto: str):
        for r in self.regras:
            if r.casa(texto, mov.entrada):
                conta, conf = self.contas_regra.get(r.nome, ("", 0))
                return r, (conta, conf)
        return None

    def _por_semelhanca(self, mov: Movimento):
        alvo = " ".join(tokens(mov.descricao))
        if not alvo or not self._indice_historico:
            return None
        pontos: dict[str, float] = defaultdict(float)
        exemplo: dict[str, str] = {}
        melhor_por_conta: dict[str, float] = defaultdict(float)
        for txt, conta, entrada, hist in self._indice_historico:
            if entrada != mov.entrada:
                continue
            s = fuzz.token_set_ratio(alvo, txt)
            if s >= 75:
                pontos[conta] += s
                if s > melhor_por_conta[conta]:
                    melhor_por_conta[conta] = s
                    exemplo[conta] = hist
        if not pontos:
            return None
        conta = max(pontos, key=lambda c: (melhor_por_conta[c], pontos[c]))
        conf = int(min(90, melhor_por_conta[conta] - 10 + min(10, pontos[conta] / 100)))
        return conta, conf, exemplo[conta]

    def _aprender_de_razao(self, mov: Movimento, m: MovRazao) -> None:
        """Lançamentos que já estão no razão ensinam o texto do extrato -> conta."""
        c = self.contas.get(m.contrapartida)
        if c and c.tipo in ("cliente", "fornecedor"):
            nome = nome_limpo(mov.descricao)
            if nome:
                self.perfil.apelidos.setdefault(nome, c.codigo)
            if mov.cnpj:
                self.perfil.cnpj_conta.setdefault(mov.cnpj, c.codigo)
        else:
            chave = chave_memoria(mov.descricao)
            if chave and chave not in self.perfil.memoria and m.contrapartida:
                self.perfil.memoria[chave] = {"conta": m.contrapartida, "historico": m.historico}

    def _generico(self, mov: Movimento, descricao: str = "") -> str:
        chave = "generico_entrada" if mov.entrada else "generico_saida"
        return self._modelo(chave, descricao=descricao or descricao_curta(mov.descricao), parte="", nf="")


# ---------------------------------------------------------------------------------- formatação
def formatar(modelo: str, **kw) -> str:
    if not modelo:
        return ""
    vals = {k: (v or "") for k, v in kw.items()}
    for k in ("nf", "parte", "descricao", "banco", "mes"):
        vals.setdefault(k, "")
    try:
        txt = modelo.format(**vals)
    except (KeyError, IndexError, ValueError):
        txt = modelo
    txt = re.sub(r"\s+-\s*$", "", txt)
    txt = re.sub(r"\s{2,}", " ", txt).strip(" -")
    return txt.upper()


def descricao_curta(texto: str) -> str:
    t = normalizar(texto)
    t = re.sub(r"\b\d{2}/\d{2}(/\d{2,4})?\b", " ", t)
    t = re.sub(r"\b\d{2}:\d{2}\b", " ", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" -")[:80]


def _mes(mov: Movimento) -> str:
    return f"{MESES[mov.data.month - 1]}/{mov.data.year}"


def nf_texto(nfs: list[str]) -> str:
    return ", ".join(nf_chave(n) for n in nfs if nf_chave(n))
