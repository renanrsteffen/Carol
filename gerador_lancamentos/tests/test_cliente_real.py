"""Testes com trechos no formato real dos arquivos (SIGER, sistema do cliente, e-CAC)."""
from datetime import date

from openpyxl import Workbook

from core.classificador import Classificador, Contexto, subconjunto_soma
from core.conciliado import interpretar_conciliado
from core.ecac import interpretar_relacao
from core.modelos import Conta, ItemFinanceiro, Movimento, MovRazao
from core.perfil import Perfil, Regra
from core.razao import Razao
from core.tributos import aprender_codigos, conta_por_codigo


def test_razao_siger_codigo_reduzido_grupo_e_saldo(tmp_path):
    wb = Workbook(); ws = wb.active
    ws.append(["* R A Z A O  A N A L I T I C O *"])
    ws.append(["LOTE", "LANCTO", "REG", "HISTORICO", "N.DOCTO", "DATA", "DEBITO", "CREDITO  S", "ALDO DO MES SA", "LDO EXERCICIO"])
    ws.append(["Grupo: 1.1.1.02.02 BANCOS CONTA MOVIMENTO"])
    ws.append(["Conta: 1.1.1.02.02.00157 SICREDI"])
    ws.append([None, None, None, "SALDO ANTERIOR", None, "", None, None, 0, 100])
    ws.append(["Grupo: 2.1.1.01.01 FORNECEDORES NO PAIS"])
    ws.append(["Conta: 2.1.1.01.01.01275 METAL SUL COMERCIO"])
    ws.append([None, None, None, "SALDO ANTERIOR", None, "", None, None, 0, -500])
    ws.append([331008, 899, 2, "ENTRADA NOTA FISCAL Contrapartida: 4.1.1.01.01.00014 compras", 732, date(2026, 3, 16), None, 222, -222, -722])
    ws.append([402, 35, 2, "VLR PGTO CFE FATURA Contrapartida: 1.1.1.02.02.00157 SICREDI", None, date(2026, 4, 2), 222, None, 0, -500])
    f = tmp_path / "razao.xlsx"; wb.save(f)
    r = Razao.carregar([f])
    assert r.contas["157"].tipo == "banco" and r.contas["1275"].tipo == "fornecedor"
    assert r.contas["157"].classificacao == "1.1.1.02.02.00157"
    m = next(m for m in r.movimentos if m.credito == 222)
    assert m.nfs == ["732"] and m.contrapartida == "14"
    assert r.saldos_anteriores["1275"][0] == 500 and r.saldos_anteriores["1275"][1]   # credor


def test_extrato_conciliado_do_cliente():
    linhas = [
        ("Banco: 748 SICREDI Agência: 0000 SICREDI", 300, {}),
        ("Conta Custo Histórico Entradas Saídas", 560, {"ent": 480, "sai": 560}),
        ("01/10/2026", 100, {}),
        ("20 8 RECEBIDO PEDIDO/NF Nº 10001-3 - AUDIO EXEMPLO COMERCIO DE 696,03", 481, {}),
        ("ELETROELETRONICOS LTDA", 300, {}),
        ("743 8 VALOR REF RET LUCRO JOAO FOR CHINA 3.725,00", 559, {}),
        ("324 8 1027/1 - METAL SUL COMERCIO LTDA 180,00", 560, {}),
        ("Saldo Anterior: 0,00 Saldo Atual:0,00 Total do Dia: 696,03 3.905,00", 560, {}),
    ]
    itens, avisos = interpretar_conciliado([linhas])
    assert not avisos
    a, b, c = itens
    assert (a.tipo, a.valor, a.conta_cliente, a.banco) == ("receber", 696.03, "20", "748")
    assert a.nome == "AUDIO EXEMPLO COMERCIO DE ELETROELETRONICOS LTDA"
    assert (b.tipo, b.conta_cliente) == ("pagar", "743")
    assert c.nf == "1027" and c.nome.startswith("METAL SUL")


def test_relacao_de_pagamentos_ecac():
    txt = """Contribuinte: 12.345.678/0001-90   Data de arrecadação: 01/02/2026 a 23/11/2026
Pagamentos Selecionados
     29/10/2026            1734       07000000000000001               30/10/2026                      1.713,07      342,60       484,88     2.540,55
             08/10/2026            0086           1900000001                  08/10/2026                      3.432,35        0,00          0,00    3.432,35
"""
    cs = interpretar_relacao(txt)
    assert [c.itens[0].codigo for c in cs] == ["1734", "0086"]
    assert (cs[0].principal, cs[0].multa, cs[0].juros, cs[0].total) == (1713.07, 342.6, 484.88, 2540.55)
    assert cs[0].cnpj == "12345678000190" and cs[0].banco == ""


