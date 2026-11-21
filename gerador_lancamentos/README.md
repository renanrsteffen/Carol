# Gerador de Lançamentos Contábeis

Transforma **extratos bancários** (e, se houver, o **relatório financeiro** do cliente) em lançamentos contábeis prontos para importar no **SIGER**, usando o **razão** para saber quais notas de clientes e fornecedores estão em aberto e como o escritório costuma lançar cada tipo de movimento.

## Como instalar e abrir

1. Instale o Python 3.10 ou superior (https://www.python.org — no Windows marque **"Add Python to PATH"**).
2. Copie a pasta `gerador_lancamentos` para o computador.
3. Dê dois cliques em **`iniciar.bat`** (Windows) ou rode `./iniciar.sh` (Mac/Linux).
   Na primeira vez ele instala o que precisa (alguns minutos); depois abre direto no navegador.

O programa roda no próprio computador: nenhum arquivo do cliente é enviado para a internet.

## Passo a passo

| Etapa | O que fazer |
|---|---|
| **Cliente** (barra lateral) | Crie ou escolha o cliente. Informe o número e a descrição do lote. |
| **1 · Arquivos** | Envie o **razão** (Excel/CSV), os **extratos** (OFX, Excel, CSV ou PDF) e, se tiver, o **relatório financeiro**. Escolha a conta contábil de cada extrato (fica gravada para os próximos meses). Clique em **Gerar lançamentos**. |
| **2 · Lançamentos** | Revise. ✅ OK · 🟡 Revisar · 🔴 Pendente · 📗 Já consta no razão. Troque a contrapartida, ajuste o histórico, marque **Memorizar** no que corrigir. |
| **3 · Notas em aberto** | Notas de clientes/fornecedores em aberto no razão e o saldo que fica depois dos extratos. |
| **4 · Exportar** | Conferência extrato × lançamentos por banco, validação e download do **arquivo SIGER** e da **planilha de conferência**. |
| **⚙️ Configurações** | Contas de juros/descontos, regras, modelos de histórico, tipo das contas e memória do cliente. |

**Dica:** envie o razão com **alguns meses anteriores** (ou desde o início do ano). Quanto mais histórico, melhor ele conhece as notas em aberto e as contas que você usa.

## Como o programa decide cada lançamento

Para cada linha do extrato, nesta ordem:

1. **Já está no razão?** Mesma conta de banco, valor e data (±1 dia) → marcada como *Já lançado* e fica fora da exportação (evita duplicidade). Esses lançamentos também ensinam o programa.
2. **Memória do cliente**: o que você corrigiu e marcou *Memorizar* em meses anteriores.
3. **Relatório financeiro**: casa por data e valor (inclusive um pagamento em lote que soma vários títulos) e usa o favorecido e o nº da nota do relatório. Um pagamento em lote vira um lançamento por nota.
4. **Cliente/fornecedor**: identifica pelo CNPJ ou pelo nome no extrato e procura a nota em aberto:
   - nota com o mesmo valor; ou
   - soma de até 5 notas do mesmo cliente/fornecedor; ou
   - nota com diferença até a tolerância → separa **juros/multa** (se a conta estiver configurada) ou marca para revisar (desconto/pagamento parcial).
   A nota usada é baixada, então não é vinculada duas vezes.
5. **Regras de categoria**: tarifa bancária, IOF, juros bancários, aplicação, resgate, rendimento, telefone/internet, energia, água, DAS, DARF, INSS, FGTS, ICMS/ISS, folha, empréstimo, cartão. **A conta de cada regra é descoberta no razão** (a contrapartida que você mais usou em históricos daquele tipo). Também pode fixar a conta em Configurações.
6. **Semelhança com o razão**: compara o texto do extrato com os históricos já lançados.
7. **Nota em aberto com valor único**: se só uma nota em aberto tem aquele valor, sugere e marca para revisar.
8. Sem nada disso → **Pendente** para você escolher a conta.

### Históricos

Formais e em maiúsculas, com o número da nota quando houver. Padrões (editáveis em Configurações):

- `VLR. PAGO REF. NF 1002 - FORNECEDORA GAMA INDUSTRIA LTDA`
- `VLR. RECEBIDO REF. NF 89 - ALFA COMERCIO DE ALIMENTOS LTDA`
- `VLR. REF. TARIFA BANCÁRIA CONF. EXTRATO`
- `VLR. REF. APLICAÇÃO FINANCEIRA`

## Arquivos aceitos

- **Razão**: Excel ou CSV exportado do sistema. Aceita o razão por conta (linha `Conta: 123 - 1.1.1.02.001 - NOME`, seguida dos movimentos) ou com coluna de conta em cada linha. As colunas são encontradas pelo cabeçalho (*Data, Histórico, Débito, Crédito, Lote, Lançamento*). A contrapartida vem de `Contrapartida:` no histórico, de uma coluna própria ou do par lote + lançamento.
- **Plano de contas** (opcional): colunas código, classificação e descrição. Ajuda a reconhecer clientes (1.1.2…), fornecedores (2.1.1…) e bancos.
- **Extratos**: **OFX** (mais confiável), Excel/CSV (*Data, Histórico, Saída/Entrada* ou *Valor* com D/C) e **PDF** com texto (Banco do Brasil, Itaú e similares; PDF escaneado não funciona). Sempre confira o total na aba Exportar.
- **Relatório financeiro** (opcional): contas pagas/recebidas com *Data do pagamento, Fornecedor/Cliente, Documento/NF, Valor pago*. Se o nome do arquivo tiver "pagar"/"pagas" ou "receber" o tipo é reconhecido automaticamente.

## Arquivo de importação SIGER

Aba `Plan1`, 26 colunas do modelo de importação, uma linha por lançamento:
partida (G) = banco; contrapartida (U) = conta classificada; **D** para entrada e **C** para saída no banco; lote único; lançamento sequencial; data em texto `dd/mm/aaaa`; valor sem separador de milhar; nº da nota em *Número do documento*.

## Para quem for mexer no código

```
app.py                  interface (Streamlit)
core/razao.py           leitura do razão e do plano; apuração das notas em aberto
core/extratos.py        leitura de OFX, Excel/CSV e PDF
core/financeiro.py      leitura do relatório financeiro
core/classificador.py   motor de classificação e vínculo com notas
core/perfil.py          perfil do cliente, regras padrão, modelos de histórico, memória
core/exportar.py        arquivo SIGER e planilha de conferência
exemplos/               gerador de dados fictícios para teste
tests/                  testes automáticos (python -m pytest)
```

Os perfis dos clientes ficam em `clientes/*.json` (não vão para o Git).
