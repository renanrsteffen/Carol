"""Exportação: arquivo de importação do SIGER e planilha de conferência."""
from __future__ import annotations

import io
from collections import defaultdict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .modelos import Lancamento, Titulo
from .util import fmt_data

COLS_SIGER = [
    "Numero do lote", "Descricao do lote", "Numero do Lancamento", "Numero do registro (partida)",
    "Tipo de lancamento (D ou C)", "Data do lancamento (DD/MM/AAAA)", "Conta analitica", "Codigo da filial",
    "Valor do lancamento", "Codigo do historico padrao", "Complemento do historico", "Numero do documento",
    "Desdobramento", "Codigo do cliente/fornecedor", "Tipo inscricao (F-Fis ou J-Jur)", "CNPJ/CPF", "UF",
    "Tipo de cadastro (C-Cli/F-For)", "Centro custo", "Valor centro de custo", "Conta analitica de contrapartida",
    "Codigo da filial de contrapartida", "Cod.do historico de contrapartida", "Compl.do historico de contrapartida",
    "Sequencial de arquivamento", "Arquivamento",
]
LARG_SIGER = [10, 18, 12, 12, 12, 14, 12, 10, 14, 12, 50, 14, 12, 14, 12, 18, 5, 12, 10, 12, 18, 12, 12, 50, 12, 12]
AZUL = "1F3864"


def _cabecalho(ws, cols):
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(name="Calibri", bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=AZUL)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"


def exportaveis(lancs: list[Lancamento]) -> list[Lancamento]:
    return [l for l in lancs if l.status not in ("ja_lancado", "ignorar")]


def validar(lancs: list[Lancamento]) -> list[str]:
    erros = []
    for i, l in enumerate(exportaveis(lancs), 1):
        if not str(l.conta_banco).strip():
            erros.append(f"Linha {i} ({fmt_data(l.data)} {l.valor:.2f}): sem conta do banco")
        if not str(l.conta_contra).strip():
            erros.append(f"Linha {i} ({fmt_data(l.data)} {l.valor:.2f} {l.descricao_extrato[:40]}): sem contrapartida")
        if not str(l.historico).strip():
            erros.append(f"Linha {i}: sem histórico")
        if l.valor <= 0:
            erros.append(f"Linha {i}: valor inválido")
    return erros


def gerar_siger(lancs: list[Lancamento], lote: str, descricao_lote: str, filial: str = "") -> bytes:
    """Uma linha por lançamento; partida (G) = banco, contrapartida (U) = conta da classificação.
    Entrada no banco = D; saída = C. Data em texto e valor sem separador de milhar."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Plan1"
    _cabecalho(ws, COLS_SIGER)
    lote_v = int(lote) if str(lote).isdigit() else lote
    for i, l in enumerate(sorted(exportaveis(lancs), key=lambda x: (x.data, x.conta_banco)), 1):
        tipo = "D" if l.natureza == "entrada" else "C"
        compl = l.historico.strip()
        ws.append([
            lote_v, descricao_lote, i, 1, tipo, fmt_data(l.data), _cod(l.conta_banco), filial or None,
            round(abs(float(l.valor)), 2), None, compl, l.nf or None, None, None, None, None, None, None, None, None,
            _cod(l.conta_contra), filial or None, None, compl, None, None,
        ])
        r = ws.max_row
        ws.cell(r, 6).number_format = "@"
        ws.cell(r, 9).number_format = "0.00"
    for idx, w in enumerate(LARG_SIGER):
        ws.column_dimensions[ws.cell(1, idx + 1).column_letter].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cod(c):
    c = str(c).strip()
    return int(c) if c.isdigit() else c


def gerar_conferencia(lancs: list[Lancamento], titulos: list[Titulo], nomes: dict[str, str], totais_extrato: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumo"
    _cabecalho(ws, ["Conta banco", "Entradas extrato", "Saídas extrato", "Entradas lançadas", "Saídas lançadas",
                    "Já no razão", "Diferença"])
    por_banco = defaultdict(lambda: [0.0, 0.0, 0.0])
    for l in lancs:
        if l.observacao.startswith("Lançamento sem movimento bancário"):
            continue
        b = por_banco[l.conta_banco]
        if l.status == "ja_lancado":
            b[2] += l.valor
        elif l.status != "ignorar":
            b[0 if l.natureza == "entrada" else 1] += l.valor
    for banco, (ent, sai, ja) in por_banco.items():
        te, ts = totais_extrato.get(banco, (0.0, 0.0))
        ws.append([f"{banco} - {nomes.get(banco, '')}", round(te, 2), round(ts, 2), round(ent, 2), round(sai, 2),
                   round(ja, 2), round(te - ts - (ent - sai) - _ja_liq(lancs, banco), 2)])
    for row in ws.iter_rows(min_row=2):
        for c in row[1:]:
            c.number_format = "#,##0.00"
    _larguras(ws, [40, 16, 16, 18, 16, 14, 14])

    ws2 = wb.create_sheet("Lançamentos")
    _cabecalho(ws2, ["Status", "Data", "Banco", "Natureza", "Valor", "Contrapartida", "Nome contrapartida",
                     "Histórico", "NF", "Descrição no extrato", "Origem", "Confiança", "Observação"])
    for l in sorted(lancs, key=lambda x: (x.data, x.conta_banco)):
        ws2.append([ROTULO_STATUS.get(l.status, l.status), fmt_data(l.data), l.conta_banco, l.natureza, l.valor,
                    l.conta_contra, nomes.get(str(l.conta_contra), ""), l.historico, l.nf, l.descricao_extrato,
                    l.origem, l.confianca, l.observacao])
        ws2.cell(ws2.max_row, 5).number_format = "#,##0.00"
    _larguras(ws2, [12, 11, 10, 9, 13, 12, 32, 50, 12, 50, 28, 10, 50])
    ws2.auto_filter.ref = ws2.dimensions

    ws3 = wb.create_sheet("Notas em aberto")
    _cabecalho(ws3, ["Tipo", "Conta", "Nome", "NF", "Data", "Valor", "Saldo em aberto"])
    for t in sorted((t for t in titulos if t.aberto), key=lambda t: (t.tipo, t.nome_conta, t.data)):
        ws3.append([t.tipo, t.conta, t.nome_conta, t.nf or "s/n", fmt_data(t.data), t.valor, t.saldo])
        for c in (6, 7):
            ws3.cell(ws3.max_row, c).number_format = "#,##0.00"
    _larguras(ws3, [11, 10, 40, 12, 11, 14, 16])
    ws3.auto_filter.ref = ws3.dimensions

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _ja_liq(lancs, banco):
    return sum((l.valor if l.natureza == "entrada" else -l.valor) for l in lancs if l.status == "ja_lancado" and l.conta_banco == banco)


def _larguras(ws, larg):
    for i, w in enumerate(larg, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w


ROTULO_STATUS = {
    "ok": "OK",
    "revisar": "Revisar",
    "pendente": "Pendente",
    "ja_lancado": "Já lançado",
    "ignorar": "Ignorar",
}
