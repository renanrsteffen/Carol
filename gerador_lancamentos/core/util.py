"""Funções utilitárias: normalização de texto, valores, datas, CNPJ e notas fiscais."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

import pandas as pd

_ESPACOS = re.compile(r"\s+")


def sem_acento(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in texto if not unicodedata.combining(c))


def normalizar(texto) -> str:
    """Maiúsculas, sem acento, sem pontuação repetida e com espaços simples."""
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""
    t = sem_acento(str(texto)).upper()
    t = re.sub(r"[^A-Z0-9/.,\- ]", " ", t)
    return _ESPACOS.sub(" ", t).strip()


_STOP = {
    "DE", "DA", "DO", "DAS", "DOS", "E", "A", "O", "EM", "P/", "REF", "VLR", "VALOR", "PAGTO",
    "PAGAMENTO", "PAG", "PGTO", "RECEBIMENTO", "RECEB", "REC", "LTDA", "ME", "EPP", "SA", "S/A",
    "EIRELI", "NF", "NFE", "NFS", "NFSE", "NOTA", "FISCAL", "CONF", "CONFORME", "N", "NO", "NR",
    "NUM", "COM", "PARA", "POR", "-", "/", "SPE",
}


def tokens(texto: str, manter_numeros: bool = False) -> list[str]:
    """Quebra o texto em palavras úteis para comparação (remove datas/números e palavras vazias)."""
    t = normalizar(texto)
    t = re.sub(r"[.,/\-]", " ", t)
    out = []
    for p in t.split():
        if p in _STOP or len(p) < 2:
            continue
        if not manter_numeros and any(ch.isdigit() for ch in p):
            continue
        out.append(p)
    return out


def parse_valor(v) -> float | None:
    """Converte '1.234,56', '1234.56', '-1.234,56', '1.234,56 D', 'R$ 10,00' em float (sinal preservado)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and pd.isna(v):
            return None
        return float(v)
    s = str(v).strip().upper().replace("R$", "").replace(" ", "")
    if not s or s in {"-", "NAN", "NONE"}:
        return None
    neg = False
    if s.endswith("-") or s.endswith("D"):
        neg = s.endswith("-") or s.endswith("D")
        s = s[:-1]
    elif s.endswith("C"):
        s = s[:-1]
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.startswith("-"):
        neg, s = True, s[1:]
    s = s.lstrip("+")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


_RE_DATA = re.compile(r"(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?")


def parse_data(v, ano_padrao: int | None = None) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.date()
    if isinstance(v, (int, float)):
        if isinstance(v, float) and pd.isna(v):
            return None
        if 20000 < v < 80000:  # serial do Excel
            return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(v))).date()
        return None
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = _RE_DATA.search(s)
    if not m:
        return None
    d, mth, a = int(m[1]), int(m[2]), m[3]
    if a is None:
        if ano_padrao is None:
            return None
        ano = ano_padrao
    else:
        ano = int(a)
        if ano < 100:
            ano += 2000
    try:
        return date(ano, mth, d)
    except ValueError:
        return None


def fmt_data(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def fmt_valor(v: float) -> str:
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if v < 0 else "") + s


_RE_CNPJ = re.compile(r"(?<!\d)(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)")
_RE_CPF = re.compile(r"(?<!\d)(\d{3}\.\d{3}\.\d{3}-\d{2}|\d{11})(?!\d)")


def extrair_cnpj_cpf(texto: str) -> str | None:
    t = str(texto or "")
    m = _RE_CNPJ.search(t)
    if m:
        return re.sub(r"\D", "", m[1])
    m = _RE_CPF.search(t)
    if m:
        return re.sub(r"\D", "", m[1])
    return None


# Números de nota: "NF 1234", "NF-e nº 1.234", "NFS 55", "N.F. 123", "NOTA FISCAL 99", "NFS-E 12/1"
_RE_NF = re.compile(
    r"(?:\bN\.?\s?F\.?(?:\s?-?\s?(?:E|S|SE|CE))?\b|\bNOTAS?\s+FISCA(?:L|IS)(?:\s+ELETRONICA)?|\bNFS?E?\b|\bDUPL(?:ICATA)?\.?)"
    r"\s*(?:N[ROº°O]?\.?\s*)?[:\-]?\s*((?:\d[\d.]*)(?:\s*[/,E]\s*\d[\d.]*)*)",
)


def extrair_nfs(texto: str) -> list[str]:
    """Extrai números de notas fiscais de um histórico (sem zeros à esquerda)."""
    t = normalizar(texto).replace("º", "").replace("°", "")
    out: list[str] = []
    for m in _RE_NF.finditer(t):
        grupo = m.group(1)
        for parte in re.split(r"\s*[/,E]\s*", grupo):
            num = parte.replace(".", "").lstrip("0")
            if num and len(num) <= 10 and num not in out:
                out.append(num)
    return out


def nf_chave(nf) -> str:
    """Forma canônica de um número de nota (só dígitos, sem zeros à esquerda)."""
    s = re.sub(r"\D", "", str(nf or ""))
    return s.lstrip("0")
