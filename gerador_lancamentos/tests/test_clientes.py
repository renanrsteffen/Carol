import importlib

from openpyxl import Workbook


def test_importa_relacao_de_clientes(tmp_path, monkeypatch):
    monkeypatch.setenv("GERADOR_DADOS", str(tmp_path / "dados"))
    import core.armazenamento as arm
    importlib.reload(arm)
    import core.perfil as pf
    importlib.reload(pf)
    import core.clientes as cl
    importlib.reload(cl)
    wb = Workbook(); ws = wb.active
    ws.append(["id", "razao_social", "nome_fantasia", "sigla", "cnpj", "tipo_cliente", "cidade"])
    ws.append(["1", "EMPRESA ALFA LTDA", None, "ALF", "12.345.678/0001-90", "simples_nacional", "SAPIRANGA"])
    ws.append(["2", "JOSE DA SILVA", None, "JDS", "123.456.789-09", "pessoa_fisica", "CANOAS"])
    f = tmp_path / "clientes.xlsx"; wb.save(f)
    pf.Perfil(nome="Alfa (criado à mão)", cnpj="12345678000190", lote="9").salvar()
    lista = cl.ler_relacao_clientes(f)
    assert [c["sigla"] for c in lista] == ["ALF", "JDS"]
    assert cl.importar_clientes(lista) == (1, 1)
    assert cl.importar_clientes(lista) == (0, 0)          # repetir não duplica
    alfa = cl.cliente_por_cnpj("12.345.678/0001-90")
    assert alfa.nome == "Alfa (criado à mão)" and alfa.sigla == "ALF" and alfa.lote == "9"   # completa sem apagar
    assert sorted(p.rotulo for p in pf.Perfil.todos()) == ["ALF · Alfa (criado à mão)", "JDS · JOSE DA SILVA"]


def test_cnpj_da_empresa_no_cabecalho_do_razao(tmp_path):
    from core.razao import Razao
    wb = Workbook(); ws = wb.active
    ws.append(["Empresa:EMPRESA ALFA LTDA  Sigla:ALF    CNPJ:12.345.678/0001-90 IE:000"])
    ws.append(["LOTE", "LANCTO", "REG", "HISTORICO", "N.DOCTO", "DATA", "DEBITO", "CREDITO"])
    f = tmp_path / "r.xlsx"; wb.save(f)
    r = Razao.carregar([f])
    assert r.cnpj_empresa == "12345678000190" and r.nome_empresa == "EMPRESA ALFA LTDA"
