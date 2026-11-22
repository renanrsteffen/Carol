import subprocess
import sys
from pathlib import Path

import pytest

from core.classificador import Classificador, Contexto
from core.ecac import interpretar_texto, ler_comprovantes
from core.perfil import Perfil
from core.processo import processar
from core.tributos import identificar

EX = Path(__file__).resolve().parent.parent / "exemplos"


@pytest.fixture(scope="module")
def cenario():
    for s in ("gerar_exemplos.py", "gerar_comprovantes.py"):
        subprocess.run([sys.executable, str(EX / s)], check=True, capture_output=True)
    perfil = Perfil(nome="T", cnpj="12.345.678/0001-90")
    r = processar(perfil, [EX / "razao_outubro.xlsx"], [(EX / "extrato_bb_novembro.ofx", "5")], plano=EX / "plano_de_contas.xlsx")
    comps = ler_comprovantes(EX / "comprovantes_ecac.pdf")
    clf = Classificador(Contexto(perfil=perfil, razao=r.razao, titulos=r.titulos, comprovantes=comps))
    return clf, clf.classificar(r.movimentos), comps


def _linhas(lancs, doc):
    return [l for l in lancs if l.nf == doc]


def test_leitura_dos_comprovantes(cenario):
    _, _, comps = cenario
    assert [c.tipo for c in comps] == ["DARF", "DARF", "DAS", "DARF"]
    c = comps[1]
    assert (c.numero, c.banco, c.agencia, str(c.data_arrecadacao), str(c.periodo_apuracao)) == \
        ("07.18.26320.7654321-0", "001", "1234", "2026-11-26", "2026-09-30")
    assert (c.principal, c.multa, c.juros, c.total) == (1000.0, 120.0, 56.67, 1176.67)
    assert comps[0].cnpj == "12345678000190"


def test_identificacao_de_tributos():
    assert identificar("2172", "COFINS") == ("COFINS", True)
    assert identificar("5952", "PIS/COFINS/CSLL - Retenção") == ("CSRF", True)
    assert identificar("", "IMPOSTO SOBRE A RENDA RETIDO NA FONTE") == ("IRRF", True)
    assert identificar("1099", "CP PATRONAL", "DCTFWEB") == ("INSS", True)
    assert identificar("1001", "IRPJ - SIMPLES NACIONAL", "DAS") == ("SIMPLES", True)
    assert identificar("9999", "TAXA DE FISCALIZACAO") == ("OUTROS", False)


def test_das_tudo_no_simples(cenario):
    _, lancs, _ = cenario
    ls = _linhas(lancs, "07.20.26310.5555555-1")
    assert [(l.conta_contra, l.valor) for l in ls] == [("223", 200.0)] and ls[0].status == "ok"


def test_darf_pis_cofins_no_passivo_com_provisao(cenario):
    _, lancs, _ = cenario
    ls = _linhas(lancs, "07.18.26320.1234567-8")
    assert [(l.conta_contra, l.valor) for l in ls] == [("220", 800.0), ("221", 173.33)]
    assert all(l.status == "ok" and l.conta_banco == "5" and l.natureza == "saida" for l in ls)
    assert "COFINS PA 10/2026" in ls[0].historico


def test_multa_e_juros_em_contas_proprias(cenario):
    _, lancs, _ = cenario
    ls = _linhas(lancs, "07.18.26320.7654321-0")
    assert [(l.conta_contra, l.valor) for l in ls] == [("222", 1000.0), ("440", 120.0), ("441", 56.67)]
    assert len({l.mov_id for l in ls}) == 1   # mesma linha do extrato


def test_taxa_esporadica_na_despesa_e_sem_extrato(cenario):
    _, lancs, _ = cenario
    ls = _linhas(lancs, "07.18.26320.9999999-9")
    assert ls[0].conta_contra == "442"
    # pago no Itaú, cliente só tem conta no Banco do Brasil: pede a conta do banco
    assert ls[0].conta_banco == "" and ls[0].status == "pendente" and ls[0].motivo == "Banco do comprovante sem conta"


def test_linha_generica_do_extrato_substituida(cenario):
    _, lancs, _ = cenario
    darfs = [l for l in lancs if l.descricao_extrato == "PAGAMENTO DARF"]
    assert darfs and all("e-CAC" in l.origem for l in darfs)


def test_varios_darfs_num_debito_e_cnpj_errado():
    txt = """Comprovante de Arrecadação
CNPJ Razão Social
99.999.999/0001-99 OUTRA EMPRESA
Período de Apuração Data de Vencimento Número do Documento
31/10/2026 25/11/2026 07.18.26320.1111111-1
Data de Arrecadação Banco Agência
25/11/2026 001 - BANCO DO BRASIL 1234
Composição do Documento de Arrecadação
2172 COFINS 100,00 0,00 0,00 100,00
Totais 100,00 0,00 0,00 100,00
Comprovante de Arrecadação
CNPJ Razão Social
12.345.678/0001-90 EMPRESA EXEMPLO LTDA
Período de Apuração Data de Vencimento Número do Documento
31/10/2026 25/11/2026 07.18.26320.2222222-2
Data de Arrecadação Banco Agência
25/11/2026 001 - BANCO DO BRASIL 1234
Composição do Documento de Arrecadação
8109 PIS 50,00 0,00 0,00 50,00
Totais 50,00 0,00 0,00 50,00
"""
    comps = interpretar_texto(txt)
    assert [c.total for c in comps] == [100.0, 50.0]
    from datetime import date
    from core.modelos import Conta, Movimento
    from core.razao import Razao
    r = Razao()
    r.contas = {"5": Conta("5", "BANCO DO BRASIL", tipo="banco"), "220": Conta("220", "COFINS A RECOLHER", "2.1.3.1"),
                "221": Conta("221", "PIS A RECOLHER", "2.1.3.2")}
    clf = Classificador(Contexto(perfil=Perfil(nome="t", cnpj="12345678000190"), razao=r, comprovantes=comps))
    ls = clf.classificar([Movimento("m", date(2026, 11, 25), "DARF", -150.0, conta_banco="5")])
    assert {(l.conta_contra, l.valor) for l in ls} == {("220", 100.0), ("221", 50.0)}
    assert ls[0].status == "pendente" and "outra empresa" in ls[0].motivo
