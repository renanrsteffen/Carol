"""Gera arquivos fictícios para testar o programa: razão, plano, extrato OFX e relatório financeiro.

Uso:  python exemplos/gerar_exemplos.py
"""
from pathlib import Path

from openpyxl import Workbook

PASTA = Path(__file__).resolve().parent

CONTAS = {
    "5": ("1.1.1.02.001", "BANCO DO BRASIL S/A C/C 12345-6"),
    "20": ("1.1.1.03.001", "APLICACAO FINANCEIRA BB CDB"),
    "101": ("1.1.2.01.0001", "ALFA COMERCIO DE ALIMENTOS LTDA"),
    "102": ("1.1.2.01.0002", "BETA SERVICOS EIRELI"),
    "201": ("2.1.1.01.0001", "FORNECEDORA GAMA INDUSTRIA LTDA"),
    "202": ("2.1.1.01.0002", "DELTA DISTRIBUIDORA DE PECAS LTDA"),
    "301": ("3.1.1.01.0001", "RECEITA DE VENDAS DE MERCADORIAS"),
    "150": ("1.1.4.01.0001", "ESTOQUE DE MERCADORIAS"),
    "401": ("4.1.3.01.0001", "DESPESAS BANCARIAS"),
    "402": ("4.1.3.01.0002", "IOF"),
    "405": ("4.1.2.01.0010", "TELEFONE E INTERNET"),
    "501": ("3.2.1.01.0001", "RENDIMENTOS DE APLICACOES FINANCEIRAS"),
    "410": ("4.1.3.01.0005", "JUROS E MULTAS PAGOS"),
    "510": ("3.2.1.01.0005", "JUROS RECEBIDOS"),
}

# (data, lote, lanc, conta_debito, conta_credito, valor, historico)
LANCS = [
    ("01/10/2026", 10, 1, "150", "201", 5000.00, "VLR. REF. NF 1001 FORNECEDORA GAMA INDUSTRIA LTDA"),
    ("02/10/2026", 10, 2, "150", "201", 2300.00, "VLR. REF. NF 1002 FORNECEDORA GAMA INDUSTRIA LTDA"),
    ("20/10/2026", 10, 3, "150", "201", 1250.00, "VLR. REF. NF 1010 FORNECEDORA GAMA INDUSTRIA LTDA"),
    ("05/10/2026", 10, 4, "150", "202", 870.50, "VLR. REF. NF 555 DELTA DISTRIBUIDORA"),
    ("07/10/2026", 10, 5, "150", "202", 1129.50, "VLR. REF. NF 556 DELTA DISTRIBUIDORA"),
    ("03/10/2026", 11, 1, "101", "301", 3000.00, "VLR. REF. NF 88 ALFA COMERCIO"),
    ("09/10/2026", 11, 2, "101", "301", 4500.00, "VLR. REF. NF 89 ALFA COMERCIO"),
    ("15/10/2026", 11, 3, "102", "301", 1200.00, "VLR. REF. NF 90 BETA SERVICOS"),
    ("10/10/2026", 12, 1, "201", "5", 5000.00, "VLR. PAGO REF. NF 1001 - FORNECEDORA GAMA"),
    ("12/10/2026", 12, 2, "5", "101", 3000.00, "VLR. RECEBIDO REF. NF 88 - ALFA COMERCIO"),
    ("03/10/2026", 12, 3, "401", "5", 45.00, "VLR. REF. TARIFA BANCARIA CONF. EXTRATO"),
    ("14/10/2026", 12, 4, "20", "5", 8000.00, "VLR. REF. APLICACAO FINANCEIRA CDB"),
    ("15/10/2026", 12, 5, "5", "501", 28.40, "VLR. REF. RENDIMENTO DE APLICACAO FINANCEIRA"),
    ("16/10/2026", 12, 6, "405", "5", 189.90, "VLR. PAGO REF. TELEFONE VIVO"),
    ("25/10/2026", 12, 7, "402", "5", 0.87, "VLR. REF. IOF CONF. EXTRATO"),
    ("31/10/2026", 12, 8, "401", "5", 12.00, "VLR. REF. TARIFA BANCARIA CONF. EXTRATO"),
]


