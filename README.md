# Calculadora Judicial — plugin para o Claude Code

Atualização de débitos judiciais **conversando com o Claude**: cível, trabalhista, tributário federal e
Fazenda Pública, com correção monetária, juros de mora, multas e honorários, e memória de cálculo em
**PDF e Excel** pronta para juntar aos autos.

> "Atualiza R$ 10.000,00 vencidos em 10/01/2023, cível, citação em 15/03/2023, até hoje, com honorários de 10%."

O Claude pergunta o que faltar, mostra um resumo de tudo o que o cálculo vai usar (datas, índices, juros,
períodos de cada parcela) e só calcula depois que você confirmar. **Os números nunca saem da "cabeça" do
Claude**: quem calcula é um motor testado contra ferramentas oficiais (Calculadora do Cidadão do Banco
Central e Manual de Cálculos da Justiça Federal).

## O que a calculadora faz sozinha

- Aplica os cortes legais de cada área: Lei 14.905/2024 (IPCA + taxa legal desde 30/08/2024), ADC 58
  (trabalhista), Lei 9.250/95 (restituição de tributo federal), Temas 810/905 e EC 113/2021 (Fazenda),
  art. 523 do CPC (multa e honorários do cumprimento).
- **Mantém os índices em dia**: antes de cada cálculo confere o calendário de divulgação (IPCA/INPC por
  volta do dia 12; SELIC, TR, taxa legal e IPCA-15 no início do mês) e busca no Banco Central o que já
  saiu. Nenhum índice é estimado: se o do mês ainda não saiu, ela avisa e oferece as opções.
- Guarda os cálculos: "atualiza o cálculo do processo X até hoje" refaz com os mesmos critérios.

## Instalação

Precisa do [Claude Code](https://claude.com/claude-code) instalado e de internet na primeira vez.

1. No Claude Code, rode os dois comandos:

   ```
   /plugin marketplace add papuxa-maker/calculadora-judicial
   /plugin install calculadora-judicial@calculadora-judicial
   ```

2. **Ligue a atualização automática** (uma vez só): digite `/plugin` e, com as setas do teclado e Enter,
   abra a aba **Marketplaces**, escolha **calculadora-judicial** e depois **Enable auto-update**. Assim as correções e as
   mudanças de lei chegam sozinhas quando você abre o Claude Code.

3. Peça um cálculo. Na primeira vez o Claude prepara a calculadora no seu computador (1 a 3 minutos,
   com internet). Se faltar o Python 3.12, ele pede sua autorização para instalar. Se a preparação
   falhar (internet, antivírus ou firewall do escritório bloqueando downloads), ele explica a causa; é só
   tentar de novo depois.

Se preferir preencher numa tela, peça "abre a calculadora": ela abre no navegador (na primeira vez baixa
uns 400 MB de componentes e leva alguns minutos).

O Claude vai pedir sua permissão para rodar a calculadora e gravar o pedido de cálculo: pode escolher
"sempre permitir" para esses comandos.

## Onde ficam os arquivos

- Memórias de cálculo (PDF, Excel): pasta **Documentos\Calculadora Judicial**. Se a sua pasta Documentos
  estiver no OneDrive, elas sincronizam com a nuvem; para usar outra pasta, peça ao Claude: "grava os
  relatórios em C:\...\Cálculos".
- Cálculos salvos, índices e configurações: na pasta de dados do plugin, no seu computador. Eles não
  se perdem quando o plugin atualiza. No Windows, os componentes do Python ficam em
  `%LOCALAPPDATA%\CalculadoraJudicial` (cerca de 60 MB).
- Cabeçalho do escritório no PDF: "coloca meu escritório no relatório: nome, OAB, endereço" (e o logo em PNG).
- **Cópia de segurança:** os cálculos salvos e o cabeçalho são copiados para
  `Documentos\Calculadora Judicial\Cópia de segurança (não apague)`. Se você desinstalar e instalar o plugin
  de novo, ele recupera essa cópia. Para reinstalar sem perder nada, use
  `/plugin uninstall calculadora-judicial@calculadora-judicial --keep-data`.

## Privacidade

Os cálculos e os índices ficam no seu computador (se a pasta Documentos sincroniza com OneDrive ou iCloud,
os PDFs e a cópia de segurança também vão para essa nuvem). O que você digita na conversa passa pelo Claude, como em
qualquer uso do Claude: a calculadora não precisa do nome das partes, só de valores e datas.

## Aviso

Ferramenta de apoio ao advogado. Confira a memória de cálculo antes de usar; a responsabilidade pelo cálculo
apresentado em juízo é de quem o assina. As escolhas jurídicas em que a lei ou os tribunais admitem mais de
um caminho (ex.: juros cíveis antes de 30/08/2024 — 1% ao mês ou SELIC, STJ Tema 1.368) aparecem na
memória com o critério adotado, e podem ser trocadas. Versão em validação com casos reais.

## Licença

MIT — veja o arquivo [LICENSE](LICENSE). O uso é livre, inclusive comercial, mantido o aviso de direitos autorais; o software é fornecido sem garantia (veja o aviso acima).
