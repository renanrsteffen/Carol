"""Leitura dos comprovantes de arrecadação baixados do e-CAC (DARF, DAS, DARF DCTFWeb, GPS).

O leitor procura os campos pelos rótulos ("Período de Apuração", "Data de Arrecadação",
"Banco", "Composição do Documento de Arrecadação"...), não pela posição, para tolerar
pequenas mudanças de layout. Um PDF pode trazer vários comprovantes.
"""
from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass, field
from datetime import date

from .util import normalizar, parse_data, parse_valor

BANCOS = {
    "001": "BANCO DO BRASIL", "033": "SANTANDER", "041": "BANRISUL", "070": "BRB", "077": "INTER",
    "085": "AILOS", "104": "CAIXA ECONOMICA", "136": "UNICRED", "208": "BTG", "212": "ORIGINAL",
    "237": "BRADESCO", "260": "NUBANK", "290": "PAGBANK", "323": "MERCADO PAGO", "336": "C6",
    "341": "ITAU", "422": "SAFRA", "748": "SICREDI", "756": "SICOOB", "133": "CRESOL", "197": "STONE",
    "403": "CORA", "461": "ASAAS",
}


@dataclass
class ItemComposicao:
    codigo: str
    denominacao: str
    principal: float = 0.0
    multa: float = 0.0
    juros: float = 0.0
    total: float = 0.0


@dataclass
class Comprovante:
    tipo: str                         # DARF | DAS | DCTFWEB | GPS
    numero: str
    data_arrecadacao: date | None
    periodo_apuracao: date | None = None
    vencimento: date | None = None
    cnpj: str = ""
    razao_social: str = ""
    banco: str = ""                   # código (001, 341...)
    agencia: str = ""
    itens: list[ItemComposicao] = field(default_factory=list)
    total: float = 0.0
    arquivo: str = ""
    avisos: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"ecac-{re.sub(r'[^0-9A-Za-z]', '', self.numero) or id(self)}"

    @property
    def principal(self) -> float:
        return round(sum(i.principal for i in self.itens), 2)

    @property
    def multa(self) -> float:
        return round(sum(i.multa for i in self.itens), 2)

    @property
    def juros(self) -> float:
        return round(sum(i.juros for i in self.itens), 2)

    @property
    def banco_nome(self) -> str:
        return BANCOS.get(self.banco, f"BANCO {self.banco}" if self.banco else "")

    @property
    def pa_texto(self) -> str:
        p = self.periodo_apuracao
        return f"{p.month:02d}/{p.year}" if p else ""

    def para_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def de_dict(cls, d: dict) -> "Comprovante":
        d = dict(d)
        d["itens"] = [ItemComposicao(**i) for i in d.get("itens", [])]
        return cls(**d)


# ----------------------------------------------------------------------------------- leitura
def ler_comprovantes(arquivo) -> list[Comprovante]:
    nome = getattr(arquivo, "name", str(arquivo))
    if hasattr(arquivo, "read"):
        dados = arquivo.read()
        if hasattr(arquivo, "seek"):
            arquivo.seek(0)
    else:
        with open(arquivo, "rb") as f:
            dados = f.read()
    import pdfplumber
    paginas = []
    with pdfplumber.open(io.BytesIO(dados)) as pdf:
        for p in pdf.pages:
            paginas.append(p.extract_text() or "")
    comps = interpretar_texto("\n".join(paginas))
    for c in comps:
        c.arquivo = nome
    return comps


RE_INICIO = re.compile(r"COMPROVANTE DE (ARRECADACAO|PAGAMENTO)")
RE_NUM_DOC = re.compile(r"\b(\d{2}\.\d{2}\.\d{5}\.\d{7}-\d|\d{17})\b")
RE_CNPJ = re.compile(r"\b(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2})\b")
RE_DINHEIRO = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")
RE_ITEM = re.compile(r"^\s*(\d{4})(?:[-/]\d{1,2})?\s+(.*)$")

