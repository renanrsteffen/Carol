"""Gera PDFs fictícios de comprovantes de arrecadação no formato do e-CAC (para testes).

Uso:  python exemplos/gerar_comprovantes.py
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

PASTA = Path(__file__).resolve().parent

COMPROVANTES = [
    # (tipo, numero, PA, vencimento, arrecadacao, banco, agencia, itens[(cod, denom, principal, multa, juros)])
    ("DARF", "07.18.26320.1234567-8", "31/10/2026", "25/11/2026", "25/11/2026", "001 - BANCO DO BRASIL S.A.", "1234",
     [("2172", "COFINS - Contribuição para o Financiamento da Seguridade Social", 800.00, 0, 0),
      ("8109", "PIS - Programa de Integração Social", 173.33, 0, 0)]),
    ("DARF", "07.18.26320.7654321-0", "30/09/2026", "24/10/2026", "26/11/2026", "001 - BANCO DO BRASIL S.A.", "1234",
     [("2089", "IRPJ - Lucro Presumido", 1000.00, 120.00, 56.67)]),
    ("DAS", "07.20.26310.5555555-1", "31/10/2026", "20/11/2026", "19/11/2026", "001 - BANCO DO BRASIL S.A.", "1234",
     [("1001", "IRPJ - SIMPLES NACIONAL", 50.00, 0, 0), ("1002", "CSLL - SIMPLES NACIONAL", 30.00, 0, 0),
      ("1010", "ISS - SIMPLES NACIONAL", 120.00, 0, 0)]),
    ("DARF", "07.18.26320.9999999-9", "15/11/2026", "15/11/2026", "27/11/2026", "341 - ITAU UNIBANCO S.A.", "0500",
     [("6621", "Taxa de Fiscalização Avulsa", 88.50, 0, 0)]),
]


def desenhar(c: canvas.Canvas, comp):
    tipo, numero, pa, venc, arrec, banco, ag, itens = comp
    y = 800
    def t(txt, x=40, dy=16, tam=9):
        nonlocal y
        c.setFont("Helvetica", tam)
        c.drawString(x, y, txt)
        y -= dy
    t("MINISTÉRIO DA FAZENDA - Receita Federal do Brasil", tam=8)
    t("Comprovante de Arrecadação", tam=13, dy=22)
    if tipo == "DAS":
        t("Documento de Arrecadação do Simples Nacional", tam=9)
    t("Dados do Contribuinte", tam=10)
    t("CNPJ                    Razão Social")
    t("12.345.678/0001-90 EMPRESA EXEMPLO LTDA")
    t("Dados do Documento", tam=10)
    t("Período de Apuração   Data de Vencimento   Número do Documento")
    t(f"{pa}   {venc}   {numero}")
    t("Dados do Pagamento", tam=10)
    t("Data de Arrecadação   Banco   Agência")
    t(f"{arrec}   {banco}   {ag}")
    t("Composição do Documento de Arrecadação", tam=10)
    t("Código   Denominação   Principal   Multa   Juros   Total")
    tot = 0
    for cod, den, pr, mu, ju in itens:
        total = pr + mu + ju
        tot += total
        t(f"{cod}   {den}   {fmt(pr)}   {fmt(mu)}   {fmt(ju)}   {fmt(total)}", tam=8)
    t(f"Totais   {fmt(sum(i[2] for i in itens))}   {fmt(sum(i[3] for i in itens))}   {fmt(sum(i[4] for i in itens))}   {fmt(tot)}")
    c.showPage()


def fmt(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def gerar(caminho: Path):
    c = canvas.Canvas(str(caminho), pagesize=A4)
    for comp in COMPROVANTES:
        desenhar(c, comp)
    c.save()


if __name__ == "__main__":
    gerar(PASTA / "comprovantes_ecac.pdf")
    print("Gerado", PASTA / "comprovantes_ecac.pdf")
