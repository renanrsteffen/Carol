# Plano — Lançamentos a partir dos comprovantes de pagamento do e-CAC

> **Situação (22/11/2026):** etapas 1 a 6 implementadas e testadas com PDFs de exemplo. Falta validar o leitor com PDFs reais do e-CAC.
>
> **Decisões do escritório:** DAS tudo na conta do Simples · impostos normais já vão à despesa na apuração, então o pagamento baixa só o passivo · taxas e impostos esporádicos direto na despesa · multa e juros em contas próprias · comprovante sem extrato é lançado e marcado para conferir.

## Objetivo

Ler os comprovantes de pagamento baixados do e-CAC (DARF, DAS, DARF da DCTFWeb e GPS) e gerar os lançamentos de pagamento de impostos:

- **conta do banco** pela qual foi pago (banco e agência do comprovante);
- **principal** de cada tributo, separado pelo código de receita;
- **multa** e **juros**, separados do principal;
- histórico formal com o tributo, o período de apuração e o nº do documento.

Os lançamentos entram na mesma fila de revisão dos extratos e saem no mesmo arquivo do SIGER.

## Entrada

- **Comprovante de Arrecadação** (um por pagamento) ou PDF com vários comprovantes juntos.
- **Relatório de pagamentos** (lista do período), quando vier junto. Serve para conferir se nenhum comprovante faltou.

Dados lidos de cada comprovante:

| Campo | Uso |
|---|---|
| CNPJ / razão social | confere se o comprovante é do cliente certo |
| Tipo (DARF, DAS, DCTFWeb, GPS) | escolhe as regras de lançamento |
| Nº do documento | vai no histórico e no "nº do documento" do SIGER; evita lançar duas vezes |
| Período de apuração e vencimento | histórico e busca da provisão no razão |
| Data de arrecadação | data do lançamento |
| Banco / agência | conta contábil do banco |
| Composição: código de receita, denominação, principal, multa, juros, total | uma linha por tributo, mais multa e juros |

## Como os lançamentos serão montados

Exemplo: DARF de 1.150,00 pago pelo Banco do Brasil, composto de COFINS 800,00 + PIS 173,33 + multa 120,00 + juros 56,67.

| D | C | Valor | Histórico |
|---|---|---|---|
| COFINS a recolher | Banco do Brasil | 800,00 | VLR. PAGO REF. COFINS PA 10/2026 - DOC 07.xx |
| PIS a recolher | Banco do Brasil | 173,33 | VLR. PAGO REF. PIS PA 10/2026 - DOC 07.xx |
| Multas fiscais | Banco do Brasil | 120,00 | VLR. MULTA S/ PAGTO COFINS/PIS PA 10/2026 |
| Juros s/ tributos | Banco do Brasil | 56,67 | VLR. JUROS S/ PAGTO COFINS/PIS PA 10/2026 |

**Conta de cada tributo**
1. A **tabela de códigos de receita** (código → tributo: IRPJ, CSLL, PIS, COFINS, IRRF, CSRF retido, INSS, Simples Nacional, parcelamentos…) vem pré-carregada com os códigos mais usados e pode ser editada.
2. A **conta contábil de cada tributo** é aprendida do razão: a conta "… a recolher" onde o imposto foi provisionado e a conta usada em pagamentos anteriores. Uma vez confirmada, fica gravada no cliente.
3. **Sem provisão no razão** (imposto não apropriado no mês): o lançamento é sugerido contra a despesa do tributo e entra em 🟡 *conferir*.

**Conferência com o razão**
- O principal de cada tributo é comparado com o saldo provisionado na conta "a recolher" daquele período de apuração. Diferença → 🟡 "Valor ≠ provisão".
- **Duplicidade:** pagamento já lançado no razão (mesma data, valor e banco) → 📗 "Já no razão", fora da exportação.

## Integração com os extratos

O mesmo pagamento costuma aparecer no extrato só como "DARF" ou "PAGTO TRIBUTOS", com o valor total:

