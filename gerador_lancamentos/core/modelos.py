"""Estruturas de dados do programa."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Conta:
    codigo: str                 # código reduzido / analítico usado na importação
    nome: str
    classificacao: str = ""     # ex.: 2.1.1.01.0005
    tipo: str = "outra"         # banco | cliente | fornecedor | outra
    grupo: str = ""             # nome do grupo no razão (ex.: "FORNECEDORES NO PAIS")

    @property
    def rotulo(self) -> str:
        return f"{self.codigo} - {self.nome}"


@dataclass
class MovRazao:
    conta: str
    data: date
    historico: str
    debito: float = 0.0
    credito: float = 0.0
    lote: str = ""
    lancamento: str = ""
    contrapartida: str = ""
    nfs: list[str] = field(default_factory=list)

    @property
    def valor(self) -> float:
        return self.debito - self.credito


@dataclass
class Movimento:
    """Uma linha de extrato bancário. valor > 0 = entrada; valor < 0 = saída."""
    id: str
    data: date
    descricao: str
    valor: float
    conta_banco: str = ""
    banco_nome: str = ""
    documento: str = ""
    cnpj: str | None = None
    saldo: float | None = None
    arquivo: str = ""

    @property
    def entrada(self) -> bool:
        return self.valor > 0


@dataclass
class Titulo:
    """Nota/título em aberto de cliente ou fornecedor apurado a partir do razão."""
    conta: str
    nome_conta: str
    tipo: str                  # cliente | fornecedor
    nf: str
    data: date
    valor: float
    saldo: float
    historico: str = ""

    @property
    def aberto(self) -> bool:
        return self.saldo > 0.009


@dataclass
class ItemFinanceiro:
    """Linha do relatório financeiro do cliente (contas pagas/recebidas)."""
    data: date | None
    valor: float
    nome: str = ""
    nf: str = ""
    descricao: str = ""
    tipo: str = ""             # pagar | receber | ""
    usado: bool = False
    conta_cliente: str = ""    # código da conta no sistema do cliente (extrato conciliado)
    banco: str = ""            # código do banco (001, 748...) quando o relatório é por banco


@dataclass
class Lancamento:
    """Lançamento proposto (partida = banco; contrapartida = conta_contra)."""
    mov_id: str
    data: date
    conta_banco: str
    valor: float               # sempre positivo
    natureza: str              # entrada | saida
    conta_contra: str = ""
    historico: str = ""
    nf: str = ""
    descricao_extrato: str = ""
    confianca: int = 0
    origem: str = ""
    status: str = "pendente"   # ok | revisar | pendente | ja_lancado | ignorar
    observacao: str = ""
    parte: str = ""
    motivo: str = ""           # resumo curto do que pede atenção (ex.: "Parcela de nota")
    alternativas: list = field(default_factory=list)   # [{conta, nfs, texto}] outras possibilidades
    chave: str = ""            # texto usado para memorizar (descrição do relatório do cliente, quando houver)
    principal: bool = True     # False = linha complementar (juros, retenção, desconto) do mesmo movimento
