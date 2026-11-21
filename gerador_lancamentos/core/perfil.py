"""Perfil do cliente: contas, regras, modelos de histórico e o que o programa aprendeu.

Cada cliente fica salvo em clientes/<nome>.json. Tudo que o contador corrige e marca como
"memorizar" vira regra para os próximos meses.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .util import normalizar

PASTA = Path(__file__).resolve().parent.parent / "clientes"

# Modelos de histórico. Campos: {nf} {parte} {descricao} {banco} {mes}
MODELOS_PADRAO = {
    "pagamento_fornecedor_nf": "VLR. PAGO REF. NF {nf} - {parte}",
    "pagamento_fornecedor": "VLR. PAGO A {parte}",
    "recebimento_cliente_nf": "VLR. RECEBIDO REF. NF {nf} - {parte}",
    "recebimento_cliente": "VLR. RECEBIDO DE {parte}",
    "juros_pagos": "VLR. JUROS/MULTA S/ PAGTO NF {nf} - {parte}",
    "juros_recebidos": "VLR. JUROS RECEBIDOS S/ NF {nf} - {parte}",
    "desconto_obtido": "VLR. DESCONTO OBTIDO S/ NF {nf} - {parte}",
    "desconto_concedido": "VLR. DESCONTO CONCEDIDO S/ NF {nf} - {parte}",
    "generico_saida": "VLR. PAGO REF. {descricao}",
    "generico_entrada": "VLR. RECEBIDO REF. {descricao}",
}


@dataclass
class Regra:
    nome: str
    padrao: str                 # expressão regular sobre o texto do extrato (maiúsculo, sem acento)
    natureza: str = "ambos"     # entrada | saida | ambos
    conta: str = ""             # contrapartida; vazio = aprender do razão
    historico: str = ""         # modelo de histórico
    ativa: bool = True

    def casa(self, texto_norm: str, entrada: bool) -> bool:
        if not self.ativa or not self.padrao:
            return False
        if self.natureza == "entrada" and not entrada:
            return False
        if self.natureza == "saida" and entrada:
            return False
        try:
            return re.search(self.padrao, texto_norm) is not None
        except re.error:
            return self.padrao.upper() in texto_norm


REGRAS_PADRAO = [
    Regra("Tarifa bancária", r"\bTAR\b|TARIFA|\bTAR\.|CESTA|PACOTE (DE )?SERV|MANUT(ENCAO)? (DE )?CONTA|TX\.? |TAXA (DE )?(EMISSAO|MANUT|SERV|PIX|TED|DOC|BOLETO)|CUSTAS? (DE )?COBRANCA|TAR COBRANCA|DESP(ESAS)? COBRANCA", "saida", historico="VLR. REF. TARIFA BANCÁRIA CONF. EXTRATO"),
    Regra("IOF", r"\bIOF\b", "saida", historico="VLR. REF. IOF CONF. EXTRATO"),
    Regra("Juros bancários pagos", r"JUROS (S/ ?)?(LIMITE|CHEQUE|SALDO|CONTA|ADIANT)|JUROS CH|ENCARGOS|JUROS MORA BANC|\bJUROS\b", "saida", historico="VLR. REF. JUROS BANCÁRIOS CONF. EXTRATO"),
    Regra("Aplicação financeira", r"APLIC|APLICACAO|INVEST(IMENTO)?|CDB.*APL|APL\.? AUT|POUP.*APL", "saida", historico="VLR. REF. APLICAÇÃO FINANCEIRA"),
    Regra("Resgate de aplicação", r"RESG|RESGATE|REG\.? APLIC|RESG\.? AUT", "entrada", historico="VLR. REF. RESGATE DE APLICAÇÃO FINANCEIRA"),
    Regra("Rendimento de aplicação", r"REND(IMENTO)?S?\b|REND PAGO|RENDIMENTOS?", "entrada", historico="VLR. REF. RENDIMENTO DE APLICAÇÃO FINANCEIRA"),
    Regra("Telefone / internet", r"TELEFON|\bVIVO\b|\bCLARO\b|\bTIM\b|\bOI\b|EMBRATEL|NET SERV|TELECOM|INTERNET|ALGAR", "saida", historico="VLR. PAGO REF. DESPESA COM TELEFONE/INTERNET - {parte}"),
    Regra("Energia elétrica", r"ENERGIA|CELESC|CEMIG|COPEL|CPFL|\bENEL\b|\bRGE\b|ELETROPAULO|EQUATORIAL|NEOENERGIA|COELBA|LIGHT S", "saida", historico="VLR. PAGO REF. ENERGIA ELÉTRICA - {parte}"),
    Regra("Água e esgoto", r"SANEAMENTO|CASAN|SABESP|CORSAN|SANEPAR|SAMAE|COPASA|EMBASA|SEMAE|\bDAE\b AGUA|AGUA E ESGOTO", "saida", historico="VLR. PAGO REF. ÁGUA E ESGOTO - {parte}"),
    Regra("Simples Nacional (DAS)", r"\bDAS\b|SIMPLES NAC|PGDAS", "saida", historico="VLR. PAGO REF. SIMPLES NACIONAL (DAS)"),
    Regra("DARF / tributos federais", r"\bDARF\b|RECEITA FED|\bPIS\b|COFINS|\bIRPJ\b|\bCSLL\b|\bIRRF\b", "saida", historico="VLR. PAGO REF. TRIBUTOS FEDERAIS (DARF)"),
    Regra("INSS (GPS/DCTFWeb)", r"\bGPS\b|\bINSS\b|DCTFWEB|PREVIDENCIA", "saida", historico="VLR. PAGO REF. INSS"),
    Regra("FGTS", r"\bFGTS\b|\bGRF\b|FGTS DIGITAL|\bGFD\b", "saida", historico="VLR. PAGO REF. FGTS"),
    Regra("ICMS / ISS / tributos estaduais e municipais", r"\bICMS\b|\bISS\b|\bISSQN\b|\bDAE\b|\bGARE\b|\bDARE\b|\bIPTU\b|\bIPVA\b|PREFEITURA|SEFAZ|TAXA ALVARA", "saida", historico="VLR. PAGO REF. TRIBUTOS ESTADUAIS/MUNICIPAIS - {descricao}"),
    Regra("Folha de pagamento", r"SALARIO|FOLHA|PAG(TO)? ?SAL|PAGTO FUNC|PROVENTOS|SISPAG SALARIOS", "saida", historico="VLR. PAGO REF. FOLHA DE PAGAMENTO"),
    Regra("Empréstimo / financiamento (parcela)", r"EMPREST|FINANC|CONS PARCELA|PARC(ELA)? (EMPR|FINANC|CONSORC)|AMORTIZ|BNDES|PRONAMPE|GIRO", "saida", historico="VLR. PAGO REF. PARCELA DE EMPRÉSTIMO/FINANCIAMENTO"),
    Regra("Cartão de crédito / maquininha (recebimento)", r"CIELO|REDE\b|REDECARD|GETNET|STONE|PAGSEGURO|SUMUP|SAFRAPAY|VERO|ANTECIP", "entrada", historico="VLR. RECEBIDO REF. VENDAS COM CARTÃO - {parte}"),
]

# palavras de banco que atrapalham a identificação do nome do favorecido/pagador
RUIDO_BANCARIO = {
    "PIX", "ENVIADO", "ENVIADA", "RECEBIDO", "RECEBIDA", "TED", "DOC", "TEF", "TRANSF", "TRANSFERENCIA",
    "PAGAMENTO", "PAGTO", "PGTO", "PAG", "BOLETO", "BOLETOS", "SISPAG", "FORNEC", "FORNECEDOR", "FORNECEDORES",
    "TITULO", "TITULOS", "COBRANCA", "LIQUIDACAO", "CREDITO", "DEBITO", "DEB", "CRED", "CONTA", "CC", "AGENCIA",
    "AG", "BCO", "BANCO", "QRCODE", "QR", "CODE", "CHAVE", "OUTRA", "OUTRO", "TITULARIDADE", "MESMA", "INT",
    "INTERNET", "MOBILE", "APP", "SPI", "COMPRA", "CARTAO", "ELO", "VISA", "MASTER", "DEPOSITO", "DEP", "ON", "LINE",
    "ONLINE", "AUTOATENDIMENTO", "TAA", "PAGO", "EFETUADO", "ENV", "REC", "DEV", "ESTORNO", "TRANSFER", "CX",
    "ELETR", "ELETRONICO", "AUT", "AGENDADO", "DIA", "TARIFA",
}


@dataclass
class Perfil:
    nome: str
    cnpj: str = ""
    lote: str = "1"
    descricao_lote: str = ""
    contas_banco: dict = field(default_factory=dict)        # identificador do extrato -> código da conta
    conta_juros_pagos: str = ""
    conta_juros_recebidos: str = ""
    conta_desconto_obtido: str = ""
    conta_desconto_concedido: str = ""
    tolerancia_juros_pct: float = 10.0                       # diferença máxima (%) tratada como juros/desconto
    tipos_conta: dict = field(default_factory=dict)          # código -> cliente | fornecedor | banco | outra
    modelos: dict = field(default_factory=lambda: dict(MODELOS_PADRAO))
    regras: list = field(default_factory=lambda: [asdict(r) for r in REGRAS_PADRAO])
    memoria: dict = field(default_factory=dict)              # chave do texto do extrato -> {conta, historico}
    cnpj_conta: dict = field(default_factory=dict)           # CNPJ/CPF -> código da conta
    apelidos: dict = field(default_factory=dict)             # nome no extrato -> código da conta

    # ------------------------------------------------------------------ persistência
    @staticmethod
    def arquivo(nome: str) -> Path:
        seguro = re.sub(r"[^A-Za-z0-9_-]+", "_", normalizar(nome)).strip("_") or "cliente"
        return PASTA / f"{seguro}.json"

    def salvar(self) -> Path:
        PASTA.mkdir(parents=True, exist_ok=True)
        p = self.arquivo(self.nome)
        p.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    @classmethod
    def carregar(cls, nome: str) -> "Perfil":
        p = cls.arquivo(nome)
        if not p.exists():
            return cls(nome=nome)
        dados = json.loads(p.read_text(encoding="utf-8"))
        perfil = cls(**{k: v for k, v in dados.items() if k in cls.__dataclass_fields__})
        # garante novos modelos adicionados em versões futuras
        for k, v in MODELOS_PADRAO.items():
            perfil.modelos.setdefault(k, v)
        return perfil

    @staticmethod
    def listar() -> list[str]:
        if not PASTA.exists():
            return []
        nomes = []
        for p in sorted(PASTA.glob("*.json")):
            try:
                nomes.append(json.loads(p.read_text(encoding="utf-8")).get("nome", p.stem))
            except Exception:
                continue
        return nomes

    # ------------------------------------------------------------------ helpers
    def obter_regras(self) -> list[Regra]:
        return [Regra(**r) for r in self.regras]

    def memorizar(self, texto_extrato: str, conta: str, historico: str, cnpj: str | None = None, parte: str = "") -> None:
        chave = chave_memoria(texto_extrato)
        if chave:
            self.memoria[chave] = {"conta": conta, "historico": historico}
        if cnpj and conta:
            self.cnpj_conta[cnpj] = conta
        if parte and conta:
            self.apelidos[normalizar(parte)] = conta


def chave_memoria(texto: str) -> str:
    """Texto do extrato sem números/datas/CNPJ, para reconhecer o mesmo tipo de movimento."""
    t = normalizar(texto)
    t = re.sub(r"\d+", " ", t)
    t = re.sub(r"[./,\-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()
