from datetime import date

from core.busca import buscar, melhor_entre_contas
from core.classificador import Classificador, Contexto
from core.modelos import Conta, Movimento, Titulo
from core.perfil import Perfil
from core.razao import Razao

D = date(2026, 11, 10)


def T(nf, valor, saldo=None, conta="101", dia=1, tipo="cliente"):
    return Titulo(conta, "CLIENTE " + conta, tipo, nf, date(2026, 10, dia), valor, valor if saldo is None else saldo)


def test_exato():
    c = buscar([T("1", 500), T("2", 800)], 800, D)[0]
    assert c.tipo == "exato" and c.nfs == ["2"]


def test_soma_de_notas():
    c = buscar([T("1", 500), T("2", 800), T("3", 120)], 620, D)[0]
    assert c.tipo == "soma" and sorted(c.nfs) == ["1", "3"]


def test_parcela_primeira_e_segunda():
    t = T("10", 3000)
    c = buscar([t], 1000, D)[0]
    assert c.tipo == "parcela" and c.parcela == "1/3"
    c.aplicar()
    c2 = buscar([t], 1000, D)[0]
    assert c2.tipo == "parcela" and c2.parcela == "2/3"
    c2.aplicar()
    c3 = buscar([t], 1000, D)[0]
    assert c3.tipo == "exato" and t.saldo == 1000  # última parcela = saldo


def test_parcela_com_arredondamento():
    c = buscar([T("11", 1000)], 333.34, D)[0]
    assert c.tipo == "parcela" and c.parcela.endswith("/3")


def test_parcial():
    c = buscar([T("12", 1000)], 427.13, D)[0]
    assert c.tipo == "parcial" and c.baixas[0].valor == 427.13


def test_nf_citada_parcial():
    c = buscar([T("1", 500), T("2", 1000)], 250, D, nfs=["2"])[0]
    assert c.tipo == "parcela" and c.nfs == ["2"] and c.parcela == "1/4"


def test_retencao_servicos():
    # 10.000,00 - IRRF 1,5% (150) - PIS/COFINS/CSLL 4,65% (465) = 9.385,00
    c = buscar([T("20", 10000), T("21", 7000)], 9385.00, D)[0]
    assert c.tipo == "retencao" and c.nfs == ["20"]
    assert dict(c.retencoes) == {"IRRF 1,5%": 150.0, "PIS 0,65%": 65.0, "COFINS 3%": 300.0, "CSLL 1%": 100.0}


def test_retencao_iss():
    c = buscar([T("22", 2000)], 1900.00, D)[0]   # ISS 5%
    assert c.tipo == "retencao" and dict(c.retencoes) == {"ISS 5%": 100.0}


def test_juros():
    c = buscar([T("30", 1000)], 1021.50, D)[0]
    assert c.tipo == "juros" and c.juros == 21.5


def test_peps():
    c = buscar([T("1", 500, dia=1), T("2", 800, dia=5), T("3", 900, dia=9)], 1700, D)[0]
    # 500 + 800 + 400 parcial da 3
    assert c.tipo in ("peps", "parcial", "soma")
    assert sum(b.valor for b in c.baixas) == 1700


def test_nota_posterior_ao_pagamento_ignorada():
    t = Titulo("101", "X", "cliente", "99", date(2026, 12, 20), 500, 500)
    assert buscar([t], 500, D) == []


def test_busca_em_todos_os_clientes():
    por_conta = {"101": [T("1", 500, conta="101")], "102": [T("7", 1234.56, conta="102")]}
    c, _ = melhor_entre_contas(por_conta, 1234.56, D, None, 10)
    assert c and c.conta == "102"
    # valor ambíguo (duas contas com a mesma nota) -> não escolhe sozinho
    por_conta["103"] = [T("8", 1234.56, conta="103")]
    c, outros = melhor_entre_contas(por_conta, 1234.56, D, None, 10)
    assert c is None and len(outros) == 2


def test_classificador_gera_retencoes_sem_banco():
    r = Razao()
    r.contas = {"5": Conta("5", "BANCO X", tipo="banco"), "101": Conta("101", "ALFA SERVICOS LTDA", tipo="cliente"),
                "60": Conta("60", "IRRF A RECUPERAR"), "61": Conta("61", "CSRF A RECUPERAR")}
    p = Perfil(nome="t", contas_retencao={"IRRF": {"cliente": "60"}, "PIS": {"cliente": "61"},
                                          "COFINS": {"cliente": "61"}, "CSLL": {"cliente": "61"}})
    tit = [Titulo("101", "ALFA SERVICOS LTDA", "cliente", "20", date(2026, 10, 1), 10000, 10000)]
    clf = Classificador(Contexto(perfil=p, razao=r, titulos=tit))
    mov = Movimento("m1", D, "TED RECEBIDA ALFA SERVICOS", 9385.00, conta_banco="5")
    ls = clf.classificar([mov])
    assert ls[0].conta_contra == "101" and ls[0].valor == 9385.0 and ls[0].nf == "20"
    sem_banco = ls[1:]
    assert {(l.conta_banco, l.conta_contra, l.valor) for l in sem_banco} == {
        ("101", "60", 150.0), ("101", "61", 65.0), ("101", "61", 300.0), ("101", "61", 100.0)}
    assert all(l.natureza == "saida" for l in sem_banco)  # D imposto a recuperar / C cliente
    assert tit[0].saldo == 0


def test_saldo_anterior_vira_titulo(tmp_path):
    from openpyxl import Workbook
    from core.razao import apurar_titulos
    wb = Workbook(); ws = wb.active
    ws.append(["Conta: 201 - 2.1.1.01.0001 - FORNECEDOR ANTIGO LTDA"])
    ws.append(["Data", "Histórico", "Débito", "Crédito", "Saldo"])
    ws.append(["", "Saldo anterior", None, None, "3.500,00 C"])
    ws.append(["05/10/2026", "VLR. REF. NF 900 FORNECEDOR ANTIGO", None, 1000, None])
    ws.append(["10/10/2026", "VLR. PAGO FORNECEDOR ANTIGO", 1500, None, None])
    f = tmp_path / "r.xlsx"; wb.save(f)
    r = Razao.carregar([f])
    tits = apurar_titulos(r)
    abertos = {(t.nf, t.saldo) for t in tits if t.aberto}
    # pagamento sem NF abate o mais antigo (saldo anterior) por PEPS
    assert abertos == {("", 2000.0), ("900", 1000.0)}