def test_aprende_codigo_de_darf_no_razao():
    r = Razao()
    r.contas = {"157": Conta("157", "SICREDI", tipo="banco"), "49": Conta("49", "SIMPLES PARCELAMENTO"),
                "135": Conta("135", "PARCELAMENTO RECEITA FEDERAL"), "35": Conta("35", "DESPESAS COM IMPORTACAO")}
    d1, d2 = date(2026, 8, 28), date(2026, 9, 21)
    r.movimentos = [
        MovRazao("157", d1, "PGTO PARCELAMENTO", credito=8694.73, contrapartida="135"),
        MovRazao("157", d1, "PGTO SIMPLES PARCELAMENTO", credito=2500.79, contrapartida="49"),
        MovRazao("157", d2, "VLR REF DESPESAS COM IMPORTACAO", credito=300.0, contrapartida="35"),
    ]
    txt = """Pagamentos Selecionados
 28/08/2026 1124 07000000000000002 31/08/2026 7.283,24 0,00 1.411,49 8.694,73
 28/08/2026 1734 07000000000000003 31/08/2026 1.686,25 337,23 477,31 2.500,79
 21/09/2026 0086 1900000002 21/09/2026 200,00 0,00 0,00 200,00
 21/09/2026 7811 1900000003 21/09/2026 100,00 0,00 0,00 100,00
"""
    a = aprender_codigos(interpretar_relacao(txt), r)
    assert a["1124"]["conta"] == "135" and a["1124"]["modo"] == "total"
    assert a["1734"]["conta"] == "49"
    assert a["0086"]["conta"] == "35" and a["7811"]["conta"] == "35"   # soma do dia
    assert conta_por_codigo(a, "1124", 7283.24)[0] == "135"


def test_lote_de_cobranca_por_soma():
    itens = [ItemFinanceiro(date(2026, 10, 1), v) for v in [696.03, 14.33, 593.59, 6348.89, 1057.01, 50.0, 8.0]]
    alvo = 696.03 + 14.33 + 6348.89 + 1057.01
    comb = subconjunto_soma(itens, alvo)
    assert comb and round(sum(i.valor for i in comb), 2) == round(alvo, 2)
    assert subconjunto_soma(itens, 0.01) is None


def _clf(regras=None, movs_razao=None):
    r = Razao()
    r.contas = {"157": Conta("157", "SICREDI", tipo="banco"), "2": Conta("2", "BANCO DO BRASIL", tipo="banco"),
                "155": Conta("155", "JOAO P SOCIO - ADTOS"), "1151": Conta("1151", "JOAO PEREIRA SOCIO", tipo="fornecedor")}
    r.movimentos = movs_razao or []
    p = Perfil(nome="t")
    if regras:
        from dataclasses import asdict
        p.regras = [asdict(x) for x in regras] + p.regras
    return Classificador(Contexto(perfil=p, razao=r))


def test_transferencia_entre_contas_lancada_uma_vez():
    clf = _clf()
    ls = clf.classificar([Movimento("a", date(2026, 10, 7), "TRANSF ENTRE CONTAS SIC-BB", -9000.0, conta_banco="157"),
                          Movimento("b", date(2026, 10, 7), "TRANSFERENCIA RECEBIDA", 9000.0, conta_banco="2")])
    a = next(l for l in ls if l.mov_id == "a"); b = next(l for l in ls if l.mov_id == "b")
    assert (a.conta_contra, a.status) == ("2", "ok") and b.status == "ja_lancado"


def test_regra_de_socio_prioritaria_com_forma_simples():
    regra = Regra("Socio China", "JOAO + CHINA", "saida", "155", "VLR REF. JOAO P SOCIO", True, True)
    clf = _clf([regra])
    clf.financeiro = [ItemFinanceiro(date(2026, 10, 1), 3725.0, descricao="VALOR REF RET LUCRO JOAO FOR CHINA",
                                     tipo="pagar", conta_cliente="743")]
    l = clf.classificar([Movimento("m", date(2026, 10, 1), "TRANSF ENTRE CONTAS 12345678909 JOAO PEREIRA SOCIO",
                                   -3725.0, conta_banco="157")])[0]
    assert (l.conta_contra, l.historico, l.status) == ("155", "VLR REF. JOAO P SOCIO", "ok")


def test_conta_dominante_do_banco():
    movs = [MovRazao("157", date(2026, 9, d % 28 + 1), "VLR REF. COBRANCA CLIENTE", debito=10.0 + d, contrapartida="121")
            for d in range(30)]
    clf = _clf(movs_razao=movs)
    clf.contas["121"] = Conta("121", "CLIENTES NO PAIS", tipo="cliente")
    clf.dominante = clf._contas_dominantes()
    l = clf.classificar([Movimento("m", date(2026, 10, 2), "LIQ.COBRANCA SIMPLES-COB000010", 13171.41, conta_banco="157")])[0]
    assert l.conta_contra == "121" and l.status == "ok"
