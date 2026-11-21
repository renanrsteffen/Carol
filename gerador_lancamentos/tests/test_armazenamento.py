import importlib
import io
import zipfile

from openpyxl import Workbook  # noqa: F401


def _modulos(tmp_path, monkeypatch):
    monkeypatch.setenv("GERADOR_DADOS", str(tmp_path / "dados"))
    import core.armazenamento as arm
    importlib.reload(arm)
    import core.perfil as perfil
    importlib.reload(perfil)
    return arm, perfil


def test_perfil_salva_e_recupera_bak(tmp_path, monkeypatch):
    arm, perfil = _modulos(tmp_path, monkeypatch)
    p = perfil.Perfil(nome="Padaria São João", lote="7")
    p.salvar()
    p.lote = "8"
    p.salvar()
    assert perfil.Perfil.carregar("Padaria São João").lote == "8"
    # arquivo principal corrompido -> usa a cópia .bak
    arq = perfil.Perfil.arquivo("Padaria São João")
    arq.write_text("{corrompido", encoding="utf-8")
    assert perfil.Perfil.carregar("Padaria São João").lote == "7"
    assert perfil.Perfil.listar() == ["Padaria São João"]


def test_trabalho_ida_e_volta(tmp_path, monkeypatch):
    arm, perfil = _modulos(tmp_path, monkeypatch)
    from core.estado import competencia, desempacotar, empacotar
    from core.processo import processar
    from pathlib import Path
    import subprocess, sys
    ex = Path(__file__).resolve().parent.parent / "exemplos"
    subprocess.run([sys.executable, str(ex / "gerar_exemplos.py")], check=True, capture_output=True)
    r = processar(perfil.Perfil(nome="X"), [ex / "razao_outubro.xlsx"], [(ex / "extrato_bb_novembro.ofx", "5")])
    pacote = empacotar(lancs=r.lancamentos, movs=r.movimentos, titulos_inicio=r.titulos, titulos=r.titulos,
                       razao=r.razao, totais=r.totais_extrato, financeiro=[], editados={"abc"})
    comp = competencia(r.lancamentos)
    assert comp == "2026-11"
    arm.salvar_trabalho("X", comp, pacote)
    volta = desempacotar(arm.carregar_trabalho("X", comp))
    assert [(l.data, l.valor, l.conta_contra) for l in volta["lancs"]] == [(l.data, l.valor, l.conta_contra) for l in r.lancamentos]
    assert volta["razao"].contas["201"].tipo == "fornecedor" and volta["editados"] == {"abc"}
    assert arm.listar_trabalhos("X")[0][0] == "2026-11"


def test_backup_e_restauracao(tmp_path, monkeypatch):
    arm, perfil = _modulos(tmp_path, monkeypatch)
    extra = tmp_path / "pendrive"
    c = arm.config(); c["pasta_backup_extra"] = str(extra); arm.salvar_config(c)
    perfil.Perfil(nome="Cliente A", lote="1").salvar()
    assert arm.backup_automatico() is not None
    assert arm.backup_automatico() is None          # só um automático por dia
    z = arm.listar_backups()[0]
    assert (extra / z.name).exists()
    nomes = zipfile.ZipFile(z).namelist()
    assert "clientes/CLIENTE_A.json" in nomes and not any(n.startswith("backups") for n in nomes)
    # apaga e restaura
    perfil.Perfil.arquivo("Cliente A").unlink()
    assert arm.restaurar_backup(z.read_bytes()) >= 1
    assert perfil.Perfil.carregar("Cliente A").lote == "1"
    # zip malicioso não escreve fora da pasta
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as m:
        m.writestr("../fora.txt", "x")
        m.writestr("clientes/../../fora2.txt", "x")
    arm.restaurar_backup(buf.getvalue())
    assert not (tmp_path / "fora.txt").exists() and not (tmp_path / "fora2.txt").exists()
