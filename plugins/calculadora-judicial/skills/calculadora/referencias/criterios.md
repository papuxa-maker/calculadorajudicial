# Regras aplicadas e critérios que o advogado pode trocar

O motor aplica sozinho os cortes legais de cada área. Os itens de `criterios` só mudam escolhas onde a lei
ou os tribunais admitem mais de um caminho. Todas as escolhas aparecem na memória de cálculo.

## O que cada área faz automaticamente

- **Cível** — até 29/08/2024 (sem pró-rata, agosto/2024 inteiro, como nas tabelas dos tribunais; com pró-rata, dividido por dias): tabela do tribunal (padrão TJGO = INPC) + juros de 1% a.m. (0,5% a.m. antes
  de 11/01/2003, CC/1916); desde 30/08/2024 (Lei 14.905/2024): IPCA + taxa legal (Res. CMN 5.171/2024,
  juros simples). Juros desde a citação (contratual), do evento (extracontratual, Súmula 54) ou do
  vencimento quando a obrigação vence depois da citação; nunca antes do vencimento. Dano moral: correção
  desde o arbitramento (Súmula 362).
- **Trabalhista** (ADC 58/STF) — fase pré-judicial: IPCA-E + juros pela TR; desde o ajuizamento: SELIC
  (sem outro índice); desde 30/08/2024: IPCA + taxa legal em qualquer fase (TST, SBDI-1).
- **Tributário federal** (Lei 9.250/95, art. 39, §4º) — SELIC do mês seguinte ao pagamento indevido até o
  mês anterior ao cálculo + 1% no mês do cálculo. Custas e honorários seguem a regra da Fazenda.
- **Fazenda Pública** — até 08/12/2021: IPCA-E + juros da poupança desde a citação (Temas 810/905);
  desde 09/12/2021: SELIC (EC 113/2021). Sem multa do art. 523 (CPC, art. 534, §2º); honorários do
  cumprimento só com impugnação (STJ, Tema 1.190).
- **Em todas** — pagamentos imputados primeiro nos juros (CC, art. 354); multa e honorários % sobre a
  condenação sem descontar pagamentos; deflação não reduz o principal abaixo do valor original (STJ,
  REsp 1.265.580); mês sem índice: o cálculo para (ou usa projeção, se o advogado autorizar).

## Critérios (`"criterios": {...}`)

| Critério | Padrão | Alternativas e quando usar |
| --- | --- | --- |
| `juros_fixados_no_titulo` | `false` | `true` quando o título fixou os juros de 1% a.m. (a memória diz isso e não traz o aviso do Tema 1.368) |
| `juros_civel_pre_14905` | `um_por_cento` | `selic_tema_1368` — STJ, Tema 1.368 (29/10/2025): título omisso → SELIC antes da Lei 14.905. **Pergunte o que o título diz.** |
| `indice_correcao_civel` | `TJGO` | a sigla do tribunal (`TJMG`, `TJSC`, `TJDFT`... — lista no comando `tabelas`), `TJSP`, `INPC`, `IPCA`, `IPCA_E`, `IPCA_15`, `IGPM`, `JF` (tabela da Justiça Federal), `JF_PREV`, ou o código de uma tabela importada — use o que o título ou o tribunal manda. TJGO = INPC até 08/2024 (Prov. CGJ-GO 06/2019) e IPCA desde 09/2024, montada pela calculadora com os índices do Banco Central (não é a tabela publicada pelo tribunal). |
| `indice_correcao_pos_14905` | `IPCA` | `IPCA_15` (usado pelo TJSP e pelo CJF) |
| `responsabilidade` | `contratual` | `extracontratual` (ato ilícito: juros do evento) |
| `termo_inicial_juros` | padrão da área | `citacao`, `vencimento` (dívida líquida e com termo: mora ex re, CC, art. 397), `evento`, `transito` — quando o título fixar ou a dívida tiver vencimento certo |
| `pro_rata` | `false` | `true`: dias corridos nos meses das pontas |
| `trabalhista_pre_judicial_ate_corte` | `true` | `false`: IPCA-E + TR até o ajuizamento mesmo depois de 30/08/2024 |
| `fazenda_regime_pos_ec136` | `selic` | `ipca15_taxa_legal`: IPCA-15 + taxa legal desde 01/09/2025 (EC 136/2025, Tema 1.419, Manual CJF 2026) |
| `indice_correcao_fazenda` | `IPCA_E` | `INPC` (benefício previdenciário) |
| `houve_impugnacao` | `false` | `true` (Fazenda: honorários do cumprimento) |
| `capitalizacao_selic` | `simples` | `composta` |
| `selic_como` | `juros` | `correcao` (não aceita pagamentos nem arredondamento CJF) |
| `base_multa_523` | `decurso` | `data_calculo` |
| `imputacao` | `juros_primeiro` | `capital_primeiro`, `proporcional` |
| `arredondamento` | `exato` | `cjf`: truncamentos do Manual de Cálculos da JF (bate no centavo com os exemplos do Manual) |
| `preservar_valor_nominal` | `true` | `false` (deflação reduz o principal) |
| `permitir_projecao` | `false` | `true`: mês ainda não divulgado usa o último índice disponível, marcado como projeção na memória. Só com autorização do advogado. |

## Convenções que o motor não segue (diga se o advogado comparar)

- Manual CJF, devedor privado: SELIC só a partir do mês seguinte à citação e, em ago/2024, somada à taxa
  legal — o motor não soma SELIC com outro índice no mesmo mês (diferença de centavos a dezenas de reais).
- Planilha do TJSP: no modo "mês cheio" desloca a taxa legal um mês; no "pro rata" usa ano de 360 dias e
  não faz pró-rata na correção. O motor segue a Res. CMN 5.171/2024 e a Calculadora do Cidadão.
- Calculadora do Cidadão: arredonda o fator a 7 casas; o motor usa 10 (diferença só acima de ~R$ 250 mil).
