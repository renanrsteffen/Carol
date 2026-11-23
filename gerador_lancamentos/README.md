# Gerador de Lançamentos Contábeis

Transforma **extratos bancários** (e, se houver, o **relatório financeiro** do cliente) em lançamentos contábeis prontos para importar no **SIGER**, usando o **razão** para saber quais notas de clientes e fornecedores estão em aberto e como o escritório costuma lançar cada tipo de movimento.

## Como instalar e abrir

1. Instale o Python 3.12 (3.11 ou superior) (https://www.python.org — no Windows marque **"Add Python to PATH"**).
2. Copie a pasta `gerador_lancamentos` para o computador.
3. Dê dois cliques em **`iniciar.bat`** (Windows) ou rode `./iniciar.sh` (Mac/Linux). A pasta pode ficar no computador ou na rede (`\\servidor\...`); as bibliotecas são instaladas em cada computador e os dados ficam na pasta do programa.
   Na primeira vez ele instala o que precisa (alguns minutos); depois abre direto no navegador.

**Sem a janela preta:** depois da primeira instalação, abra pelo **`Abrir Gerador.vbs`** (crie um atalho dele na área de trabalho). Ele inicia o programa escondido e abre o navegador; se o programa já estiver aberto, só abre o navegador. Para encerrar, use **⏻ Fechar programa** na barra lateral. Se algo der errado, o `iniciar.bat` continua funcionando e mostra a mensagem de erro.

O programa roda no próprio computador: nenhum arquivo do cliente é enviado para a internet, e depois de instalado funciona sem internet. As versões das bibliotecas estão fixadas em `requirements.txt`, então atualizações externas não quebram o programa.

## Onde ficam os dados e backup

Tudo fica na pasta **`dados/`**, dentro da pasta do programa:

| Pasta | Conteúdo |
|---|---|
| `dados/clientes` | perfil de cada cliente: contas, regras, modelos de histórico e tudo que o programa aprendeu |
| `dados/trabalhos` | o trabalho de cada mês, **salvo automaticamente a cada alteração**; ao escolher o cliente, o último trabalho reabre sozinho |
| `dados/exportados` | cópia de cada arquivo baixado para o SIGER e de cada conferência |
| `dados/backups` | backups compactados (.zip) |

- **Backup automático** uma vez por dia, ao abrir o programa (mantém os últimos 60; ajustável).
- **Cópia extra**: na aba 🛟 Backup, informe uma pasta do OneDrive/Google Drive ou um pendrive; cada backup também é copiado para lá. **Recomendado** — se o computador quebrar, os dados estão lá.
- **Restaurar**: na mesma aba, envie o .zip. Antes de restaurar, o programa guarda um backup do estado atual.
- Gravação segura: cada arquivo é gravado primeiro em um temporário e só depois substitui o original, que vira `.bak`. Se um arquivo corromper (queda de energia), o programa usa o `.bak` automaticamente.
- Para levar o programa para outro computador: copie a pasta inteira (incluindo `dados/`), ou instale de novo e restaure um backup.

## Passo a passo

| Etapa | O que fazer |
|---|---|
| **Cliente** (barra lateral) | Escolha o cliente digitando a sigla ou parte do nome. Na primeira vez, use **📥 Importar relação de clientes** (Excel com razão social, sigla, CNPJ/CPF, regime e cidade) para cadastrar todos de uma vez; importar de novo só completa, não duplica. Informe o número e a descrição do lote. Se o razão enviado for de outra empresa (CNPJ do cabeçalho), o programa avisa. |
| **1 · Arquivos** | Envie o **razão** (Excel/CSV), os **extratos** (OFX, Excel, CSV ou PDF) e, se tiver, o **relatório financeiro**. Escolha a conta contábil de cada extrato (fica gravada para os próximos meses). Clique em **Gerar lançamentos**. |
| **2 · Lançamentos** | Fila de revisão. Uma linha por movimento do extrato; o que pede atenção vem primeiro, com o motivo em poucas palavras (🔴 precisa de decisão · 🟡 conferir · 🟢 pronto · 📗 já no razão · ⚪ ignorado). Clique na linha: o painel mostra o porquê, o lançamento proposto e as **outras possibilidades encontradas**; escolha, ajuste histórico/NF se quiser e **Confirmar** — a fila segue para o próximo. **Desfazer** volta à sugestão do programa. |
| **Comprovantes e-CAC** (opcional, na aba 1) | PDFs de DARF, DAS, DCTFWeb e GPS. Cada comprovante vira lançamentos por tributo, com multa e juros separados — veja abaixo. |
| **3 · Notas em aberto** | Notas de clientes/fornecedores em aberto no razão e o saldo que fica depois dos extratos. |
| **4 · Exportar** | Conferência extrato × lançamentos por banco, validação e download do **arquivo SIGER** e da **planilha de conferência**. |
| **⚙️ Configurações** | Contas de juros/descontos, regras, modelos de histórico, tipo das contas e memória do cliente. |

**Dica:** envie o razão com **alguns meses anteriores** (ou desde o início do ano). Quanto mais histórico, melhor ele conhece as notas em aberto e as contas que você usa.

## Como o programa decide cada lançamento

Para cada linha do extrato, nesta ordem:

1. **Já está no razão?** Mesma conta de banco, valor e data (±1 dia) → marcada como *Já lançado* e fica fora da exportação (evita duplicidade). Esses lançamentos também ensinam o programa.
2. **Memória do cliente**: o que você corrigiu e marcou *Memorizar* em meses anteriores.
3. **Relatório financeiro**: casa por data e valor (inclusive um pagamento em lote que soma vários títulos) e usa o favorecido e o nº da nota do relatório. Um pagamento em lote vira um lançamento por nota.
4. **Cliente/fornecedor**: identifica pelo CNPJ ou pelo nome no extrato e **procura o pagamento nas notas em aberto da conta dele no razão** (veja abaixo). A nota usada é baixada, então não é vinculada duas vezes; a próxima parcela da mesma nota é reconhecida como a seguinte.
5. **Regras de categoria**: tarifa bancária, IOF, juros bancários, aplicação, resgate, rendimento, telefone/internet, energia, água, DAS, DARF, INSS, FGTS, ICMS/ISS, folha, empréstimo, cartão. **A conta de cada regra é descoberta no razão** (a contrapartida que você mais usou em históricos daquele tipo). Também pode fixar a conta em Configurações.
6. **Semelhança com o razão**: compara o texto do extrato com os históricos já lançados.
7. **Busca em todos os clientes/fornecedores**: se o nome não aparece no extrato, procura o valor (nota, soma, parcela, retenção) em todas as contas. Só sugere quando uma conta se destaca; se houver empate, lista as possibilidades na observação.
8. Sem nada disso → **Pendente** para você escolher a conta.

### Como o pagamento é procurado nas notas em aberto

As notas em aberto vêm do razão: cada nota lançada na conta do cliente/fornecedor menos as baixas (pelo nº da nota no histórico; sem número, por valor igual e depois pela mais antiga). O **saldo anterior** da conta, quando o razão não traz o detalhe das notas antigas, entra como um título "saldo anterior".

Para cada recebimento/pagamento o programa testa, nesta ordem, e escolhe a hipótese mais forte (as outras aparecem na observação):

| Hipótese | Exemplo | Resultado |
|---|---|---|
| **NF citada** no extrato ou no relatório financeiro | "PIX REF NF 1002" | baixa essa nota (inteira, parcela ou parcial) |
| **Valor da nota** | saldo 2.300,00 = pago 2.300,00 | baixa a nota |
| **Soma de notas** (até 6) | 870,50 + 1.129,50 = 2.000,00 | uma linha, histórico com todas as notas |
| **Nota com impostos retidos** | 10.000,00 − IRRF 1,5% − PIS/COFINS/CSLL 4,65% = 9.385,00 | baixa a nota cheia e gera os lançamentos das retenções (IRRF, PIS, COFINS, CSLL, ISS 2–5%, INSS 11%) |
| **Parcela** (2 a 24 vezes) | nota 3.000,00, pago 1.000,00 | "PARC. 1/3"; no mês seguinte reconhece a 2/3 |
| **Nota + juros/multa** | nota 1.000,00, pago 1.021,50 | separa 21,50 de juros (se a conta estiver configurada) |
| **Nota com desconto** | nota 1.000,00, pago 990,00 | baixa a nota e lança o desconto |
| **Pagamento parcial** | nota 1.000,00, pago 427,13 | baixa 427,13; o resto continua em aberto |
| **Notas mais antigas** | valor cobre as mais antigas e parte da seguinte | baixa em ordem de data |

Notas emitidas depois da data do pagamento não são consideradas. Hipóteses menos seguras (parcela, juros, parcial, busca sem nome) ficam como **Conferir** (🟡). Quando duas contas são igualmente prováveis, o programa não escolhe: deixa 🔴 e mostra as opções para o operador decidir. As decisões do operador são reaplicadas sempre que a classificação é refeita, e as notas escolhidas não são usadas de novo em outro movimento.

### Comprovantes de pagamento do e-CAC

Envie os PDFs baixados do e-CAC (um ou vários comprovantes por arquivo). Para cada comprovante:

| Situação | Lançamento |
|---|---|
| **DAS** | total do principal na conta do **Simples Nacional** |
| **Impostos normais** (PIS, COFINS, IRPJ, CSLL, IRRF, PIS/COFINS/CSLL retidos, INSS, IPI, parcelamentos) | débito no **passivo "a recolher"** (a despesa já foi lançada na apuração), uma linha por tributo |
| **Taxas e impostos esporádicos** (códigos não reconhecidos como impostos normais) | direto na **despesa** (conta de taxas e impostos), marcado para conferir |
| **Multa e juros** | contas próprias, separados do principal |

- A **conta do banco** vem do banco/agência do comprovante. Se o cliente não tem conta daquele banco, o programa pede a conta uma vez e grava.
- O débito genérico do extrato ("PAGAMENTO DARF") é **substituído** pelo detalhe do comprovante. Vários DARFs pagos num único débito são casados pela soma.
- **Comprovante sem débito no extrato**: lançado mesmo assim, marcado 🟡 "Sem extrato para conferir" (ou "Não encontrado no extrato", se o extrato daquele banco foi enviado).
- **Conferência com a provisão**: o principal pago é comparado com o crédito lançado na conta "a recolher" no mês do período de apuração; diferença → 🟡.
- **CNPJ** de outra empresa → 🔴 bloqueado (informe o CNPJ do cliente em Configurações).
- As contas dos tributos, de multa, de juros e de taxas são procuradas no plano de contas pelo nome ("COFINS A RECOLHER", "MULTAS FISCAIS"…). Confira e ajuste em **Configurações › Impostos**. Envie o **plano de contas** para que contas sem movimento no razão também sejam encontradas.
- Histórico: `VLR. PAGO REF. COFINS PA 10/2026 - DOC 07.18.…`, `VLR. MULTA S/ PAGTO IRPJ PA 09/2026`; o nº do documento vai na coluna "Número do documento" do SIGER.
- A planilha de conferência ganha a aba **Impostos pagos** com a composição de cada comprovante e a conta usada.

> O leitor foi feito pelo layout conhecido do comprovante do e-CAC e testado com PDFs de exemplo. Deve ser validado com PDFs reais.

### Históricos

Formais e em maiúsculas, com o número da nota quando houver. Padrões (editáveis em Configurações):

- `VLR. PAGO REF. NF 1002 - FORNECEDORA GAMA INDUSTRIA LTDA`
- `VLR. RECEBIDO REF. NF 89 - ALFA COMERCIO DE ALIMENTOS LTDA`
- `VLR. REF. TARIFA BANCÁRIA CONF. EXTRATO`
- `VLR. REF. APLICAÇÃO FINANCEIRA`
- `VLR. RECEBIDO REF. NF 123 PARC. 2/3 - CLIENTE X`
- `VLR. IRRF RETIDO S/ NF 20 - CLIENTE X`

## Recursos aprendidos com o primeiro cliente real

Testado com um razão real do SIGER (dezenas de milhares de lançamentos), 5 extratos OFX, 4 extratos conciliados do sistema do cliente e a relação de pagamentos do e-CAC: **85% dos movimentos saíram prontos** e **todos os bancos fecharam com diferença zero**.

- **Razão do SIGER**: código reduzido = final da classificação (`1.1.1.02.02.00205` → 205); tipo da conta pelo nome do **Grupo** (BANCOS CONTA MOVIMENTO, CLIENTES, FORNECEDORES…); saldo devedor positivo; nº da nota na coluna N.DOCTO.
- **Extrato conciliado do sistema do cliente** (PDF "Extrato Bancário" com Conta/Custo/Histórico/Entradas/Saídas): cada linha traz a conta do plano do cliente e o histórico detalhado (NF, fornecedor, sócio). Os totais de cada dia são conferidos com o impresso.
- **Lote de cobrança**: um crédito "LIQ.COBRANCA" é casado com a soma dos recebimentos do dia no relatório do cliente (mesmo com dezenas de itens).
- **Transferências entre contas próprias**: a saída de um banco e a entrada no outro geram **um** lançamento; a outra ponta fica marcada para não duplicar.
- **Padrão do banco**: se ≥ 75% das entradas (ou saídas) de um banco vão para a mesma conta no razão (ex.: Mercado Pago → Clientes Mercado Pago), essa conta vira a sugestão; ≥ 90% sai pronto.
- **Conta genérica de clientes** (ex.: CLIENTES NO PAIS): a conta é certa; a NF do comprador é buscada pelo nome e entra como indicação.
- **De-para** (Configurações): conta do plano do cliente → conta Steffen. O programa sugere os códigos cujas linhas caem sempre na mesma regra (tarifas, IOF, aplicação…).
- **Regras prioritárias** (sócios): valem antes da busca por nome. Forma simples: `FULANO + CHINA` (todas as palavras precisam aparecer).
- **Relação de pagamentos do e-CAC** (lista sem banco): o banco vem do cruzamento com os extratos; a conta de cada **código de receita é aprendida no razão** (como o escritório lançou esse código nos meses anteriores, inclusive "total na conta do parcelamento"). Códigos novos ficam 🔴 e podem ser definidos em Configurações › Códigos de receita.

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
core/classificador.py   motor de classificação
core/busca.py           busca do pagamento nas notas em aberto (parcela, retenção, soma, juros...)
core/armazenamento.py   pasta dados/, gravação segura, trabalhos salvos e backups
core/estado.py          salvar/abrir o trabalho do mês
core/ecac.py            leitura dos comprovantes de arrecadação do e-CAC
core/tributos.py        identificação do tributo e das contas; aprendizado dos códigos de DARF no razão
core/conciliado.py      leitura do extrato conciliado do sistema do cliente (PDF)
core/perfil.py          perfil do cliente, regras padrão, modelos de histórico, memória
core/exportar.py        arquivo SIGER e planilha de conferência
exemplos/               gerador de dados fictícios para teste
tests/                  testes automáticos (python -m pytest)
```

A pasta `dados/` não vai para o Git (são dados dos clientes).
