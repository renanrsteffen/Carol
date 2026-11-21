"""Tudo o que o programa guarda fica na pasta `dados/`, no próprio computador:

    dados/clientes/<cliente>.json            perfil, regras e memória de cada cliente
    dados/trabalhos/<cliente>/<AAAA-MM>.json  trabalho do mês (salvo automaticamente a cada alteração)
    dados/exportados/<cliente>/...xlsx        cópia de cada arquivo exportado para o SIGER
    dados/backups/backup_<data_hora>.zip      cópias de segurança (automática 1x por dia + manual)
    dados/config.json                         pasta extra de backup, quantos backups manter

A gravação é segura: grava num arquivo temporário e só então substitui o original, mantendo
a versão anterior em .bak. Assim uma queda de energia não corrompe os dados.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import zipfile
from datetime import date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DADOS = Path(os.environ.get("GERADOR_DADOS", RAIZ / "dados"))
PASTA_CLIENTES = DADOS / "clientes"
PASTA_TRABALHOS = DADOS / "trabalhos"
PASTA_EXPORTADOS = DADOS / "exportados"
PASTA_BACKUPS = DADOS / "backups"
ARQ_CONFIG = DADOS / "config.json"
SUBPASTAS_BACKUP = ("clientes", "trabalhos", "exportados", "config.json")


def nome_seguro(nome: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", str(nome))
    t = "".join(c for c in t if not unicodedata.combining(c)).upper()
    return re.sub(r"[^A-Z0-9_-]+", "_", t).strip("_") or "CLIENTE"


# --------------------------------------------------------------------------- JSON com datas
class _Codificador(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, datetime):
            return {"__datetime__": o.isoformat()}
        if isinstance(o, date):
            return {"__date__": o.isoformat()}
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        if isinstance(o, set):
            return sorted(o)
        return super().default(o)


def _decodificar(d):
    if "__date__" in d and len(d) == 1:
        return date.fromisoformat(d["__date__"])
    if "__datetime__" in d and len(d) == 1:
        return datetime.fromisoformat(d["__datetime__"])
    return d


def gravar_json(caminho: Path, dados) -> None:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, cls=_Codificador, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    if caminho.exists():
        shutil.copy2(caminho, caminho.with_suffix(caminho.suffix + ".bak"))
    os.replace(tmp, caminho)


def ler_json(caminho: Path, padrao=None):
    caminho = Path(caminho)
    for p in (caminho, caminho.with_suffix(caminho.suffix + ".bak")):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"), object_hook=_decodificar)
            except (json.JSONDecodeError, OSError):
                continue  # arquivo corrompido: tenta a cópia .bak
    return padrao


# ------------------------------------------------------------------------------- configuração
def config() -> dict:
    c = ler_json(ARQ_CONFIG, {}) or {}
    c.setdefault("pasta_backup_extra", "")
    c.setdefault("manter_backups", 60)
    c.setdefault("ultimo_backup", "")
    return c


def salvar_config(c: dict) -> None:
    gravar_json(ARQ_CONFIG, c)


def migrar_pasta_antiga() -> None:
    """Versão anterior guardava os clientes em ./clientes; move para dados/clientes."""
    antiga = RAIZ / "clientes"
    if antiga.exists():
        for p in antiga.glob("*.json"):
            destino = PASTA_CLIENTES / p.name
            if not destino.exists():
                PASTA_CLIENTES.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, destino)


# ---------------------------------------------------------------------------------- trabalhos
def salvar_trabalho(cliente: str, competencia: str, estado: dict) -> Path:
    estado = dict(estado)
    estado["_salvo_em"] = datetime.now()
    estado["_versao_formato"] = 1
    p = PASTA_TRABALHOS / nome_seguro(cliente) / f"{competencia}.json"
    gravar_json(p, estado)
    return p


def listar_trabalhos(cliente: str) -> list[tuple[str, datetime | None]]:
    pasta = PASTA_TRABALHOS / nome_seguro(cliente)
    if not pasta.exists():
        return []
    out = []
    for p in sorted(pasta.glob("*.json"), reverse=True):
        out.append((p.stem, datetime.fromtimestamp(p.stat().st_mtime)))
    return out


def carregar_trabalho(cliente: str, competencia: str) -> dict | None:
    return ler_json(PASTA_TRABALHOS / nome_seguro(cliente) / f"{competencia}.json")


def guardar_exportado(cliente: str, nome_arquivo: str, conteudo: bytes) -> Path:
    pasta = PASTA_EXPORTADOS / nome_seguro(cliente)
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    p = pasta / f"{Path(nome_arquivo).stem}_{carimbo}{Path(nome_arquivo).suffix}"
    p.write_bytes(conteudo)
    return p


# ------------------------------------------------------------------------------------ backups
def fazer_backup(motivo: str = "manual") -> Path:
    PASTA_BACKUPS.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    destino = PASTA_BACKUPS / f"backup_{carimbo}_{nome_seguro(motivo).lower()}.zip"
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for item in SUBPASTAS_BACKUP:
            p = DADOS / item
            if p.is_file():
                z.write(p, item)
            elif p.is_dir():
                for arq in p.rglob("*"):
                    if arq.is_file() and not arq.name.endswith(".tmp"):
                        z.write(arq, arq.relative_to(DADOS).as_posix())
    c = config()
    extra = c.get("pasta_backup_extra", "").strip()
    if extra:
        try:
            Path(extra).mkdir(parents=True, exist_ok=True)
            shutil.copy2(destino, Path(extra) / destino.name)
            _limpar(Path(extra), c["manter_backups"])
        except OSError:
            pass  # pasta extra indisponível (pendrive fora, rede): o backup local continua valendo
    c["ultimo_backup"] = datetime.now().isoformat(timespec="seconds")
    salvar_config(c)
    _limpar(PASTA_BACKUPS, c["manter_backups"])
    return destino


def _limpar(pasta: Path, manter: int) -> None:
    arqs = sorted(pasta.glob("backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in arqs[max(1, int(manter)):]:
        try:
            p.unlink()
        except OSError:
            pass


def backup_automatico() -> Path | None:
    """Um backup por dia, feito ao abrir o programa (só se já houver dados)."""
    c = config()
    hoje = date.today().isoformat()
    if c.get("ultimo_backup", "")[:10] == hoje:
        return None
    if not any((DADOS / s).exists() for s in ("clientes", "trabalhos")):
        return None
    return fazer_backup("automatico")


def listar_backups() -> list[Path]:
    if not PASTA_BACKUPS.exists():
        return []
    return sorted(PASTA_BACKUPS.glob("backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)


def restaurar_backup(conteudo_zip: bytes) -> int:
    """Restaura um backup. Antes, faz um backup do estado atual (nada se perde)."""
    import io
    fazer_backup("antes_de_restaurar")
    n = 0
    with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as z:
        for info in z.infolist():
            nome = info.filename
            partes = Path(nome).parts
            if info.is_dir() or not partes or partes[0] not in SUBPASTAS_BACKUP or ".." in partes or Path(nome).is_absolute():
                continue
            destino = DADOS / Path(*partes)
            destino.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(destino, "wb") as dst:
                shutil.copyfileobj(src, dst)
            n += 1
    return n