ROTULOS_DATA = {
    "periodo_apuracao": r"PERIODO DE APURACAO|PERIODO APURACAO|COMPETENCIA",
    "vencimento": r"DATA DE VENCIMENTO|VENCIMENTO",
    "data_arrecadacao": r"DATA DE ARRECADACAO|DATA DA ARRECADACAO|DATA DO PAGAMENTO|DATA DE PAGAMENTO|ARRECADADO EM|PAGO EM",
}


def interpretar_texto(texto: str) -> list[Comprovante]:
    linhas = [l.rstrip() for l in texto.splitlines()]
    norm = [normalizar(l) for l in linhas]
    inicios = [i for i, n in enumerate(norm) if RE_INICIO.search(n)]
    if not inicios:
        inicios = [0]
    blocos = []
    for k, ini in enumerate(inicios):
        fim = inicios[k + 1] if k + 1 < len(inicios) else len(linhas)
        blocos.append((linhas[ini:fim], norm[ini:fim]))
    out = []
    for bl, bn in blocos:
        c = _interpretar_bloco(bl, bn)
        if c and (c.itens or c.total):
            out.append(c)
    return out


def _datas_por_rotulo(bl: list[str], bn: list[str]) -> dict[str, date]:
    achadas: dict[str, date] = {}
    for i, n in enumerate(bn):
        rotulos = []
        for campo, pad in ROTULOS_DATA.items():
            for m in re.finditer(pad, n):
                rotulos.append((m.start(), m.end(), campo))
        if not rotulos:
            continue
        rotulos.sort()
        # remove rótulo "VENCIMENTO" contido em "DATA DE VENCIMENTO" etc.
        filtrados = []
        for r in rotulos:
            if filtrados and r[0] < filtrados[-1][1]:
                continue
            filtrados.append(r)
        # 1) data na mesma linha, logo após cada rótulo
        mesma_linha = False
        for j, (ini, fim, campo) in enumerate(filtrados):
            prox = filtrados[j + 1][0] if j + 1 < len(filtrados) else len(n)
            trecho = n[fim:prox]
            m = re.search(r"\d{2}/\d{2}/\d{4}|\b\d{2}/\d{4}\b", trecho)
            if m and campo not in achadas:
                achadas[campo] = _data_ou_competencia(m[0])
                mesma_linha = True
        if mesma_linha:
            continue
        # 2) cabeçalho numa linha e valores na seguinte (na mesma ordem)
        for k in range(i + 1, min(i + 3, len(bn))):
            datas = re.findall(r"\d{2}/\d{2}/\d{4}|\b\d{2}/\d{4}\b", bn[k])
            if datas:
                for (_, _, campo), d in zip(filtrados, datas):
                    achadas.setdefault(campo, _data_ou_competencia(d))
                break
    return achadas


def _data_ou_competencia(t: str) -> date | None:
    if re.fullmatch(r"\d{2}/\d{4}", t):
        return date(int(t[3:]), int(t[:2]), 1)
    return parse_data(t)


