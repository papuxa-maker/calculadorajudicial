# Pedido de cálculo (JSON)

Arquivo que você escreve em `${CLAUDE_PLUGIN_DATA}/pedidos/<nome>.json` e passa para `conferir` e
`calcular`. Campos que não se aplicam podem ficar de fora. Valores no formato brasileiro ("10.000,00" ou
"10.000" = dez mil; "1.234,56"); datas sempre dd/mm/aaaa. `modelo --area <área>` imprime um esqueleto.

```json
{
  "processo": {"numero_cnj": "0000000-00.0000.8.09.0000", "vara": "1ª Vara Cível", "tipo": "cumprimento"},
  "area": "civel",
  "data_calculo": "01/10/2026",
  "datas": {"citacao": "15/03/2023", "ajuizamento": "10/02/2023", "transito": "01/03/2024",
            "intimacao_523": "05/08/2026", "fim_prazo_523": null},
  "parcelas": [
    {"descricao": "Aluguel jan/2023", "valor": "10.000,00", "data_base": "10/01/2023", "natureza": "principal"}
  ],
  "abatimentos": [{"data": "01/04/2025", "valor": "5.000,00", "descricao": "depósito judicial"}],
  "acessorios": {"multa_contratual_pct": "10", "honorarios_pct": "10", "multa_523": true, "honorarios_cumprimento": true},
  "criterios": {}
}
```

## Campos

| Campo | Conteúdo |
| --- | --- |
| `processo` | opcional: `numero_cnj`, `vara`, `tipo` (texto livre, ex.: "cumprimento de sentença"; aparece na conferência e no relatório). Sem número de processo, o nome do arquivo leva o `tipo` informado ou, sem ele, a descrição da primeira parcela — use textos que identifiquem o caso. Não use nomes de partes. |
| `area` | `civel`, `trabalhista`, `tributario` (restituição/compensação de tributo federal) ou `fazenda` (condenação contra a Fazenda Pública) |
| `data_calculo` | até quando atualizar (padrão: hoje). Os índices usados vão até o mês anterior a ela. |
| `datas.citacao` | cível e Fazenda: início dos juros (padrão). Obrigatória nessas áreas, salvo termo inicial diferente. |
| `datas.ajuizamento` | trabalhista: obrigatória (ADC 58: IPCA-E + TR antes, SELIC depois). |
| `datas.transito` | honorários em quantia certa (juros desde o trânsito, CPC art. 85 §16); termo "transito". |
| `datas.intimacao_523` / `fim_prazo_523` | art. 523: o fim dos 15 dias úteis é estimado pela intimação (feriados nacionais e recesso); informe `fim_prazo_523` se souber a data exata. |
| `parcelas[]` | `descricao`, `valor` (> 0), `data_base` (vencimento, evento danoso ou arbitramento), `natureza` (`principal` — padrão se omitida —, `dano_moral`, `custas`, `honorarios`), `data_evento` (dano moral extracontratual: evento danoso). Descrições diferentes para parcelas diferentes ajudam a ler o relatório. |
| `abatimentos[]` | pagamentos já feitos: `data`, `valor`, `descricao`. Imputados primeiro nos juros (CC, art. 354), salvo critério `imputacao`. |
| `acessorios.multa_contratual_pct` | % sobre principal corrigido + juros. |
| `acessorios.honorarios_pct` | % sobre principal corrigido + juros + multa contratual (os pagamentos não reduzem essa base). |
| `acessorios.honorarios_fixos` | `{"valor": "2.000,00", "data": "01/03/2024"}` — quantia certa, corrigida desde a fixação (exige `datas.transito`). Não use junto com `honorarios_pct`. |
| `acessorios.multa_523`, `honorarios_cumprimento` | `true`/`false` — 10% + 10% do art. 523 (exigem `intimacao_523` ou `fim_prazo_523`). Não se aplicam à Fazenda, ao tributário nem ao trabalhista: o motor não calcula e explica o motivo nos avisos. |
| `criterios` | só o que o advogado escolher diferente do padrão (lista em `criterios.md`). |
| `periodos` | só quando o título fixa índices/juros próprios (ver abaixo). Desliga os cortes legais automáticos. |

## Exemplos por situação

**Cível contratual, dois aluguéis, um depósito e honorários de 10%**
```json
{"area": "civel", "data_calculo": "01/10/2026", "datas": {"citacao": "15/03/2023"},
 "parcelas": [{"descricao": "Aluguel jan/2023", "valor": "10.000,00", "data_base": "10/01/2023"},
              {"descricao": "Aluguel fev/2023", "valor": "10.000,00", "data_base": "10/02/2023"}],
 "abatimentos": [{"data": "01/04/2025", "valor": "5.000,00", "descricao": "depósito judicial"}],
 "acessorios": {"honorarios_pct": "10"}}
```

