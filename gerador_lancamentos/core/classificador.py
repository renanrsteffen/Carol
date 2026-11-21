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

from .busca import Candidato, buscar, melhor_entre_contas
from .modelos import Conta, ItemFinanceiro, Lancamento, Movimento, MovRazao, Titulo
from .perfil import MODELOS_PADRAO, RUIDO_BANCARIO, Perfil, Regra, chave_memoria
from .razao import Razao
from .util import extrair_nfs, nf_chave, normalizar, tokens

ROTULO_BUSCA = {
    "nf": "NF citada", "exato": "valor da nota", "soma": "soma de notas", "retencao": "nota com retenção",
    "parcela": "parcela", "juros": "nota + juros", "desconto": "nota com desconto", "parcial": "pagamento parcial",
    "peps": "notas mais antigas",
}

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

    def titulos_por_conta(self, tipo: str) -> dict[str, list[Titulo]]:
        d: dict[str, list[Titulo]] = defaultdict(list)
        for t in self.titulos:
            if t.aberto and t.tipo == tipo:
                d[t.conta].append(t)
        return d

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

        # 7) procura o valor em todos os clientes/fornecedores (nome não identificado)
        cand, outros = self._buscar_em_todos(mov)
        if cand:
            c = self.contas.get(cand.conta)
            r = self._lancar_parte(mov, c, 75, "Busca nas notas em aberto", candidato=cand)
            if r:
                r[0].status = "revisar"
                r[0].observacao = ("Nome não aparece no extrato. " + r[0].observacao
                                   + (" | Também possível: " + "; ".join(f"{self.nome_conta(o.conta)}: {o.explicacao}" for o in outros) if outros else ""))
                return r
        dica = ("Possíveis: " + "; ".join(f"{self.nome_conta(o.conta)} — {o.explicacao}" for o in outros)) if outros else ""

        if parte:
            r = self._lancar_parte(mov, parte, score, "Cliente/fornecedor identificado")
            if r:
                return r
        return [self._base(mov, historico=self._generico(mov), status="pendente", origem="Não identificado", confianca=0,
                           observacao=dica or "Escolha a conta de contrapartida")]

    # ------------------------------------------------------------------ partes / NF
    def _lancar_parte(self, mov: Movimento, conta: Conta, score: int, origem: str, nfs: list[str] | None = None,
                      nome_parte: str = "", candidato: Candidato | None = None) -> list[Lancamento] | None:
        if conta is None:
            return None
        parte = nome_parte or conta.nome
        nfs = nfs or extrair_nfs(mov.descricao)
        pagando = not mov.entrada
        if candidato is None:
            cands = buscar(self.titulos_abertos(conta.codigo), mov.valor, mov.data, nfs, self.perfil.tolerancia_juros_pct)
            candidato = cands[0] if cands else None
            alternativas = cands[1:3]
        else:
            alternativas = []
        chave = "pagamento_fornecedor" if pagando else "recebimento_cliente"

        if candidato is None:
            nf_txt = ", ".join(nfs) if nfs else ""
            hist = self._modelo(chave + ("_nf" if nf_txt else ""), nf=nf_txt, parte=parte)
            abertos = self.titulos_abertos(conta.codigo)
            obs = ("Nenhuma nota em aberto no razão para " + parte if not abertos else
                   f"Nenhuma combinação das {len(abertos)} notas em aberto explica o valor: "
                   + ", ".join(f"NF {t.nf or 's/n'} {t.saldo:.2f}" for t in abertos[:5]))
            return [self._base(mov, conta_contra=conta.codigo, historico=hist, nf=nf_txt, parte=parte,
                               status="revisar", origem=origem, confianca=min(score, 70), observacao=obs)]

        c = candidato
        nf_hist = c.nf_texto
        if c.tipo == "parcela" and nf_hist:
            nf_hist = f"{nf_hist} PARC. {c.parcela}"
        elif c.tipo == "parcial" and nf_hist:
            nf_hist = f"{nf_hist} (PARCIAL)"
        hist = self._modelo(chave + ("_nf" if nf_hist else ""), nf=nf_hist, parte=parte)
        conf = min(score, c.pontos)
        status = "ok" if conf >= 85 and c.tipo in ("nf", "exato", "soma") else "revisar"
        obs = c.explicacao
        if alternativas:
            obs += " | Outras hipóteses: " + "; ".join(a.explicacao for a in alternativas)
        valor = round(abs(mov.valor), 2)
        out: list[Lancamento] = []
        conta_juros, chave_juros = self._conta_diferenca(pagando, 1)
        conta_desc, chave_desc = self._conta_diferenca(pagando, -1)

        # linha principal (banco x cliente/fornecedor)
        principal = valor
        if c.juros > 0 and conta_juros:
            principal = round(valor - c.juros, 2)
        out.append(self._base(mov, valor=principal, conta_contra=conta.codigo, historico=hist, nf=c.nf_texto,
                              parte=parte, status=status, origem=f"{origem} · {ROTULO_BUSCA.get(c.tipo, c.tipo)}",
                              confianca=conf, observacao=obs))
        if c.juros > 0:
            if conta_juros:
                out.append(self._base(mov, valor=c.juros, conta_contra=conta_juros,
                                      historico=self._modelo(chave_juros, nf=c.nf_texto, parte=parte), nf=c.nf_texto,
                                      parte=parte, status="revisar", origem=origem + " · juros", confianca=70,
                                      observacao=f"{c.juros:.2f} acima do saldo da nota"))
            else:
                out[0].observacao += " | Configure a conta de juros para separar a diferença."
        # lançamentos sem banco: desconto e retenções
        if c.desconto > 0:
            if conta_desc:
                out.append(self._sem_banco(mov, conta.codigo, conta_desc, c.desconto, pagando,
                                           self._modelo(chave_desc, nf=c.nf_texto, parte=parte), c, parte, "desconto"))
            else:
                out[0].observacao += " | Configure a conta de descontos para baixar a diferença."
        for nome, v in c.retencoes:
            conta_ret = self._conta_retencao(nome, pagando)
            h = formatar(self.perfil.modelos.get("retencao", MODELOS_PADRAO["retencao"]), imposto=nome.split()[0],
                         nf=c.nf_texto, parte=parte)
            if conta_ret:
                out.append(self._sem_banco(mov, conta.codigo, conta_ret, v, pagando, h, c, parte, "retenção"))
            else:
                out[0].observacao += f" | {nome}: {v:.2f} (configure a conta de {nome.split()[0]} retido)"
        c.aplicar()
        return out

    def _sem_banco(self, mov, conta_parte, conta_contra, valor, pagando, hist, c, parte, rotulo) -> Lancamento:
        """Fornecedor: D fornecedor / C conta (desconto obtido, imposto a recolher).
        Cliente: D conta (desconto concedido, imposto a recuperar) / C cliente."""
        return Lancamento(
            mov_id=mov.id, data=mov.data, conta_banco=conta_parte, valor=round(valor, 2),
            natureza="entrada" if pagando else "saida", conta_contra=conta_contra, historico=hist,
            nf=c.nf_texto, descricao_extrato=mov.descricao, confianca=75, origem=f"Baixa de {rotulo}",
            status="revisar", observacao=f"Lançamento sem movimento bancário ({rotulo})", parte=parte)

    def _conta_retencao(self, nome: str, pagando: bool) -> str:
        chave = nome.split()[0].upper()
        cfg = self.perfil.contas_retencao.get(chave) or {}
        return cfg.get("fornecedor" if pagando else "cliente", "")

    def _conta_diferenca(self, pagando: bool, dif: float):
        if not dif:
            return None, ""
        p = self.perfil
        if pagando:
            return (p.conta_juros_pagos or None, "juros_pagos") if dif > 0 else (p.conta_desconto_obtido or None, "desconto_obtido")
        return (p.conta_juros_recebidos or None, "juros_recebidos") if dif > 0 else (p.conta_desconto_concedido or None, "desconto_concedido")

    def _buscar_em_todos(self, mov: Movimento):
        tipo = "cliente" if mov.entrada else "fornecedor"
        return melhor_entre_contas(self.titulos_por_conta(tipo), mov.valor, mov.data, extrair_nfs(mov.descricao),
                                   self.perfil.tolerancia_juros_pct)

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
    for k in ("nf", "parte", "descricao", "banco", "mes", "imposto"):
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
