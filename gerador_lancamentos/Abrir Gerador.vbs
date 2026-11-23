' Abre o Gerador de Lancamentos sem deixar a janela preta (CMD) aberta.
' Primeira vez (ou depois de uma atualizacao que muda as bibliotecas): mostra a janela da instalacao.
' Se o programa ja estiver aberto, so abre o navegador.
Option Explicit
Dim sh, fso, pasta, url, ambiente, py, precisaInstalar, i, q
q = Chr(34)
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
pasta = fso.GetParentFolderName(WScript.ScriptFullName)
url = "http://localhost:8501"

If EstaRodando() Then
  sh.Run url
  WScript.Quit
End If

ambiente = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\GeradorLancamentos\venv"
py = ambiente & "\Scripts\python.exe"
precisaInstalar = Not fso.FileExists(py)
If Not precisaInstalar Then
  precisaInstalar = (Ler(pasta & "\requirements.txt") <> Ler(ambiente & "\requirements.txt"))
End If

If precisaInstalar Then
  sh.Run "cmd /c " & q & q & pasta & "\iniciar.bat" & q & q, 1, False
  WScript.Quit
End If

sh.Run "cmd /c " & q & q & pasta & "\iniciar.bat" & q & " oculto" & q, 0, False
For i = 1 To 90
  WScript.Sleep 1000
  If EstaRodando() Then
    sh.Run url
    WScript.Quit
  End If
Next
MsgBox "O programa nao abriu. De dois cliques em iniciar.bat para ver a mensagem de erro.", vbExclamation, "Gerador de Lancamentos"

Function EstaRodando()
  Dim http
  EstaRodando = False
  On Error Resume Next
  Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  http.setTimeouts 1000, 1000, 1000, 1000
  http.Open "GET", url & "/_stcore/health", False
  http.Send
  If Err.Number = 0 Then
    If http.Status = 200 Then EstaRodando = True
  End If
  Err.Clear
  On Error GoTo 0
End Function

Function Ler(caminho)
  Dim f
  Ler = ""
  If fso.FileExists(caminho) Then
    Set f = fso.OpenTextFile(caminho, 1)
    If Not f.AtEndOfStream Then Ler = f.ReadAll
    f.Close
  End If
End Function
