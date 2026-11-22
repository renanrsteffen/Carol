import io
import subprocess
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

from core.exportar import gerar_conferencia, gerar_siger, validar
from core.extratos import interpretar_linhas_pdf
from core.perfil import Perfil
from core.processo import processar
from core.util import extrair_nfs, parse_valor

RAIZ = Path(__file__).resolve().parent.parent
EX = RAIZ / "exemplos"


@pytest.fixture(scope="module")
def resultado():
    subprocess.run([sys.executable, str(EX / "gerar_exemplos.py")], check=True, capture_output=True)
    perfil = Perfil(nome="TESTE", conta_juros_pagos="410", conta_juros_recebidos="510")
    return processar(perfil, [EX / "razao_outubro.xlsx"], [(EX / "extrato_bb_novembro.ofx", "5")],
                     [EX / "contas_pagas_novembro.xlsx"])


def _por_valor(res, valor):
    return [l for l in res.lancamentos if abs(l.valor - valor) < 0.001]


def test_parse_valor():
    assert parse_valor("1.234,56") == 1234.56
    assert parse_valor("-1.234,56") == -1234.56
    assert parse_valor("1.234,56 D") == -1234.56
    assert parse_valor("1234.56") == 1234.56
    assert parse_valor("(10,00)") == -10.0


def test_extrair_nfs():
    assert extrair_nfs("VLR PAGO REF. NF 1.234 FORN X") == ["1234"]
    assert extrair_nfs("Pagto NF-e nº 000555/556") == ["555", "556"]
    assert extrair_nfs("NOTA FISCAL 99 - CLIENTE") == ["99"]
    assert extrair_nfs("TARIFA PACOTE") == []


def test_tipos_de_conta(resultado):
    c = resultado.razao.contas
    assert c["5"].tipo == "banco"
    assert c["201"].tipo == "fornecedor" and c["202"].tipo == "fornecedor"
    assert c["101"].tipo == "cliente" and c["102"].tipo == "cliente"
    assert c["150"].tipo == "outra"


def test_notas_abertas_antes_do_extrato():
    perfil = Perfil(nome="T2")
    r = processar(perfil, [EX / "razao_outubro.xlsx"], [])
    abertas = {(t.conta, t.nf): t.saldo for t in r.titulos if t.aberto}
    assert abertas == {("201", "1002"): 2300.0, ("201", "1010"): 1250.0, ("202", "555"): 870.5,
                       ("202", "556"): 1129.5, ("101", "89"): 4500.0, ("102", "90"): 1200.0}


def test_ja_lancado(resultado):
    l = [x for x in _por_valor(resultado, 12.0)][0]
    assert l.status == "ja_lancado" and l.conta_contra == "401"


def test_pagamento_fornecedor_com_nf(resultado):
    l = _por_valor(resultado, 2300.0)[0]
    assert l.conta_contra == "201" and l.nf == "1002" and "NF 1002" in l.historico


def test_pagamento_varias_notas(resultado):
    l = _por_valor(resultado, 2000.0)[0]
    assert l.conta_contra == "202" and l.nf == "555, 556"


def test_recebimento_cliente(resultado):
    l = _por_valor(resultado, 4500.0)[0]
    assert l.conta_contra == "101" and l.nf == "89" and l.natureza == "entrada"


def test_juros_separados(resultado):
    assert _por_valor(resultado, 1200.0)[0].conta_contra == "102"
    j = _por_valor(resultado, 30.0)[0]
    assert j.conta_contra == "510"


def test_regras_aprendidas_do_razao(resultado):
    assert _por_valor(resultado, 45.0)[0].conta_contra == "401"
    assert _por_valor(resultado, 10000.0)[0].conta_contra == "20"
    assert _por_valor(resultado, 189.90)[0].conta_contra == "405"
    assert _por_valor(resultado, 32.15)[0].conta_contra == "501"
    assert _por_valor(resultado, 1.23)[0].conta_contra == "402"


def test_valor_unico_de_nota(resultado):
    l = _por_valor(resultado, 1250.0)[0]
    assert l.conta_contra == "201" and l.nf == "1010" and l.status == "revisar"


def test_financeiro_divide_pagamento_em_lote(resultado):
    a, b = _por_valor(resultado, 1800.0)[0], _por_valor(resultado, 1300.0)[0]
    assert (a.conta_contra, a.nf) == ("201", "1012")
    assert (b.conta_contra, b.nf) == ("202", "560")


def test_pendente_e_validacao(resultado):
    p = _por_valor(resultado, 350.0)[0]
    assert p.status == "pendente" and p.nf == "77"
    assert any("sem contrapartida" in e for e in validar(resultado.lancamentos))


def test_saida_siger(resultado):
    lancs = [l for l in resultado.lancamentos if l.conta_contra]
    dados = gerar_siger(lancs, "15", "BANCO BB NOV/2026")
    ws = load_workbook(io.BytesIO(dados)).active
    linhas = list(ws.iter_rows(min_row=2, values_only=True))
    assert ws.title == "Plan1" and ws.max_column == 26
    assert [l[2] for l in linhas] == list(range(1, len(linhas) + 1))
    assert {l[0] for l in linhas} == {15}
    assert all(isinstance(l[5], str) and len(l[5]) == 10 for l in linhas)
    # a soma do banco confere com o extrato (exceto o que já está no razão)
    total = sum(l[8] if l[4] == "D" else -l[8] for l in linhas)
    exportados = {l.mov_id for l in lancs if l.status != "ja_lancado"}
    extrato = sum(m.valor for m in resultado.movimentos if m.id in exportados)
    assert round(total, 2) == round(extrato, 2)
    gerar_conferencia(resultado.lancamentos, resultado.titulos, {}, resultado.totais_extrato)


def test_pdf_generico():
    linhas = [
        "EXTRATO DE CONTA CORRENTE  Período 01/11/2026 a 30/11/2026",
        "31/10/2026 SALDO ANTERIOR 1.000,00",
        "03/11/2026 Tarifa Pacote de Serviços 45,00 955,00",
        "05/11/2026 Pix - Recebido 200,00 1.155,00",
        "15/11 12345678000190 CLIENTE TESTE",
        "06/11/2026 Pagamento de Boleto 155,00 1.000,00",
        "FORNECEDOR XYZ LTDA",
    ]
    movs, info = interpretar_linhas_pdf(linhas)
    assert [m.valor for m in movs] == [-45.0, 200.0, -155.0]
    assert "CLIENTE TESTE" in movs[1].descricao and "FORNECEDOR XYZ" in movs[2].descricao
    assert info["saldo_inicial"] == 1000.0