def _interpretar_bloco(bl: list[str], bn: list[str]) -> Comprovante | None:
    junto = "\n".join(bn)
    if "SIMPLES NACIONAL" in junto or "DOCUMENTO DE ARRECADACAO DO SIMPLES" in junto or re.search(r"\bDAS\b", junto):
        tipo = "DAS"
    elif "DCTFWEB" in junto.replace(" ", "") or "DCTF WEB" in junto:
        tipo = "DCTFWEB"
    elif re.search(r"\bGPS\b|GUIA DA PREVIDENCIA", junto):
        tipo = "GPS"
    else:
        tipo = "DARF"

    m = RE_NUM_DOC.search("\n".join(bl))
    numero = m[1] if m else ""
    if not numero:
        for i, n in enumerate(bn):
            if "NUMERO DO DOCUMENTO" in n:
                cand = re.findall(r"[\d.\-/]{8,}", " ".join(bl[i:i + 2]))
                if cand:
                    numero = cand[0]
                    break

    m = RE_CNPJ.search("\n".join(bl))
    cnpj = re.sub(r"\D", "", m[1]) if m else ""
    razao = ""
    if m:
        for l in bl:
            if m[1] in l:
                razao = l.split(m[1], 1)[1].strip(" -")
                break

    datas = _datas_por_rotulo(bl, bn)

    banco = agencia = ""
    for i, n in enumerate(bn):
        if re.search(r"\bBANCO\b", n) and not banco:
            trecho = " ".join(bn[i:i + 2])
            mb = re.search(r"BANCO\s*:?\s*(\d{3})\b", trecho) or re.search(r"\b(\d{3})\s*-\s*[A-Z]", " ".join(bn[i + 1:i + 2]))
            if mb:
                banco = mb[1]
            ma = re.search(r"AGENCIA\s*:?\s*(\d{3,5})", trecho)
            if ma:
                agencia = ma[1]
            elif "AGENCIA" in n and i + 1 < len(bn):
                # cabeçalho "Data de Arrecadação  Banco  Agência" e valores na linha seguinte
                nums = re.findall(r"\b\d{3,5}\b", re.sub(r"\d{2}/\d{2}/\d{4}", " ", bn[i + 1]))
                if nums:
                    banco = banco or nums[0]
                    if len(nums) > 1:
                        agencia = nums[1]

    # composição
    itens: list[ItemComposicao] = []
    total = 0.0
    em_comp = False
    for l, n in zip(bl, bn):
        if "COMPOSICAO" in n:
            em_comp = True
            continue
        if not em_comp:
            continue
        if n.startswith(("TOTAL", "TOTAIS", "VALOR TOTAL")):
            vals = RE_DINHEIRO.findall(l)
            if vals:
                total = parse_valor(vals[-1]) or 0.0
            em_comp = False
            continue
        mi = RE_ITEM.match(l)
        vals = RE_DINHEIRO.findall(l)
        if mi and vals:
            texto = l[mi.start(2): l.find(vals[0])].strip(" -") if vals else mi[2]
            nums = [parse_valor(v) or 0.0 for v in vals]
            if len(nums) >= 4:
                pr, mu, ju, to = nums[-4:]
            elif len(nums) == 3:
                pr, mu, ju = nums
                to = pr + mu + ju
            else:
                pr = to = nums[-1]
                mu = ju = 0.0
            itens.append(ItemComposicao(mi[1], texto, round(pr, 2), round(mu, 2), round(ju, 2), round(to, 2)))
        elif itens and not vals and n and not n.startswith(("CODIGO", "PAGINA")):
            itens[-1].denominacao = f"{itens[-1].denominacao} {l.strip()}".strip()

    if not itens:
        # sem composição legível: usa o valor total do documento
        for l, n in zip(bl, bn):
            if re.search(r"VALOR TOTAL|TOTAL A PAGAR|VALOR PAGO", n):
                vals = RE_DINHEIRO.findall(l)
                if vals:
                    total = parse_valor(vals[-1]) or 0.0
                    break
    c = Comprovante(tipo=tipo, numero=numero, data_arrecadacao=datas.get("data_arrecadacao"),
                    periodo_apuracao=datas.get("periodo_apuracao"), vencimento=datas.get("vencimento"),
                    cnpj=cnpj, razao_social=razao, banco=banco, agencia=agencia, itens=itens,
                    total=round(total or sum(i.total for i in itens), 2))
    soma = round(sum(i.total for i in itens), 2)
    if itens and total and abs(soma - total) > 0.01:
        c.avisos.append(f"Soma da composição ({soma:.2f}) diferente do total ({total:.2f})")
    if not c.data_arrecadacao:
        c.avisos.append("Data de arrecadação não encontrada")
    if not itens:
        c.avisos.append("Composição não encontrada; usado o valor total")
        c.itens = [ItemComposicao("", "SIMPLES NACIONAL" if tipo == "DAS" else "TRIBUTO", c.total, 0, 0, c.total)]
    return c