def gerar_razao(caminho: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Razao"
    ws.append(["EMPRESA EXEMPLO LTDA"])
    ws.append(["Razão Analítico - Período: 01/10/2026 a 31/10/2026"])
    for cod, (classif, nome) in CONTAS.items():
        movs = []
        for data, lote, lanc, d, c, v, h in LANCS:
            if d == cod:
                movs.append((data, lote, lanc, h, v, None, c))
            elif c == cod:
                movs.append((data, lote, lanc, h, None, v, d))
        if not movs:
            continue
        ws.append([])
        ws.append([f"Conta: {cod} - {classif} - {nome}"])
        ws.append(["Data", "Lote", "Lanç.", "Histórico", "Débito", "Crédito", "Saldo"])
        ws.append(["", "", "", "Saldo anterior", None, None, 0])
        for data, lote, lanc, h, deb, cred, contra in sorted(movs, key=lambda x: x[0][6:] + x[0][3:5] + x[0][:2]):
            # metade dos lançamentos com "Contrapartida:" no histórico, metade só por lote/lançamento
            hist = f"{h} Contrapartida: {contra} - {CONTAS[contra][1]}" if lanc % 2 else h
            ws.append([data, lote, lanc, hist, deb, cred, None])
    wb.save(caminho)


def gerar_plano(caminho: Path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Código", "Classificação", "Descrição"])
    for cod, (classif, nome) in CONTAS.items():
        ws.append([int(cod), classif, nome])
    wb.save(caminho)


TRANS = [
    ("20261031", -12.00, "TARIFA PACOTE DE SERVICOS"),
    ("20261103", -45.00, "TARIFA PACOTE DE SERVICOS"),
    ("20261105", -2300.00, "PIX ENVIADO - FORNECEDORA GAMA IND"),
    ("20261106", -2000.00, "PAGTO BOLETO - DELTA DISTRIB PECAS"),
    ("20261110", 4500.00, "PIX RECEBIDO - 12.345.678/0001-90 ALFA COMERCIO DE ALIM"),
    ("20261112", -10000.00, "APLICACAO BB CDB DI"),
    ("20261113", -189.90, "PAGAMENTO CONTA - VIVO TELEFONICA"),
    ("20261115", 32.15, "RENDIMENTO APLIC AUTOMATICA"),
    ("20261118", 1230.00, "TED RECEBIDA - BETA SERVICOS"),
    ("20261120", -350.00, "PIX ENVIADO - JOAO DA SILVA"),
    ("20261122", -1250.00, "PAGTO BOLETO - 34191.09008"),
    ("20261125", -1.23, "IOF"),
    ("20261128", -3100.00, "SISPAG FORNECEDORES"),
]


def gerar_ofx(caminho: Path):
    linhas = ["OFXHEADER:100", "DATA:OFXSGML", "VERSION:102", "", "<OFX>", "<BANKMSGSRSV1><STMTTRNRS><STMTRS>",
              "<CURDEF>BRL", "<BANKACCTFROM><BANKID>001<ACCTID>12345-6</BANKACCTFROM>", "<BANKTRANLIST>"]
    for i, (dt, v, memo) in enumerate(TRANS, 1):
        linhas += ["<STMTTRN>", f"<TRNTYPE>{'CREDIT' if v > 0 else 'DEBIT'}", f"<DTPOSTED>{dt}120000[-3:BRT]",
                   f"<TRNAMT>{v:.2f}", f"<FITID>{dt}{i:03d}", f"<MEMO>{memo}", "</STMTTRN>"]
    linhas += ["</BANKTRANLIST>", "<LEDGERBAL><BALAMT>10000.00<DTASOF>20261130</LEDGERBAL>",
               "</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"]
    caminho.write_text("\n".join(linhas), encoding="latin-1")


def gerar_financeiro(caminho: Path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Relatório de contas pagas - Novembro/2026"])
    ws.append(["Data pagamento", "Fornecedor", "Documento", "Categoria", "Valor pago"])
    ws.append(["20/11/2026", "JOAO DA SILVA", "77", "Manutenção predial", 350.00])
    ws.append(["28/11/2026", "FORNECEDORA GAMA INDUSTRIA LTDA", "NF 1012", "Compra de mercadorias", 1800.00])
    ws.append(["28/11/2026", "DELTA DISTRIBUIDORA DE PECAS LTDA", "560", "Compra de mercadorias", 1300.00])
    wb.save(caminho)


if __name__ == "__main__":
    gerar_razao(PASTA / "razao_outubro.xlsx")
    gerar_plano(PASTA / "plano_de_contas.xlsx")
    gerar_ofx(PASTA / "extrato_bb_novembro.ofx")
    gerar_financeiro(PASTA / "contas_pagas_novembro.xlsx")
    print("Arquivos gerados em", PASTA)