1. **Comprovante e extrato do mesmo banco:** o comprovante **substitui** a linha genérica do extrato, casando por data e valor total. O detalhamento por tributo sai do comprovante e o banco continua batendo com o extrato.
2. **Vários DARFs pagos num único débito:** a soma dos comprovantes do dia é casada com o débito.
3. **Comprovante sem linha no extrato** (outro banco, extrato não enviado): o lançamento é gerado do mesmo jeito, marcado 🟡 "Sem extrato para conferir".
4. **Débito de imposto no extrato sem comprovante:** continua com a regra atual (DARF/DAS) e 🟡 "Sem comprovante".
5. **Banco do comprovante sem conta definida:** 🔴 pede a conta uma vez e grava no cliente (banco + agência → conta).

## Casos específicos

| Caso | Tratamento proposto |
|---|---|
| **DAS (Simples Nacional)** | Um lançamento pelo total contra "Simples a recolher" ou despesa (o que o razão mostrar), ou por tributo, conforme a escolha do escritório |
| **DCTFWeb / GPS** | INSS separado em patronal, segurados e terceiros se a composição trouxer; senão, pelo total contra "INSS a recolher" |
| **Retenções (IRRF, CSRF 5952)** | Baixa a conta de retenções a recolher do período |
| **Parcelamentos** | Baixa a conta de parcelamento (passivo); juros da parcela separados |
| **Pagamento com atraso** | Multa e juros sempre separados do principal |
| **Comprovante de outro CNPJ** | 🔴 bloqueado ("CNPJ diferente do cliente") |

## Na tela

- Novo campo na aba **1 · Arquivos**: *Comprovantes e-CAC (PDF)*, com quantos comprovantes foram lidos e o total pago por banco.
- Na **fila**, o movimento do extrato aparece com "+N" linhas (tributos, multa e juros). O painel mostra a composição do comprovante.
- Na **conferência**: nova aba "Impostos pagos", com tributo, período de apuração, principal, multa, juros, banco e provisão no razão.
- Em **Configurações**: tabela de códigos de receita e conta de cada tributo, de multas e de juros.

## Etapas

| # | Etapa | Depende de |
|---|---|---|
| 1 | Leitor dos PDFs do e-CAC (comprovante individual e relatório), com testes nos seus PDFs reais | PDFs de exemplo |
| 2 | Tabela de códigos de receita + contas por tributo (aprendidas do razão e editáveis) | plano de contas / razão |
| 3 | Geração dos lançamentos (principal, multa, juros) com histórico | 1, 2 |
| 4 | Casamento com o extrato (substituir a linha genérica, soma de vários DARFs) e checagem de duplicidade no razão | 3 |
| 5 | Conferência com a provisão no razão | 2 |
| 6 | Tela, conferência e testes de ponta a ponta com um mês real | 1–5 |

## O que preciso de vocês

1. **PDFs reais do e-CAC**, de preferência de um cliente com:
   - DARF com mais de um código (ex.: PIS + COFINS),
   - um pagamento em atraso (multa e juros),
   - DAS, DARF da DCTFWeb ou GPS,
   - o relatório/lista de pagamentos do período, se vocês costumam baixar.
2. O **razão do mesmo período** (com as contas de impostos a recolher) e o **extrato do banco** em que foram pagos.
3. Decisões do escritório:
   - Principal contra **"… a recolher"** sempre que houver provisão, e contra despesa quando não houver? (proposta: sim)
   - Multa e juros: **uma conta de multas e uma de juros** para todos os tributos, ou uma por tributo?
   - DAS: lançar **pelo total** ou **por tributo**?
   - Comprovante de banco sem extrato: **gerar o lançamento** mesmo assim (proposta: sim, como 🟡)?

## Riscos

- **Layout do PDF:** a Receita muda o layout de vez em quando. O leitor procura os campos pelos rótulos e não por posição, e os testes com PDFs reais acusam se algo deixar de ser lido.
- **PDF escaneado (imagem):** não dá para ler. O comprovante baixado direto do e-CAC é PDF de texto, então funciona.
- **Códigos de receita fora da tabela:** a linha fica 🔴 pedindo o tributo/conta uma vez, e o código passa a ser conhecido para aquele cliente.