**Dano moral extracontratual** (juros desde o evento — Súmula 54; correção desde o arbitramento — Súmula 362)
```json
{"area": "civel", "datas": {"citacao": "20/05/2022"},
 "parcelas": [{"descricao": "Dano moral", "valor": "15.000,00", "data_base": "10/04/2024", "natureza": "dano_moral",
               "data_evento": "03/01/2022"}],
 "criterios": {"responsabilidade": "extracontratual"}}
```
Na responsabilidade contratual (padrão), os juros do dano moral correm da citação.

**Cumprimento de sentença com multa e honorários do art. 523**
```json
{"area": "civel", "datas": {"citacao": "15/03/2023", "intimacao_523": "05/08/2026"},
 "parcelas": [{"descricao": "Condenação", "valor": "50.000,00", "data_base": "10/01/2023"}],
 "acessorios": {"multa_523": true, "honorarios_cumprimento": true}}
```

**Trabalhista** (ADC 58: IPCA-E + TR até o ajuizamento; SELIC depois; desde 30/08/2024 IPCA + taxa legal)
```json
{"area": "trabalhista", "datas": {"ajuizamento": "10/06/2021"},
 "parcelas": [{"descricao": "Horas extras 2019", "valor": "8.000,00", "data_base": "05/02/2019"}]}
```

**Tributário federal — restituição** (SELIC do mês seguinte ao pagamento + 1% no mês do cálculo)
```json
{"area": "tributario", "parcelas": [{"descricao": "IRPF pago a maior", "valor": "3.200,00", "data_base": "30/04/2020"}]}
```
A data-base é a data do pagamento indevido.

**Fazenda Pública** (IPCA-E + juros da poupança até 08/12/2021; SELIC desde 09/12/2021)
```json
{"area": "fazenda", "datas": {"citacao": "01/02/2021"},
 "parcelas": [{"descricao": "Diferenças 2020", "valor": "1.000,00", "data_base": "01/01/2020"}],
 "criterios": {"houve_impugnacao": false}}
```
Com impugnação rejeitada, honorários do cumprimento só pelo art. 85, §3º (informe como honorários fixos).

**Título com critérios próprios** (ex.: "IPCA + juros de 1% a.m. desde o vencimento até o pagamento")
```json
{"area": "civel", "data_calculo": "01/10/2026",
 "parcelas": [{"descricao": "Nota promissória", "valor": "7.500,00", "data_base": "10/01/2023"}],
 "periodos": {"*": [{"de": "10/01/2023", "ate": "01/10/2026", "indice_correcao": "IPCA",
                     "tipo_juros": "simples", "taxa_juros_am": "1"}]}}
```
Chave = descrição da parcela, ou `"*"` para todas. Com `periodos`, a citação não é necessária: os juros
correm conforme o `tipo_juros` de cada período, desde o seu `de`. Use `ate` igual à data do cálculo no último
período (como o resto do motor, os índices vão até o mês anterior à data do cálculo). Campos do período:

| Campo | Valores |
| --- | --- |
| `de`, `ate` | datas (ate inclusive; o último período pode ir até a data do cálculo) |
| `indice_correcao` | `TJGO`, `TJSP`, `INPC`, `IPCA`, `IPCA_E`, `IPCA_15`, `IGPM`, `JF`, `JF_PREV`, `SELIC` ou vazio (sem correção) |
| `tipo_juros` | `nenhum`, `simples` ou `composto` (com `taxa_juros_am`, % ao mês), `taxa_legal`, `indice` (com `indice_juros`: `TAXA_LEGAL`, `TR`, `JUROS_POUPANCA`), `embutido` (SELIC: correção e juros juntos — use com `indice_correcao: "SELIC"`) |
| `capitalizacao_correcao` | `composta` (padrão) ou `simples` (soma das taxas). Com `SELIC`, o padrão é `simples` (soma das taxas mensais, como a Receita e o Manual da JF — o mesmo dos cortes automáticos); se o título mandar capitalizar, informe `composta` |
| `capitalizacao_juros` | vazio (padrão do índice), `simples` ou `composta` |
| `acrescimo_final_pct` | ex. `"1"`: 1% no mês do cálculo (restituição tributária) |
| `selic_sobre_juros` | `true` (padrão) / `false`: a SELIC incide também sobre juros já vencidos |

A SELIC nunca se soma a outro índice nem a juros no mesmo período: para SELIC use `tipo_juros: "embutido"`.
Com honorários fixos e `periodos`, informe também os períodos da parcela "Honorários fixados em quantia certa" (ou use `"*"`).

**Trabalhista com honorários sucumbenciais** (art. 791-A da CLT)
```json
{"area": "trabalhista", "datas": {"ajuizamento": "10/06/2021"},
 "parcelas": [{"descricao": "Verbas rescisórias", "valor": "8.000,00", "data_base": "05/02/2019"}],
 "acessorios": {"honorarios_pct": "10"}}
```
Os valores devem ser os créditos já apurados (o motor atualiza; não calcula verbas, INSS, IRRF nem FGTS).
