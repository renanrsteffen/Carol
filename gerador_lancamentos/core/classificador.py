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
from .tributos import NOMES, aprender_codigos, conta_banco_do_comprovante, conta_por_codigo, identificar, sugerir_contas
from .modelos import Conta, ItemFinanceiro, Lancamento, Movimento, MovRazao, Titulo
from .perfil import MODELOS_PADRAO, RUIDO_BANCARIO, Perfil, Regra, chave_memoria
from .razao import Razao
from .util import fmt_valor as br
from .util import extrair_nfs, nf_chave, normalizar, tokens

MOTIVO_BUSCA = {
    "nf": "Valor ≠ nota citada", "exato": "Confirme a nota", "soma": "Confirme as notas",
    "retencao": "Nota com impostos retidos", "parcela": "Parcela de nota", "juros": "Pago com juros/multa",
    "desconto": "Pago com desconto", "parcial": "Pagamento parcial", "peps": "Notas mais antigas",
}

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
    # decisões do operador por movimento: {conta, nfs, historico, aceito, ignorar}
    forcados: dict = field(default_factory=dict)
    comprovantes: list = field(default_factory=list)   # comprovantes de pagamento do e-CAC


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
        self.comprovantes = list(ctx.comprovantes or [])
        sugeridas = sugerir_contas(self.contas)
        self.contas_tributo = {**sugeridas, **{k: v for k, v in self.perfil.contas_tributo.items() if v}}
        self.contas_tributo_sugeridas = sugeridas
        self.codigos_aprendidos = aprender_codigos(self.comprovantes, self.razao)
        self._banco_id: dict[str, str] = {v: k for k, v in self.perfil.bancos_comprovante.items() if "|" not in k}
        self.conta_clientes_generica = self.perfil.conta_clientes_generica or self._achar_clientes_generica()
        self.dominante = self._contas_dominantes()
        self.depara = {**self._depara_automatico(), **{k: v for k, v in self.perfil.depara_cliente.items() if v}}

    def _depara_automatico(self) -> dict[str, str]:
        """Conta do plano do cliente -> conta Steffen, quando as regras apontam a mesma conta para quase todos
        os itens daquele código (ex.: 390 'tarifa' -> despesas bancárias)."""
        votos: dict[str, Counter] = defaultdict(Counter)
        total: Counter = Counter()
        for it in self.financeiro:
            if not it.conta_cliente:
                continue
            total[it.conta_cliente] += 1
            texto = normalizar(f"{it.descricao} {it.nome}")
            entrada = it.tipo == "receber"
            for r in self.regras:
                if not r.prioritaria and r.casa(texto, entrada):
                    conta = self.contas_regra.get(r.nome, ("", 0))[0]
                    if conta:
                        votos[it.conta_cliente][conta] += 1
                    break
        out = {}
        for cod, v in votos.items():
            conta, n = v.most_common(1)[0]
            if n >= 2 and n / total[cod] >= 0.8:
                out[cod] = conta
        self.depara_sugerido = out
        return out

    def _contas_dominantes(self) -> dict[tuple[str, bool], tuple[str, str, float]]:
        """(banco, entrada) -> (conta, histórico, fração) quando a maioria dos lançamentos do banco vai para uma conta."""
        cont: dict[tuple[str, bool], Counter] = defaultdict(Counter)
        hist: dict[tuple[str, bool, str], Counter] = defaultdict(Counter)
        for m in self.razao.movimentos_banco(self._bancos()):
            k = (m.conta, m.debito > 0)
            cont[k][m.contrapartida] += 1
            hist[(m.conta, m.debito > 0, m.contrapartida)][m.historico] += 1
        out = {}
        for k, c in cont.items():
            total = sum(c.values())
            conta, n = c.most_common(1)[0]
            if total >= 20 and n / total >= 0.75 and conta not in self._bancos():
                h = hist[(k[0], k[1], conta)].most_common(1)[0][0]
                out[k] = (conta, h, n / total)
        return out

    def _achar_clientes_generica(self) -> str:
        """Conta de clientes usada para todos os compradores (muitas notas de nomes diferentes)."""
        cont = Counter(t.conta for t in self.titulos if t.tipo == "cliente")
        for cod, n in cont.most_common(1):
            c = self.contas.get(cod)
            if c and n >= 30 and re.search(r"CLIENTES|DUPLICATAS|CONTAS A RECEBER", normalizar(c.nome)):
                return cod
        return ""

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
                    s -= 12   # recebimento casando com fornecedor (ou pagamento com cliente) só com nome quase idêntico
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
        movimentos = list(movimentos) + self._preparar_comprovantes(movimentos)
        self._transferencias = self._achar_transferencias(movimentos)
        for mov in sorted(movimentos, key=lambda m: (m.data, m.valor)):
            f = self.ctx.forcados.get(mov.id)
            ls = self._forcado(mov, f) if f and (f.get("conta") or f.get("ignorar")) else self._classificar_um(mov, usados_razao)
            if f:
                if f.get("historico") and ls:
                    ls[0].historico = f["historico"].upper()
                if f.get("nf_texto") is not None and ls:
                    ls[0].nf = f["nf_texto"]
                if f.get("aceito") and not f.get("ignorar"):
                    for l in ls:
                        if l.conta_contra and l.status in ("revisar", "pendente"):
                            l.status = "ok"
                    if ls:
                        ls[0].motivo = ""
            for i, l in enumerate(ls):
                l.principal = i == 0
                if l.status == "pendente" and not l.motivo:
                    l.motivo = "Conta não identificada"
            if ls:
                pior = min(ls, key=lambda l: {"pendente": 0, "revisar": 1}.get(l.status, 9))
                if pior is not ls[0] and pior.status in ("pendente", "revisar") and ls[0].status not in ("pendente",):
                    ls[0].motivo = ls[0].motivo or (pior.motivo + f" ({pior.valor:.2f})".replace(".", ","))
                    if pior.observacao:
                        ls[0].observacao = (ls[0].observacao + " | " + pior.observacao).strip(" |")
            saida.extend(ls)
        return saida

    def _achar_transferencias(self, movimentos: list[Movimento]) -> dict[str, tuple[Movimento, bool]]:
        """Saída num banco e entrada de mesmo valor em outro banco do cliente (mesmo dia ou dia seguinte).
        A saída gera o lançamento; a entrada fica marcada para não duplicar."""
        RE_TRANSF = re.compile(r"TRANSF|TED|DOC|PIX|ENTRE CONTAS|MESMA TITULARIDADE|APLIC|RESG|DEPOSITO|SAQUE")
        cnpj = re.sub(r"\D", "", self.perfil.cnpj or "")
        entradas = defaultdict(list)
        for m in movimentos:
            if m.valor > 0 and m.conta_banco:
                entradas[round(m.valor, 2)].append(m)
        out: dict[str, tuple[Movimento, bool]] = {}
        usados: set[str] = set()
        for m in sorted((x for x in movimentos if x.valor < 0 and x.conta_banco), key=lambda x: x.data):
            cands = [e for e in entradas.get(round(-m.valor, 2), []) if e.id not in usados and e.conta_banco != m.conta_banco
                     and 0 <= (e.data - m.data).days <= 1]
            if not cands:
                continue
            texto = normalizar(m.descricao) + " " + normalizar(cands[0].descricao)
            mesmo_titular = bool(cnpj and (cnpj[:8] in re.sub(r"\D", "", m.descricao + cands[0].descricao)))
            if not (RE_TRANSF.search(texto) or mesmo_titular) and len(cands) > 1:
                continue
            e = min(cands, key=lambda x: (x.data - m.data).days)
            usados.add(e.id)
            out[m.id] = (e, True)
            out[e.id] = (m, False)
        return out

    # ------------------------------------------------------------------ comprovantes e-CAC
    def _preparar_comprovantes(self, movimentos: list[Movimento]) -> list[Movimento]:
        """Casa os comprovantes com as saídas do extrato (valor e data; vários DARFs num débito)
        e cria um movimento próprio para os comprovantes sem linha no extrato."""
        self._comp_por_mov: dict[str, list] = {}
        self._sem_extrato: dict[str, str] = {}
        self._conf_banco: dict[str, int] = {}
        if not self.comprovantes:
            return []
        bancos_extrato = {m.conta_banco for m in movimentos}
        if movimentos:
            # só os pagamentos do período dos extratos (a relação do e-CAC costuma cobrir o ano todo)
            ini, fim = min(m.data for m in movimentos), max(m.data for m in movimentos)
            self.comprovantes = [c for c in self.comprovantes
                                 if c.data_arrecadacao is None or ini <= c.data_arrecadacao <= fim]
        for c in self.comprovantes:
            conta, conf = conta_banco_do_comprovante(c.banco, c.agencia, self.perfil, self.contas, bancos_extrato)
            c._conta_banco, c._conf_banco = conta, conf
        livres = [c for c in self.comprovantes if c.data_arrecadacao]
        saidas = sorted((m for m in movimentos if m.valor < 0), key=lambda m: m.data)

        def compat(c, m, dias):
            return abs((c.data_arrecadacao - m.data).days) <= dias and (not c._conta_banco or c._conf_banco < 90
                                                                         or c._conta_banco == m.conta_banco)
        for dias in (0, 2):
            for m in saidas:
                if m.id in self._comp_por_mov:
                    continue
                v = round(-m.valor, 2)
                cands = [c for c in livres if compat(c, m, dias)]
                um = [c for c in cands if abs(c.total - v) < 0.011]
                if um:
                    um.sort(key=lambda c: abs((c.data_arrecadacao - m.data).days))
                    self._comp_por_mov[m.id] = [um[0]]
                    livres.remove(um[0])
                    continue
                if 2 <= len(cands) <= 16:
                    achou = None
                    for n in range(2, min(6, len(cands)) + 1):
                        for comb in itertools.combinations(cands, n):
                            if abs(sum(c.total for c in comb) - v) < 0.011:
                                achou = list(comb)
                                break
                        if achou:
                            break
                    if achou:
                        self._comp_por_mov[m.id] = achou
                        for c in achou:
                            livres.remove(c)
        extras: list[Movimento] = []
        for c in self.comprovantes:
            if any(c in cs for cs in self._comp_por_mov.values()):
                continue
            d = c.data_arrecadacao or c.vencimento or c.periodo_apuracao
            if d is None:
                continue
            mov = Movimento(id=c.id, data=d, descricao=f"{c.tipo} {c.numero} - pago no {c.banco_nome or 'banco não informado'} (e-CAC)",
                            valor=-round(c.total, 2), conta_banco=c._conta_banco)
            self._comp_por_mov[mov.id] = [c]
            self._sem_extrato[mov.id] = ("Não encontrado no extrato" if c._conta_banco and c._conta_banco in bancos_extrato
                                         else "Sem extrato para conferir")
            extras.append(mov)
        return extras

    def _lancar_comprovantes(self, mov: Movimento, comps: list) -> list[Lancamento]:
        out: list[Lancamento] = []
        sev = {"pendente": 0, "revisar": 1, "ok": 2}
        alerta = ("ok", "", "")   # (status, motivo, observação) mais grave do movimento
        todos_obs: list[str] = []

        def piora(status, motivo, obs=""):
            nonlocal alerta
            texto = f"{motivo}: {obs}" if motivo and obs else (motivo or obs)
            if texto and texto not in todos_obs:
                todos_obs.append(texto)
            if sev[status] < sev[alerta[0]] or (sev[status] == sev[alerta[0]] and not alerta[1]):
                alerta = (status, motivo, obs)

        for c in comps:
            c._mj_incluido = 0.0
            pa, doc = c.pa_texto, c.numero
            nomes = []
            linhas_c: list[tuple[str, str, float, str]] = []   # (tributo, conta, valor, historico)
            if c.tipo == "DAS":
                linhas_c.append(("SIMPLES", self.contas_tributo.get("SIMPLES", ""), c.principal,
                                 self._modelo("imposto_pago", tributo=NOMES["SIMPLES"], pa=pa, doc=doc)))
                nomes.append("SIMPLES NACIONAL")
            else:
                for it in c.itens:
                    conta_ap, modo, certeza = conta_por_codigo(self.codigos_aprendidos, it.codigo, it.principal)
                    conta_ap = self.perfil.contas_codigo.get(it.codigo) or conta_ap
                    if conta_ap:
                        nome = descricao_curta(it.denominacao)[:45]
                        if nome.startswith("RECEITA ") and conta_ap in self.contas:
                            nome = f"{self.nome_conta(conta_ap).upper()} (COD. {it.codigo})"
                        nomes.append(nome)
                        if modo == "total":
                            valor_l = it.total
                            multa_juros_ja = True
                        else:
                            valor_l = it.principal
                            multa_juros_ja = False
                        hist = self._modelo("imposto_pago", tributo=nome, pa=pa, doc=doc)
                        linhas_c.append((f"COD{it.codigo}", conta_ap, valor_l, hist))
                        if multa_juros_ja:
                            c._mj_incluido = getattr(c, "_mj_incluido", 0.0) + it.multa + it.juros
                        if not certeza and not self.perfil.contas_codigo.get(it.codigo):
                            piora("revisar", "Confirme a conta do código " + it.codigo,
                                  f"O código {it.codigo} já foi lançado em mais de uma conta; usada a do valor mais parecido")
                        continue
                    trib, recorrente = identificar(it.codigo, it.denominacao, c.tipo)
                    nome = NOMES.get(trib) if trib != "OUTROS" else (descricao_curta(it.denominacao)[:45] or "TAXA/IMPOSTO")
                    nomes.append(nome)
                    if not it.principal:
                        continue
                    if recorrente:
                        conta = self.contas_tributo.get(trib, "")
                        hist = self._modelo("imposto_pago", tributo=nome, pa=pa, doc=doc)
                        if not conta:
                            piora("pendente", f"Conta de {NOMES.get(trib, trib)} a recolher não definida",
                                  "Informe a conta em Configurações › Impostos")
                        else:
                            prov = self._provisao(conta, c.periodo_apuracao)
                            if prov is not None and abs(prov - it.principal) > 0.01:
                                piora("revisar", "Valor ≠ provisão" if prov else "Sem provisão no razão",
                                      f"{nome} PA {pa}: pago {br(it.principal)}, provisionado {br(prov)}")
                    else:
                        conta = self.contas_tributo.get("OUTROS", "")
                        hist = self._modelo("imposto_esporadico", tributo=nome, pa=pa, doc=doc)
                        piora("revisar" if conta else "pendente", "Taxa/imposto esporádico → despesa",
                              f"Código {it.codigo or 's/c'} {it.denominacao[:60]}")
                    linhas_c.append((trib, conta, it.principal, hist))
            tribs = ", ".join(dict.fromkeys(nomes))[:60]
            ja_incluido = round(getattr(c, "_mj_incluido", 0.0), 2)
            if ja_incluido and ja_incluido >= round(c.multa + c.juros, 2):
                c_multa = c_juros = 0.0   # parcelamentos: o escritório lança o total na conta do parcelamento
            else:
                c_multa, c_juros = c.multa, c.juros
            if c_multa:
                linhas_c.append(("MULTA", self.contas_tributo.get("MULTA", ""), c_multa,
                                 self._modelo("multa_tributo", tributo=tribs, pa=pa, doc=doc)))
                if not self.contas_tributo.get("MULTA"):
                    piora("pendente", "Conta de multas não definida", "Informe a conta em Configurações › Impostos")
            if c_juros:
                linhas_c.append(("JUROS", self.contas_tributo.get("JUROS", ""), c_juros,
                                 self._modelo("juros_tributo", tributo=tribs, pa=pa, doc=doc)))
                if not self.contas_tributo.get("JUROS"):
                    piora("pendente", "Conta de juros não definida", "Informe a conta em Configurações › Impostos")
            if self.perfil.cnpj and c.cnpj and re.sub(r"\D", "", self.perfil.cnpj)[:8] != c.cnpj[:8]:
                piora("pendente", "CNPJ do comprovante é de outra empresa", f"CNPJ {c.cnpj} {c.razao_social}")
            for a in c.avisos:
                piora("revisar", "Comprovante com leitura incompleta", a)
            for trib, conta, valor, hist in linhas_c:
                st_l = "ok" if conta else "pendente"
                out.append(self._base(mov, valor=round(valor, 2), conta_contra=conta, historico=hist, nf=doc,
                                      status=st_l, origem=f"Comprovante e-CAC ({c.tipo})", confianca=95 if conta else 0,
                                      parte=trib, motivo="" if conta else "Conta não definida"))
        if mov.id in self._sem_extrato:
            piora("revisar", self._sem_extrato[mov.id], "Comprovante sem débito correspondente no extrato")
        conf_banco = min((getattr(c, "_conf_banco", 100) for c in comps), default=100)
        if not mov.conta_banco:
            piora("pendente", "Banco do comprovante sem conta",
                  f"Informe em Configurações › Impostos a conta do banco {comps[0].banco} ag. {comps[0].agencia}")
        elif conf_banco < 90 and mov.id in self._sem_extrato:
            piora("revisar", "Confirme o banco do comprovante", f"Banco {comps[0].banco_nome} → {self.nome_conta(mov.conta_banco)}")
        if len(comps) > 1:
            piora("ok", "", f"{len(comps)} comprovantes pagos no mesmo débito")
        if out:
            status, motivo, obs = alerta
            if sev.get(out[0].status, 2) > sev[status]:
                out[0].status = status
            out[0].motivo = motivo or out[0].motivo
            principal_obs = obs or (todos_obs[0] if todos_obs else "")
            outros = [o for o in todos_obs if o != principal_obs and not o.startswith(motivo + ": " + obs)]
            out[0].observacao = " | ".join(x for x in [principal_obs] + outros + [
                f"{c.tipo} {c.numero} (banco {c.banco or '?'} ag. {c.agencia or '?'}): principal {br(c.principal)}, multa {br(c.multa)}, juros {br(c.juros)}"
                for c in comps] if x)
            out[0].confianca = 95 if status == "ok" else (70 if status == "revisar" else 30)
        return out

    def _provisao(self, conta: str, pa) -> float | None:
        """Créditos lançados na conta do tributo no mês do período de apuração (None se o razão não cobre o mês)."""
        if pa is None:
            return None
        mes = (pa.year, pa.month)
        if not any((m.data.year, m.data.month) == mes for m in self.razao.movimentos):
            return None
        return round(sum(m.credito for m in self.razao.movimentos if m.conta == conta and (m.data.year, m.data.month) == mes), 2)

    def _forcado(self, mov: Movimento, f: dict) -> list[Lancamento]:
        """Aplica a decisão do operador (conta escolhida, notas escolhidas ou ignorar)."""
        if f.get("ignorar"):
            return [self._base(mov, conta_contra=f.get("conta", ""), historico=self._generico(mov), status="ignorar",
                               origem="Ignorado pelo operador", confianca=100)]
        conta = self.contas.get(f["conta"])
        if conta and conta.tipo in ("cliente", "fornecedor"):
            r = self._lancar_parte(mov, conta, 100, "Escolha do operador", nfs=f.get("nfs") or None)
            if r:
                r[0].status = "ok"
                r[0].alternativas = []
                return r
        hist = f.get("historico") or self._hist_regra_para_conta(mov, f["conta"]) or self._generico(mov, self.nome_conta(f["conta"]))
        return [self._base(mov, conta_contra=f["conta"], historico=hist, status="ok", origem="Escolha do operador", confianca=100)]

    def _hist_regra_para_conta(self, mov: Movimento, conta: str) -> str:
        for r in self.regras:
            if self.contas_regra.get(r.nome, ("", 0))[0] == conta and r.historico:
                return formatar(r.historico, parte=nome_limpo(mov.descricao), descricao=descricao_curta(mov.descricao), nf="", mes=_mes(mov))
        return ""

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
                return [self._base(mov, conta_contra=m.contrapartida, historico=m.historico, status="ja_lancado", motivo="Já consta no razão",
                                   origem="Já consta no razão", confianca=100,
                                   observacao=f"Lote {m.lote} lanç. {m.lancamento}".strip())]

        # 1a) transferência entre contas próprias (aparece nos dois extratos)
        if mov.id in self._transferencias:
            outro, lanca = self._transferencias[mov.id]
            hist = self._modelo("transferencia", banco=self.nome_conta(outro.conta_banco))
            if lanca:
                return [self._base(mov, conta_contra=outro.conta_banco, historico=hist, status="ok",
                                   origem="Transferência entre contas", confianca=97,
                                   observacao=f"Par no extrato de {self.nome_conta(outro.conta_banco)} em {outro.data:%d/%m}")]
            return [self._base(mov, conta_contra=outro.conta_banco, historico=hist, status="ja_lancado",
                               origem="Transferência entre contas", confianca=100, motivo="Lançada pela outra conta",
                               observacao=f"Lançada no extrato de {self.nome_conta(outro.conta_banco)} (evita duplicidade)")]

        # 1b) comprovante de pagamento do e-CAC
        if mov.id in self._comp_por_mov:
            return self._lancar_comprovantes(mov, self._comp_por_mov[mov.id])

        # 2) relatório financeiro do cliente (detalhe por movimento)
        itens = self._casar_financeiro(mov)
        if itens:
            r = self._lancar_financeiro(mov, itens)
            if r:
                return r

        # 2b) regras prioritárias (ex.: retiradas de sócios)
        rp = self._regra_prioritaria(mov, texto)
        if rp:
            return [rp]

        # 3) memória
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

        # 4) cliente/fornecedor pelo nome/CNPJ
        parte, score = self.identificar_parte(mov)
        regra = self._regra(mov, texto)
        if parte and (score >= 88 or not regra):
            r = self._lancar_parte(mov, parte, score, "Cliente/fornecedor identificado")
            if r:
                if score < 85:
                    r[0].motivo = r[0].motivo or "Confirme o cliente/fornecedor"
                if regra and regra[1][0]:
                    r[0].alternativas.append({"conta": regra[1][0], "nfs": [], "texto": f"Regra '{regra[0].nome}'"})
                return r

        # 5) regras de categoria
        if regra:
            r, (conta, conf) = regra
            hist = formatar(r.historico, parte=nome_limpo(mov.descricao) or kw_desc, descricao=kw_desc, nf="", mes=_mes(mov)) or self._generico(mov)
            alts = [{"conta": parte.codigo, "nfs": [], "texto": f"{parte.nome} (nome parecido no extrato)"}] if parte else []
            return [self._base(mov, conta_contra=conta, historico=hist, status="ok" if conta and conf >= 80 else ("revisar" if conta else "pendente"),
                               motivo="" if conta and conf >= 80 else ("Regra pouco usada no razão" if conta else "Regra sem conta definida"),
                               alternativas=alts,
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
                               motivo="Semelhança com o razão" if conf < 85 else "",
                               confianca=conf, observacao=f"Parecido com: {hist_razao[:60]}")]

        # 7a) padrão forte do banco (>= 90%) vem antes de adivinhar pelo valor
        dom = self.dominante.get((mov.conta_banco, mov.entrada))
        if dom and dom[2] >= 0.9:
            conta, h, frac = dom
            return [self._base(mov, conta_contra=conta, historico=h.upper(), status="ok",
                               origem="Padrão do banco no razão", confianca=int(frac * 100),
                               observacao=f"{int(frac * 100)}% das {'entradas' if mov.entrada else 'saídas'} deste banco no razão vão para {self.nome_conta(conta)}")]

        # 7) procura o valor em todos os clientes/fornecedores (nome não identificado)
        cand, outros = self._buscar_em_todos(mov)
        if cand:
            c = self.contas.get(cand.conta)
            r = self._lancar_parte(mov, c, 75, "Busca nas notas em aberto", candidato=cand)
            if r:
                r[0].status = "revisar"
                r[0].motivo = "Nome fora do extrato"
                r[0].alternativas = [{"conta": o.conta, "nfs": o.nfs, "texto": f"{self.nome_conta(o.conta)} — {o.explicacao}"} for o in outros]
                r[0].observacao = ("Nome não aparece no extrato. " + r[0].observacao
                                   + (" | Também possível: " + "; ".join(f"{self.nome_conta(o.conta)}: {o.explicacao}" for o in outros) if outros else ""))
                return r
        dica = ("Possíveis: " + "; ".join(f"{self.nome_conta(o.conta)} — {o.explicacao}" for o in outros)) if outros else ""

        # 8) conta dominante do banco (ex.: quase todas as entradas do Mercado Pago vão para "Clientes Mercado Pago")
        dom = self.dominante.get((mov.conta_banco, mov.entrada))
        if dom:
            conta, h, frac = dom
            return [self._base(mov, conta_contra=conta, historico=h.upper(), status="ok" if frac >= 0.9 else "revisar",
                               origem="Padrão do banco no razão", confianca=int(frac * 100),
                               motivo="" if frac >= 0.9 else "Padrão do banco — confirme",
                               observacao=f"{int(frac * 100)}% das {'entradas' if mov.entrada else 'saídas'} deste banco no razão vão para {self.nome_conta(conta)}")]

        if parte:
            r = self._lancar_parte(mov, parte, score, "Cliente/fornecedor identificado")
            if r:
                return r
        alts = [{"conta": o.conta, "nfs": o.nfs, "texto": f"{self.nome_conta(o.conta)} — {o.explicacao}"} for o in outros]
        if parte:
            alts.insert(0, {"conta": parte.codigo, "nfs": [], "texto": f"{parte.nome} (nome parecido)"})
        return [self._base(mov, historico=self._generico(mov), status="pendente", origem="Não identificado", confianca=0,
                           motivo="Conta não identificada", alternativas=alts,
                           observacao=dica or "Escolha a conta de contrapartida")]

    # ------------------------------------------------------------------ partes / NF
    def _lancar_parte(self, mov: Movimento, conta: Conta, score: int, origem: str, nfs: list[str] | None = None,
                      nome_parte: str = "", candidato: Candidato | None = None, filtro_nome: str = "") -> list[Lancamento] | None:
        if conta is None:
            return None
        parte = nome_parte or conta.nome
        nfs = nfs or extrair_nfs(mov.descricao)
        pagando = not mov.entrada
        sem_controle = conta.codigo == self.conta_clientes_generica or (
            self.titulos_abertos(conta.codigo) and all(not t.nf for t in self.titulos_abertos(conta.codigo)))
        if candidato is None and sem_controle and not filtro_nome:
            # conta sem controle por nota (clientes genérica, Mercado Pago...): a conta é o que importa
            chave = "pagamento_fornecedor" if pagando else "recebimento_cliente"
            nf_txt = ", ".join(nfs) if nfs else ""
            return [self._base(mov, conta_contra=conta.codigo, historico=self._modelo(chave + ("_nf" if nf_txt else ""), nf=nf_txt, parte=parte),
                               nf=nf_txt, parte=parte, status="ok" if score >= 85 else "revisar", origem=origem,
                               confianca=min(score, 92), motivo="" if score >= 85 else "Confirme a conta")]
        if candidato is None and ((conta.tipo == "cliente" and pagando) or (conta.tipo == "fornecedor" and not pagando)):
            # devolução/estorno: sem busca de nota
            hist = self._generico(mov, ("DEVOLUCAO/ESTORNO - " + parte))
            return [self._base(mov, conta_contra=conta.codigo, historico=hist, parte=parte,
                               status="ok" if score >= 90 else "revisar", origem=origem, confianca=min(score, 90),
                               motivo="" if score >= 90 else "Devolução/estorno — confirme",
                               observacao="Movimento no sentido contrário ao da conta (devolução, estorno ou reembolso)")]
        if candidato is None:
            abertos = self.titulos_abertos(conta.codigo)
            if filtro_nome:
                alvo = normalizar(filtro_nome)
                abertos = [t for t in abertos if fuzz.token_set_ratio(alvo, normalizar(t.historico)) >= 88]
            cands = buscar(abertos, mov.valor, mov.data, nfs, self.perfil.tolerancia_juros_pct)
            if filtro_nome:  # conta genérica: só aceita hipóteses fortes do mesmo comprador
                cands = [c for c in cands if c.tipo in ("nf", "exato", "soma", "parcela", "juros", "desconto", "retencao")]
            candidato = cands[0] if cands else None
            alternativas = cands[1:3]
        else:
            alternativas = []
        chave = "pagamento_fornecedor" if pagando else "recebimento_cliente"

        if candidato is None:
            nf_txt = ", ".join(nfs) if nfs else ""
            hist = self._modelo(chave + ("_nf" if nf_txt else ""), nf=nf_txt, parte=parte)
            abertos = self.titulos_abertos(conta.codigo)
            if filtro_nome:
                abertos = [t for t in abertos if fuzz.token_set_ratio(normalizar(filtro_nome), normalizar(t.historico)) >= 88]
            obs = ("Nenhuma nota em aberto no razão para " + parte if not abertos else
                   f"Nenhuma combinação das {len(abertos)} notas em aberto explica o valor: "
                   + ", ".join(f"NF {t.nf or 's/n'} {br(t.saldo)}" for t in abertos[:5]))
            if nfs and score >= 90 and conta.tipo == "fornecedor":
                return [self._base(mov, conta_contra=conta.codigo, historico=hist, nf=nf_txt, parte=parte,
                                   status="ok", origem=origem, confianca=min(score, 88),
                                   observacao=f"NF {nf_txt} informada no relatório do cliente; ainda não consta no razão")]
            if filtro_nome or conta.codigo == self.conta_clientes_generica:
                # conta genérica de clientes: a conta é certa; só a nota não foi localizada
                return [self._base(mov, conta_contra=conta.codigo, historico=hist, nf=nf_txt, parte=parte,
                                   status="ok" if score >= 85 else "revisar", origem=origem, confianca=min(score, 88),
                                   observacao="Nota do comprador não localizada no razão (conta de clientes genérica)",
                                   motivo="" if score >= 85 else "Confirme o cliente")]
            return [self._base(mov, conta_contra=conta.codigo, historico=hist, nf=nf_txt, parte=parte,
                               status="revisar", origem=origem, confianca=min(score, 70), observacao=obs,
                               motivo="Nota não encontrada")]

        c = candidato
        nf_hist = c.nf_texto
        if c.tipo == "parcela" and nf_hist:
            nf_hist = f"{nf_hist} PARC. {c.parcela}"
        elif c.tipo == "parcial" and nf_hist:
            nf_hist = f"{nf_hist} (PARCIAL)"
        hist = self._modelo(chave + ("_nf" if nf_hist else ""), nf=nf_hist, parte=parte)
        conf = min(score, c.pontos)
        status = "ok" if conf >= 85 and c.tipo in ("nf", "exato", "soma") else "revisar"
        if filtro_nome and score >= 85:
            status = "ok"   # conta genérica de clientes: a nota é só indicação
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
                              confianca=conf, observacao=obs,
                              motivo="" if status == "ok" else MOTIVO_BUSCA.get(c.tipo, "Confirme a nota"),
                              alternativas=[{"conta": a.conta, "nfs": a.nfs, "texto": a.explicacao} for a in alternativas]))
        if mov.entrada and out[0].motivo:
            out[0].motivo = out[0].motivo.replace("Pago com", "Recebido com").replace("Pagamento parcial", "Recebimento parcial")
        if status == "ok" and c.tipo == "exato" and c.pontos < 90:
            out[0].motivo = "Notas de mesmo valor"
            out[0].status = "revisar"
        if c.juros > 0:
            if conta_juros:
                out.append(self._base(mov, valor=c.juros, conta_contra=conta_juros,
                                      historico=self._modelo(chave_juros, nf=c.nf_texto, parte=parte), nf=c.nf_texto,
                                      parte=parte, status="revisar", origem=origem + " · juros", confianca=70,
                                      observacao=f"{br(c.juros)} acima do saldo da nota"))
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
                out[0].observacao += f" | {nome}: {br(v)} (configure a conta de {nome.split()[0]} retido)"
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
        banco_mov = self._banco_id.get(mov.conta_banco, "")
        livres = [i for i in self.financeiro if not i.usado and (not i.tipo or i.tipo == tipo) and i.data
                  and (not i.banco or not banco_mov or i.banco == banco_mov)]
        for dias in (0, 1, 3):
            perto = [i for i in livres if abs((i.data - mov.data).days) <= dias]
            for i in perto:
                if abs(i.valor - v) < 0.01:
                    i.usado = True
                    return [i]
            if dias <= 1 and len(perto) >= 2:
                comb = subconjunto_soma(perto, v)
                if comb:
                    for i in comb:
                        i.usado = True
                    return comb
        return []

    def _lancar_financeiro(self, mov: Movimento, itens: list[ItemFinanceiro]) -> list[Lancamento]:
        out: list[Lancamento] = []
        for it in itens:
            n_antes = len(out)
            sub = Movimento(id=mov.id, data=mov.data, descricao=f"{mov.descricao} | {it.nome} {it.descricao}",
                            valor=it.valor if mov.entrada else -it.valor, conta_banco=mov.conta_banco, cnpj=mov.cnpj)
            texto_it = normalizar(f"{it.descricao} {it.nome}")
            obs_it = f"Relatório do cliente: {it.descricao}" + (f" (conta {it.conta_cliente} no sistema do cliente)" if it.conta_cliente else "")
            rp = self._regra_prioritaria(sub, texto_it, nf="", obs=obs_it)
            if rp:
                rp.descricao_extrato = mov.descricao
                out.append(rp)
                continue
            conta, score = self.identificar_parte(sub, it.nome) if it.nome else (None, 0)
            nfs = [it.nf] if it.nf else []
            depara = self.depara.get(it.conta_cliente) if it.conta_cliente else ""
            mem_it = self.perfil.memoria.get(chave_memoria(it.descricao)) if it.descricao else None
            if mem_it and mem_it.get("conta") in self.contas and self.contas[mem_it["conta"]].tipo not in ("cliente", "fornecedor"):
                out.append(self._base(sub, conta_contra=mem_it["conta"], historico=formatar(mem_it.get("historico", ""), parte=it.nome,
                                      descricao=descricao_curta(it.descricao), nf=it.nf, mes=_mes(mov)) or self._generico(mov),
                                      nf=it.nf, parte=it.nome, status="ok", origem="Memória do cliente (relatório)", confianca=96,
                                      descricao_extrato=mov.descricao, chave=it.descricao, observacao=obs_it))
                continue
            if depara and depara in self.contas and not conta:
                c_dp = self.contas[depara]
                if c_dp.tipo in ("cliente", "fornecedor"):
                    conta, score = c_dp, 90
                else:
                    hist = self._hist_regra_para_conta(sub, depara) or self._modelo(
                        "generico_entrada" if mov.entrada else "generico_saida", descricao=descricao_curta(it.descricao))
                    out.append(self._base(sub, conta_contra=depara, historico=hist, nf=it.nf, parte=it.nome,
                                          status="ok", origem=f"De-para conta {it.conta_cliente} do cliente",
                                          confianca=92, descricao_extrato=mov.descricao, observacao=obs_it))
                    continue
            if mov.entrada and self.conta_clientes_generica and (not conta or conta.tipo != "cliente"):
                conta, score = self.contas[self.conta_clientes_generica], 90
            if conta:
                generica = conta.codigo == self.conta_clientes_generica
                r = self._lancar_parte(sub, conta, max(score, 85), "Relatório financeiro", nfs=nfs,
                                       nome_parte=(it.nome or conta.nome) if generica else conta.nome,
                                       filtro_nome=it.nome if generica else "")
                if r:
                    for l in r:
                        l.descricao_extrato = mov.descricao
                    if not r[0].observacao.startswith("Relatório"):
                        r[0].observacao = (obs_it + " | " + r[0].observacao).strip(" |")
                    out.extend(r)
                    continue
            # parte não cadastrada: tenta regra/semelhança pela descrição do financeiro
            texto = normalizar(f"{it.descricao} {it.nome}")
            regra = self._regra(sub, texto) or (self._regra(sub, normalizar(mov.descricao)) if len(itens) == 1 else None)
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
                                  motivo="Conta não identificada",
                                  observacao=f"'{it.nome}' não encontrado no razão; escolha a conta ({it.descricao})".strip()))
        for l in out:
            if not l.chave:
                l.chave = l.descricao_extrato
        return out

    # ------------------------------------------------------------------ regras / semelhança
    def _marcar_chave(self, ls, chave):
        for l in ls:
            l.chave = l.chave or chave

    def _regra_prioritaria(self, mov: Movimento, texto: str, nome: str = "", nf: str = "", obs: str = "") -> Lancamento | None:
        for r in self.regras:
            if r.prioritaria and r.conta and r.casa(texto, mov.entrada):
                hist = formatar(r.historico, parte=nome or nome_limpo(mov.descricao), descricao=descricao_curta(texto),
                                nf=nf, mes=_mes(mov)) or self._generico(mov, self.nome_conta(r.conta))
                return self._base(mov, conta_contra=r.conta, historico=hist, nf=nf, status="ok",
                                  origem=f"Regra: {r.nome}", confianca=95, observacao=obs)
        return None

    def _regra(self, mov: Movimento, texto: str):
        for r in self.regras:
            if r.prioritaria:
                continue
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
    for k in ("nf", "parte", "descricao", "banco", "mes", "imposto", "tributo", "pa", "doc"):
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


def subconjunto_soma(itens: list, alvo: float, limite: int = 400):
    """Acha itens cuja soma é exatamente o alvo (centavos). Usa bitset — rápido mesmo com dezenas de itens.
    Prefere usar itens na ordem em que aparecem (lotes de cobrança costumam ser contíguos)."""
    alvo_c = int(round(alvo * 100))
    vals = [int(round(i.valor * 100)) for i in itens][:limite]
    if alvo_c <= 0 or not vals:
        return None
    # caminho rápido: todos os itens
    if sum(vals) == alvo_c:
        return list(itens[: len(vals)])
    mascara = (1 << (alvo_c + 1)) - 1
    estados = [1]
    for v in vals:
        estados.append((estados[-1] | (estados[-1] << v)) & mascara)
    if not (estados[-1] >> alvo_c) & 1:
        return None
    escolhidos, resto = [], alvo_c
    for k in range(len(vals), 0, -1):
        if (estados[k - 1] >> resto) & 1:
            continue  # dá para formar sem o item k
        escolhidos.append(itens[k - 1])
        resto -= vals[k - 1]
        if resto == 0:
            break
    if resto != 0:
        return None
    return list(reversed(escolhidos))
