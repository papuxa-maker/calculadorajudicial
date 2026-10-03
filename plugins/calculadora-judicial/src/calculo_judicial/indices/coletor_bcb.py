"""Coletor de índices via API SGS do Banco Central e linha de comando da etapa 1.

Comandos (SPEC, seção 5):

    python -m calculo_judicial.indices.coletor_bcb --atualizar
    python -m calculo_judicial.indices.coletor_bcb --status
    python -m calculo_judicial.indices.coletor_bcb --importar-csv dados/arquivo.csv --indice TJGO

Regras do coletor:

- Para cada índice com código SGS, busca do mês seguinte ao último gravado até
  o mês anterior ao atual (meses do mês corrente nunca são gravados, porque
  podem estar incompletos).
- Validação por registro: taxa dentro da faixa do índice (−2% a +5%; IGP-M
  −3% a +8%); mês esperado (sequência sem lacuna); sem duplicata. Registro
  inválido não é gravado, vai para ``log/alertas.log`` e interrompe a carga
  daquele índice (os meses seguintes dependeriam de uma lacuna).
- Exceção histórica explícita: julho/1994, primeiro mês do Real, tem taxas
  oficiais acima de 5% (IPCA 6,84%, INPC 7,75%, SELIC 6,87%); esse mês é aceito
  na carga inicial e o fato é registrado em ``log/alertas.log``.
- Taxa legal negativa é gravada como 0 (art. 406, §3º, do Código Civil).
- Depois de gravar, recalcula o ``fator_acumulado`` do índice inteiro.
- Séries diárias são agregadas por mês conforme a natureza da série.

O motor de cálculo nunca chama este módulo: ele só lê o banco local.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import NamedTuple

from calculo_judicial.datas import indice_mes, mes_anterior, parse_data, primeiro_dia, somar_meses, ultimo_dia
from calculo_judicial.decimais import CEM, CONTEXTO, UM, ZERO, para_decimal, q10
from calculo_judicial.indices.fatores import recalcular_fatores
from calculo_judicial.indices import tribunais
from calculo_judicial.indices.importador_csv import MARCAS_PRESUMIDA
from calculo_judicial.indices.modelo import (
    SERIES_ENCERRADAS,
    DATA_BASE_FATOR,
    PASTA_DADOS,
    RAIZ_PROJETO,  # noqa: F401 - reexportado (tabelas_tribunal, scripts)
    InfoIndice,
    PeriodoComposto,
    TabelaComposta,
    conectar,
    gravar_tabela_composta,
    gravar_taxa,
    listar_indices,
    registrar_indice,
    ultimo_mes_gravado,
)

URL_API = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
URL_SGS = "https://www3.bcb.gov.br/sgspub/localizarseries/localizarSeries.do?method=prepararTelaLocalizarSeries"
CAMINHO_ALERTAS = PASTA_DADOS / "log" / "alertas.log"

# Meses aceitos fora da faixa na carga histórica (primeiro mês do Plano Real).
EXCECOES_FAIXA_HISTORICA = {date(1994, 7, 1)}

FAIXA_PADRAO = (Decimal("-2"), Decimal("5"))
FAIXA_IGPM = (Decimal("-3"), Decimal("8"))


@dataclass(frozen=True)
class DefinicaoSerie:
    """Série do SGS e como transformá-la em taxa mensal.

    ``agregacao``:
      - ``mensal``: um registro por mês, datado no dia 01;
      - ``diaria_produto``: taxa diária; taxa do mês = produto de (1 + t/100) − 1;
      - ``primeiro_dia``: cada registro é a taxa mensal do período que começa
        naquela data; usa-se o registro do dia 01 de cada mês.
    """

    codigo: str
    nome: str
    tipo: str
    codigo_sgs: int
    agregacao: str = "mensal"
    faixa: tuple[Decimal, Decimal] = FAIXA_PADRAO
    piso_zero: bool = False
    inicio: date = DATA_BASE_FATOR
    dia_divulgacao: int = 15  # dia aproximado do mês seguinte em que o mês de referência é divulgado
    observacao: str = ""
    # Séries diárias: a API recusa janelas > 10 anos e dá timeout em janelas grandes da 226.
    anos_por_janela: int = 30
    # Algumas séries mensais (ex.: 29543) devolvem "Requisição inválida" quando há filtro de datas.
    consulta_sem_datas: bool = False
    fonte_personalizada: str = ""

    @property
    def fonte(self) -> str:
        return self.fonte_personalizada or f"BCB SGS {self.codigo_sgs}"

    @property
    def url_fonte(self) -> str:
        return f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{self.codigo_sgs}/dados?formato=json"

    def info(self) -> InfoIndice:
        return InfoIndice(self.codigo, self.nome, self.fonte, self.tipo, self.codigo_sgs, self.url_fonte)


# Códigos confirmados na API e no web service SOAP do SGS em 01/10/2026 (ver PROGRESSO.md, etapa 1).
SERIES_BCB: list[DefinicaoSerie] = [
    DefinicaoSerie("IPCA", "IPCA — variação mensal (IBGE)", "correcao", 433, dia_divulgacao=12),
    # IPCA-E: SGS 10764 é idêntica à 7478 (IPCA-15) em todos os meses desde mai/2000 e cobre
    # desde 1992; é a série que a Calculadora do Cidadão usa para "IPCA-E (IBGE)".
    DefinicaoSerie("IPCA_E", "IPCA-E — variação mensal (IBGE; = IPCA-15 mensal)", "correcao", 10764,
                   dia_divulgacao=1, observacao="SPEC indicava 7478 (IPCA-15, só desde mai/2000); valores idênticos"),
    DefinicaoSerie("IPCA_15", "IPCA-15 — variação mensal (IBGE)", "correcao", 7478, inicio=date(2000, 5, 1),
                   dia_divulgacao=1, observacao="série começa em mai/2000; insumo do Fator IPCA da taxa legal"),
    DefinicaoSerie("INPC", "INPC — variação mensal (IBGE)", "correcao", 188, dia_divulgacao=12),
    # IGP-M: SGS 28655 (variação calculada dos números-índice com 3 casas, FGV) é a série que a
    # Calculadora do Cidadão usa desde 12/04/2021; a 189 é a mesma variação arredondada a 2 casas.
    DefinicaoSerie("IGPM", "IGP-M — variação mensal (FGV)", "correcao", 28655, faixa=FAIXA_IGPM, dia_divulgacao=1,
                   observacao="SPEC indicava 189 (2 casas); 28655 bate com a Calculadora do Cidadão"),
    DefinicaoSerie("SELIC", "SELIC acumulada no mês", "composto", 4390, dia_divulgacao=2),
    # TR: a 226 traz a TR de cada período mensal pela data de início; usa-se o período de 01/MM a
    # 01/MM+1. (A 7811, "TR - primeiro dia do mês", tem os mesmos valores mas arredondados a 2 casas
    # até 2002.) O SPEC sugeria somar a série diária, o que estaria errado: cada registro já é mensal.
    DefinicaoSerie("TR", "Taxa Referencial — período de 01/MM a 01/MM+1", "juros", 226, agregacao="primeiro_dia",
                   dia_divulgacao=2, anos_por_janela=1),
    # Taxa legal (art. 406 do CC, Lei 14.905/2024; Res. CMN 5.171/2024): % a.m., já com piso zero.
    DefinicaoSerie("TAXA_LEGAL", "Taxa legal — art. 406 CC (Res. CMN 5.171/2024)", "juros", 29543,
                   piso_zero=True, inicio=date(2024, 8, 1), dia_divulgacao=1, consulta_sem_datas=True,
                   observacao="mês M usa SELIC e IPCA-15 do mês M-1; publicada no início do mês M"),
    # Poupança (juros da Fazenda Pública, art. 1º-F da Lei 9.494/97): rentabilidade do período de
    # 01/MM a 01/MM+1. A 25 é a regra antiga (depósitos até 03/05/2012); a 195 a regra nova
    # (depósitos desde 04/05/2012, primeiro aniversário no dia 01 em 01/06/2012).
    DefinicaoSerie("POUPANCA_ANTIGA", "Poupança — regra até 03/05/2012, período de 01/MM a 01/MM+1", "juros", 25,
                   agregacao="primeiro_dia", dia_divulgacao=2, anos_por_janela=1),
    DefinicaoSerie("POUPANCA_NOVA", "Poupança — regra desde 04/05/2012, período de 01/MM a 01/MM+1", "juros", 195,
                   agregacao="primeiro_dia", inicio=date(2012, 6, 1), dia_divulgacao=2, anos_por_janela=1),
    # Juros de mora da Fazenda Pública (art. 1º-F da Lei 9.494/97): só a remuneração adicional da
    # poupança, sem a TR (Tema 810/STF; Manual CJF 2026, 4.2.2). Lei 8.177/91, art. 12, II: 0,5% a.m.;
    # desde 04/05/2012 (red. Lei 12.703/2012), 70% da meta SELIC mensalizada quando a meta for
    # ≤ 8,5% a.a. Derivado da meta SELIC vigente no dia 01 de cada mês (SGS 432).
    DefinicaoSerie("JUROS_POUPANCA", "Juros da poupança (remuneração adicional, sem TR) — Lei 8.177/91, art. 12, II",
                   "juros", 432, agregacao="juros_poupanca", dia_divulgacao=2, anos_por_janela=5,
                   fonte_personalizada="Derivado: Lei 8.177/91, art. 12, II + meta SELIC BCB SGS 432",
                   observacao="derivado por fórmula legal da meta SELIC (SGS 432); confere com 0,4412% de dez/2021 do Manual CJF"),
]

# Tabelas compostas cadastradas pelo coletor (encadeamento de séries do banco).
TABELAS_COMPOSTAS_BCB = [
    TabelaComposta(
        "POUPANCA",
        [
            PeriodoComposto(date(1994, 7, 1), date(2012, 5, 31), "POUPANCA_ANTIGA"),
            PeriodoComposto(date(2012, 6, 1), None, "POUPANCA_NOVA"),
        ],
    ),
    # TJGO: não há tabela de fatores oficial da CGJ-GO. Provimento CGJ-GO 06/2019, art. 7º: INPC
    # "caso não seja determinado de outra forma no ato judicial". Depois de 30/08/2024, IPCA
    # (art. 389, p.ú., do CC, red. Lei 14.905/2024). PENDENTE DE CONFIRMAÇÃO PELO ESCRITÓRIO.
    TabelaComposta(
        "TJGO",
        [
            PeriodoComposto(date(1994, 7, 1), date(2024, 8, 31), "INPC"),
            PeriodoComposto(date(2024, 9, 1), None, "IPCA"),
        ],
    ),
]

# Tribunais de Justiça cuja tabela é a cadeia de índices oficiais (levantamento de 03/10/2026)
TABELAS_COMPOSTAS_BCB += [tribunais.tabela_composta(t) for t in tribunais.TABELAS_TRIBUNAIS]

INFO_COMPOSTAS = {
    "POUPANCA": InfoIndice("POUPANCA", "Poupança (rentabilidade total, com TR) — 25 até 05/2012, 195 depois",
                           "BCB SGS 25 + 195", "juros", None, URL_API.format(codigo=195)),
    "TJGO": InfoIndice("TJGO", "Tabela do TJGO montada pela calculadora com os índices do Banco Central (não é a tabela "
                       "publicada pelo tribunal): INPC até 08/2024 (Prov. CGJ-GO 06/2019, art. 7º) e IPCA desde "
                       "09/2024 (CC, art. 389)", "Montada pela calculadora: INPC (BCB SGS 188) e IPCA (BCB SGS 433)",
                       "correcao", None, "https://tjdocs.tjgo.jus.br/documentos/516868"),
}
INFO_COMPOSTAS.update({t.codigo: tribunais.info(t) for t in tribunais.TABELAS_TRIBUNAIS})

# Lei 8.177/91, art. 12, II: limite da meta SELIC para a regra dos 70% e remuneração fixa.
LIMITE_META_SELIC = Decimal("8.5")
JUROS_POUPANCA_FIXO = Decimal("0.5")
INICIO_REGRA_NOVA_POUPANCA = date(2012, 5, 1)


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------


_ALERTAS_DO_DIA: set[tuple[str, str, str, str]] = set()


def registrar_alerta(indice: str, mensagem: str, caminho: Path | None = None) -> None:
    """Acrescenta uma linha a ``log/alertas.log``.

    Nunca levanta exceção (arquivo aberto em outro programa não pode derrubar a rotina que avisa)
    e não repete a mesma mensagem do mesmo índice no mesmo dia (a rotina automática tenta várias vezes).
    """
    destino = caminho or CAMINHO_ALERTAS
    agora = datetime.now()
    chave = (str(destino), agora.date().isoformat(), indice, mensagem)
    if chave in _ALERTAS_DO_DIA:
        return
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        with open(destino, "a", encoding="utf-8") as arq:
            arq.write(f"{agora.isoformat(timespec='seconds')} [{indice}] {mensagem}\n")
        _ALERTAS_DO_DIA.add(chave)
    except OSError as erro:
        try:
            print(f"[alerta não gravado em {destino}: {erro}] [{indice}] {mensagem}", file=sys.stderr)
        except (OSError, ValueError):
            pass


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


class ErroAPI(RuntimeError):
    pass


def _cliente():
    import httpx

    return httpx.Client(
        timeout=httpx.Timeout(60.0, connect=20.0),
        headers={"Accept": "application/json", "User-Agent": "calculo-judicial/0.1 (uso interno)"},
        follow_redirects=True,
    )


class RegistroSGS(NamedTuple):
    """Registro bruto da API: data (início), valor e, nas séries por período (TR), a data final."""

    data: date
    valor: Decimal
    data_fim: date | None = None


def _titulo_html(texto: str) -> str:
    """Título de uma página HTML de erro (para o alerta), ou o começo do texto."""
    import re

    achado = re.search(r"<title>(.*?)</title>", texto or "", re.IGNORECASE | re.DOTALL)
    if achado:
        return f"página HTML '{' '.join(achado.group(1).split())}'"
    return repr((texto or "")[:120])


def _ler_registros(dados: list[dict]) -> list[RegistroSGS]:
    registros = []
    for r in dados:
        valor = str(r.get("valor", "")).strip()
        if not valor:
            continue
        fim = r.get("dataFim")
        registros.append(RegistroSGS(parse_data(r["data"]), para_decimal(valor), parse_data(fim) if fim else None))
    return registros


def baixar_trecho(codigo_sgs: int, inicio: date, fim: date, cliente=None, tentativas: int = 6) -> list[RegistroSGS]:
    """Baixa um trecho da série; devolve registros com valores em Decimal.

    A API devolve lista de ``{"data": "dd/mm/aaaa", "valor": "0.43"}`` (a TR traz
    também ``dataFim``). Erros 5xx, falhas de rede e respostas 200 que não são
    JSON (a API às vezes devolve página de erro com status 200) são repetidos
    com espera crescente. Resposta 404 significa "sem dados no trecho".
    """
    import httpx

    proprio = cliente is None
    cliente = cliente or _cliente()
    params = {"formato": "json", "dataInicial": inicio.strftime("%d/%m/%Y"), "dataFinal": fim.strftime("%d/%m/%Y")}
    try:
        ultimo_erro = None
        for tentativa in range(tentativas):
            try:
                resp = cliente.get(URL_API.format(codigo=codigo_sgs), params=params)
            except httpx.HTTPError as erro:
                ultimo_erro = f"falha de rede: {erro}"
            else:
                if resp.status_code == 404:
                    return []
                if resp.status_code == 200:
                    try:
                        dados = resp.json()
                    except ValueError:
                        # Página HTML ("Requisição inválida!") com status 200: aparece também de
                        # forma transitória em consultas válidas, então é repetida.
                        ultimo_erro = f"resposta 200 sem JSON: {_titulo_html(resp.text)}"
                    else:
                        if isinstance(dados, dict):
                            # A API devolve objeto de erro em alguns casos. "Sem valores no
                            # intervalo" (ex.: mês ainda não divulgado) chega como {"erro": {}} ou
                            # com "Value(s) not found"; isso é lista vazia, não falha.
                            erro = dados.get("erro", dados.get("error"))
                            texto = str(erro or dados.get("message") or "").lower()
                            if erro == {} or erro == "" or "not found" in texto or "nenhum" in texto:
                                return []
                            raise ErroAPI(f"SGS {codigo_sgs}: resposta inesperada: {str(dados)[:200]}")
                        return _ler_registros(dados)
                else:
                    ultimo_erro = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
                        raise ErroAPI(f"SGS {codigo_sgs}: {ultimo_erro}")
            time.sleep(min(2 ** tentativa, 20))
        raise ErroAPI(f"SGS {codigo_sgs}: {ultimo_erro}")
    finally:
        if proprio:
            cliente.close()


def baixar_serie_completa(codigo_sgs: int, cliente=None, tentativas: int = 6) -> list[RegistroSGS]:
    """Baixa a série inteira sem filtro de datas (usado em séries mensais curtas como a 29543)."""
    import httpx

    proprio = cliente is None
    cliente = cliente or _cliente()
    try:
        ultimo_erro = None
        for tentativa in range(tentativas):
            try:
                resp = cliente.get(URL_API.format(codigo=codigo_sgs), params={"formato": "json"})
            except httpx.HTTPError as erro:
                ultimo_erro = f"falha de rede: {erro}"
            else:
                if resp.status_code == 200:
                    try:
                        dados = resp.json()
                    except ValueError:
                        ultimo_erro = f"resposta 200 sem JSON: {_titulo_html(resp.text)}"
                    else:
                        if isinstance(dados, list):
                            return _ler_registros(dados)
                        raise ErroAPI(f"SGS {codigo_sgs}: resposta inesperada: {str(dados)[:200]}")
                else:
                    ultimo_erro = f"HTTP {resp.status_code}: {resp.text[:200]}"
            time.sleep(min(2 ** tentativa, 20))
        raise ErroAPI(f"SGS {codigo_sgs}: {ultimo_erro}")
    finally:
        if proprio:
            cliente.close()


def baixar_serie(
    codigo_sgs: int,
    inicio: date,
    fim: date,
    cliente=None,
    anos_por_janela: int = 9,
    sem_datas: bool = False,
    tentativas: int = 6,
) -> list[RegistroSGS]:
    """Baixa a série entre ``inicio`` e ``fim``.

    Em janelas de até ``anos_por_janela`` anos (a API limita séries diárias a 10
    anos e dá timeout em janelas grandes da TR). Com ``sem_datas``, baixa a
    série inteira e filtra localmente.
    """
    proprio = cliente is None
    cliente = cliente or _cliente()
    try:
        if sem_datas:
            registros = [r for r in baixar_serie_completa(codigo_sgs, cliente, tentativas) if inicio <= r.data <= fim]
        else:
            registros = []
            atual = inicio
            while atual <= fim:
                limite = min(date(atual.year + anos_por_janela, atual.month, 1) - timedelta(days=1), fim)
                registros.extend(baixar_trecho(codigo_sgs, atual, limite, cliente, tentativas))
                atual = limite + timedelta(days=1)
                if atual <= fim:
                    time.sleep(0.5)
    finally:
        if proprio:
            cliente.close()
    return sorted(set(registros), key=lambda r: (r.data, r.data_fim or r.data))


# ---------------------------------------------------------------------------
# Agregação mensal
# ---------------------------------------------------------------------------


def agregar_mensal(
    definicao: DefinicaoSerie, registros, desde: date | None = None
) -> tuple[list[tuple[date, Decimal]], list[str]]:
    """Transforma registros brutos em [(mês, taxa_pct)] conforme ``definicao.agregacao``.

    ``registros``: ``RegistroSGS`` ou tuplas ``(data, valor[, data_fim])``.
    Mês com dois valores diferentes na resposta da API é EXCLUÍDO (SPEC: duplicata
    não é gravada); a lacuna resultante faz ``validar_e_gravar`` parar com alerta.
    Repetição com o mesmo valor é só deduplicada.
    """
    problemas: list[str] = []
    normalizados = [RegistroSGS(r[0], r[1], r[2] if len(r) > 2 else None) for r in registros]
    if definicao.agregacao in ("mensal", "primeiro_dia"):
        por_mes: dict[date, Decimal] = {}
        conflitos: set[date] = set()
        for d, v, fim in normalizados:
            if definicao.agregacao == "primeiro_dia":
                # Período que começa no dia 01 e termina no dia 01 do mês seguinte. A série 226
                # repete datas 01 com períodos que terminam no "dia 31 virtual" (ex.: 01/07/1994 a
                # 31/07/1994); esses são descartados pelo dataFim.
                if d.day != 1 or (fim is not None and fim != somar_meses(d, 1)):
                    continue
                mes = d
            else:
                mes = primeiro_dia(d)
            if mes in por_mes and por_mes[mes] != v:
                conflitos.add(mes)
                problemas.append(f"{mes:%m/%Y}: valores divergentes na resposta da API ({por_mes[mes]} e {v}); mês não gravado")
                continue
            por_mes.setdefault(mes, v)
        return sorted((m, v) for m, v in por_mes.items() if m not in conflitos), problemas
    if definicao.agregacao == "juros_poupanca":
        inicio = max(primeiro_dia(definicao.inicio), primeiro_dia(desde)) if desde else primeiro_dia(definicao.inicio)
        return juros_poupanca_mensal(normalizados, inicio), problemas
    if definicao.agregacao == "diaria_produto":
        fatores: dict[date, Decimal] = {}
        with localcontext(CONTEXTO):
            for d, v, _ in normalizados:
                mes = primeiro_dia(d)
                fatores[mes] = fatores.get(mes, UM) * (UM + v / CEM)
            return sorted((m, q10((f - UM) * CEM)) for m, f in fatores.items()), problemas
    raise ValueError(f"agregação desconhecida: {definicao.agregacao}")


def taxa_juros_poupanca(meta_selic_aa: Decimal) -> Decimal:
    """Remuneração adicional da poupança (% a.m.) para uma meta SELIC (% a.a.).

    Lei 8.177/91, art. 12, II (red. Lei 12.703/2012): 0,5% a.m. se a meta SELIC for
    superior a 8,5% a.a.; senão 70% da meta, mensalizada: ((1 + 0,7 × meta)^(1/12) − 1).
    Resultado em 4 casas (como a poupança publicada pelo BCB e o Manual do CJF).
    Ex.: meta 7,75% -> 0,4412% (Manual de Cálculos da JF 2026, juros de dez/2021).
    """
    if meta_selic_aa > LIMITE_META_SELIC:
        return JUROS_POUPANCA_FIXO
    with localcontext(CONTEXTO):
        anual = UM + Decimal("0.7") * meta_selic_aa / CEM
        mensal = (anual.ln() / Decimal(12)).exp() - UM
        return (mensal * CEM).quantize(Decimal("0.0001"), rounding="ROUND_HALF_EVEN")


def juros_poupanca_mensal(registros: list[RegistroSGS], inicio: date) -> list[tuple[date, Decimal]]:
    """Série mensal de juros da poupança a partir da meta SELIC diária (SGS 432).

    Meses até abr/2012: 0,5% (regra única do art. 12, II, original). Desde mai/2012: regra
    da Lei 12.703/2012 com a meta vigente no dia 01 do mês (último registro com data ≤ dia 01).
    Para isso o download precisa começar antes do primeiro mês pedido (ver ``atualizar``).
    Um mês sem meta vigente conhecida interrompe a série (sem estimativa).
    """
    ultimo_registro = max((r.data for r in registros), default=None)
    ordenados = sorted(registros, key=lambda r: r.data)
    saida: list[tuple[date, Decimal]] = []
    mes = inicio
    while mes < INICIO_REGRA_NOVA_POUPANCA:
        saida.append((mes, JUROS_POUPANCA_FIXO))
        mes = somar_meses(mes, 1)
    if ultimo_registro is None:
        return saida
    indice = 0
    vigente: Decimal | None = None
    while mes <= ultimo_registro:
        while indice < len(ordenados) and ordenados[indice].data <= mes:
            vigente = ordenados[indice].valor
            indice += 1
        if vigente is None:
            break
        saida.append((mes, taxa_juros_poupanca(vigente)))
        mes = somar_meses(mes, 1)
    return saida


# ---------------------------------------------------------------------------
# Validação e gravação
# ---------------------------------------------------------------------------


@dataclass
class ResultadoIndice:
    codigo: str
    gravados: list[date] = field(default_factory=list)
    rejeitados: list[str] = field(default_factory=list)
    erro: str | None = None

    def __str__(self) -> str:
        if self.erro:
            return f"{self.codigo}: ERRO {self.erro}"
        if not self.gravados:
            texto = f"{self.codigo}: nada novo"
        else:
            texto = f"{self.codigo}: {len(self.gravados)} meses gravados ({self.gravados[0]:%m/%Y} a {self.gravados[-1]:%m/%Y})"
        if self.rejeitados:
            texto += f"; rejeitados: {'; '.join(self.rejeitados[:3])}"
        return texto


def validar_e_gravar(
    conn: sqlite3.Connection,
    definicao: DefinicaoSerie,
    mensais: list[tuple[date, Decimal]],
    limite_mes: date,
    caminho_alertas: Path | None = None,
) -> ResultadoIndice:
    """Valida cada mês (faixa, sequência, duplicata) e grava os válidos.

    ``limite_mes``: último mês que pode ser gravado (mês anterior ao atual).
    Para no primeiro registro inválido, para não deixar lacuna na série.
    """
    resultado = ResultadoIndice(definicao.codigo)
    ultimo = ultimo_mes_gravado(conn, definicao.codigo)
    esperado = somar_meses(ultimo, 1) if ultimo else primeiro_dia(definicao.inicio)
    minimo, maximo = definicao.faixa
    vistos: set[date] = set()
    coleta = datetime.now().isoformat(timespec="seconds")
    projetados = {
        parse_data(m)
        for (m,) in conn.execute(
            "SELECT mes_referencia FROM taxas_mensais WHERE indice_codigo = ? AND projecao = 1", (definicao.codigo,)
        )
    }
    for mes, taxa in mensais:
        if mes > limite_mes:
            break
        if ultimo and mes <= ultimo:
            continue  # já gravado
        if mes in vistos:
            msg = f"{mes:%m/%Y}: duplicata na resposta da API"
            registrar_alerta(definicao.codigo, msg, caminho_alertas)
            resultado.rejeitados.append(msg)
            break
        vistos.add(mes)
        if mes != esperado:
            msg = f"{mes:%m/%Y}: mês inesperado (esperado {esperado:%m/%Y}); série com lacuna, carga interrompida"
            registrar_alerta(definicao.codigo, msg, caminho_alertas)
            resultado.rejeitados.append(msg)
            break
        if definicao.piso_zero and taxa < ZERO:
            registrar_alerta(definicao.codigo, f"{mes:%m/%Y}: taxa {taxa} negativa gravada como 0 (art. 406, §3º, CC)", caminho_alertas)
            taxa = ZERO
        if not (minimo <= taxa <= maximo):
            if mes in EXCECOES_FAIXA_HISTORICA and ultimo is None:
                registrar_alerta(
                    definicao.codigo,
                    f"{mes:%m/%Y}: taxa {taxa}% fora da faixa [{minimo}; {maximo}] aceita por exceção histórica "
                    "(primeiro mês do Real, valor oficial)",
                    caminho_alertas,
                )
            else:
                msg = f"{mes:%m/%Y}: taxa {taxa}% fora da faixa [{minimo}; {maximo}]; não gravada"
                registrar_alerta(definicao.codigo, msg, caminho_alertas)
                resultado.rejeitados.append(msg)
                break
        if mes in projetados:
            registrar_alerta(definicao.codigo, f"{mes:%m/%Y}: projeção substituída pelo valor oficial {taxa}", caminho_alertas)
        gravar_taxa(conn, definicao.codigo, mes, taxa, data_coleta=coleta, substituir=mes in projetados)
        resultado.gravados.append(mes)
        esperado = somar_meses(mes, 1)
    conn.commit()
    if resultado.gravados:
        recalcular_fatores(conn, definicao.codigo)
    return resultado


def registrar_series(conn: sqlite3.Connection, series: list[DefinicaoSerie] | None = None) -> None:
    """Cadastra as séries do BCB e cria as tabelas compostas padrão que ainda não existem.

    Tabela composta só é criada se não houver tabela com o mesmo nome nem taxas
    gravadas sob esse código (ex.: se o escritório importou a tabela real do TJGO,
    a composta padrão nunca volta).
    """
    for d in series or SERIES_BCB:
        registrar_indice(conn, d.info())
    if series is None:
        tribunais.registrar_ipcr(conn)  # série oficial encerrada do IBGE, usada nas tabelas dos tribunais
        for tabela in TABELAS_COMPOSTAS_BCB:
            ja_composta = conn.execute("SELECT 1 FROM tabelas_compostas WHERE nome = ?", (tabela.nome,)).fetchone()
            ja_taxas = conn.execute("SELECT 1 FROM taxas_mensais WHERE indice_codigo = ? LIMIT 1", (tabela.nome,)).fetchone()
            if not ja_composta and not ja_taxas:
                gravar_tabela_composta(conn, tabela)
                ja_composta = True
            info = conn.execute("SELECT nome FROM indices WHERE codigo = ?", (tabela.nome,)).fetchone()
            montada = info is not None and any(m in (info[0] or "") for m in MARCAS_PRESUMIDA)
            if ja_composta and not ja_taxas and montada:
                atual = conn.execute("SELECT periodos_json FROM tabelas_compostas WHERE nome = ?", (tabela.nome,)).fetchone()
                if atual and atual[0] != tabela.para_json():
                    gravar_tabela_composta(conn, tabela)  # cadeia montada aqui: segue a definição atual
            if ja_composta and tabela.nome in INFO_COMPOSTAS and (
                info is None or (not ja_taxas and info[0] != INFO_COMPOSTAS[tabela.nome].nome
                                 and any(m in (info[0] or "") for m in MARCAS_PRESUMIDA))):
                registrar_indice(conn, INFO_COMPOSTAS[tabela.nome])  # descrição atual da cadeia montada aqui


def atualizar(
    conn: sqlite3.Connection,
    hoje: date | None = None,
    series: list[DefinicaoSerie] | None = None,
    baixar=baixar_serie,
    caminho_alertas: Path | None = None,
) -> list[ResultadoIndice]:
    """Comando ``--atualizar``: busca do mês seguinte ao último gravado até o mês anterior ao atual."""
    hoje = hoje or date.today()
    limite = mes_anterior(hoje)
    registrar_series(conn, series)
    series = series or SERIES_BCB
    resultados = []
    for definicao in series:
        ultimo = ultimo_mes_gravado(conn, definicao.codigo)
        inicio = somar_meses(ultimo, 1) if ultimo else primeiro_dia(definicao.inicio)
        if inicio > limite:
            resultados.append(ResultadoIndice(definicao.codigo))
            continue
        fim_download = ultimo_dia(limite)
        # Juros da poupança dependem da meta SELIC vigente no dia 01: baixar com folga para trás.
        inicio_download = somar_meses(inicio, -2) if definicao.agregacao == "juros_poupanca" else inicio
        try:
            brutos = baixar(
                definicao.codigo_sgs,
                inicio_download,
                fim_download,
                anos_por_janela=definicao.anos_por_janela,
                sem_datas=definicao.consulta_sem_datas,
            )
        except Exception as erro:  # noqa: BLE001 - qualquer falha vira alerta e o próximo índice segue
            msg = f"falha ao baixar SGS {definicao.codigo_sgs}: {erro}"
            registrar_alerta(definicao.codigo, msg, caminho_alertas)
            resultados.append(ResultadoIndice(definicao.codigo, erro=msg))
            continue
        try:
            mensais, problemas = agregar_mensal(definicao, brutos, desde=inicio)
            for p in problemas:
                registrar_alerta(definicao.codigo, p, caminho_alertas)
            resultado = validar_e_gravar(conn, definicao, mensais, limite, caminho_alertas)
            resultado.rejeitados[:0] = problemas  # publicado mas não gravado: quem chama precisa saber
            resultados.append(resultado)
        except Exception as erro:  # noqa: BLE001 - falha num índice não impede os demais
            conn.rollback()
            msg = f"falha ao validar/gravar: {type(erro).__name__}: {erro}"
            registrar_alerta(definicao.codigo, msg, caminho_alertas)
            resultados.append(ResultadoIndice(definicao.codigo, erro=msg))
    return resultados


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@dataclass
class LinhaStatus:
    codigo: str
    nome: str
    fonte: str
    ultimo_mes: date | None
    meses_faltando: int
    alvo: date
    observacao: str = ""


def status(conn: sqlite3.Connection, hoje: date | None = None, series: list[DefinicaoSerie] | None = None) -> list[LinhaStatus]:
    """Por índice: último mês gravado e quantos meses faltam até o mês anterior ao atual."""
    hoje = hoje or date.today()
    alvo = mes_anterior(hoje)
    definicoes = {d.codigo: d for d in (series or SERIES_BCB)}
    compostas = {n: j for n, j in conn.execute("SELECT nome, periodos_json FROM tabelas_compostas")}
    linhas = []
    for info in listar_indices(conn):
        if info.codigo in compostas:
            tabela = TabelaComposta.de_json(info.codigo, compostas[info.codigo])
            cadeia = "; ".join(
                f"{p.indice} desde {p.de:%d/%m/%Y}" + (f" até {p.ate:%d/%m/%Y}" if p.ate else "") for p in tabela.periodos
            )
            vigente = tabela.indice_do_mes(alvo) or tabela.periodos[-1].indice
            ultimo_origem = ultimo_mes_gravado(conn, vigente)
            faltando = max(0, indice_mes(alvo) - indice_mes(ultimo_origem)) if ultimo_origem else 0
            obs = f"segue o {vigente}" + (" — ATRASADO" if faltando >= 2 else "") + f" (tabela composta: {cadeia})"
            linhas.append(LinhaStatus(info.codigo, info.nome, info.fonte, ultimo_origem, faltando, alvo, obs))
            continue
        ultimo = ultimo_mes_gravado(conn, info.codigo)
        d = definicoes.get(info.codigo)
        if info.codigo in SERIES_ENCERRADAS:
            linhas.append(LinhaStatus(info.codigo, info.nome, info.fonte, ultimo, 0, alvo,
                                      f"série encerrada em {SERIES_ENCERRADAS[info.codigo]:%m/%Y}"))
            continue
        if ultimo is None:
            inicio = primeiro_dia(d.inicio) if d else DATA_BASE_FATOR
            faltando = max(0, indice_mes(alvo) - indice_mes(inicio) + 1)
        else:
            faltando = max(0, indice_mes(alvo) - indice_mes(ultimo))
        obs = ""
        if faltando == 1 and d is not None and hoje.day < d.dia_divulgacao:
            obs = f"normal: {alvo:%m/%Y} costuma ser divulgado por volta do dia {d.dia_divulgacao}"
        elif faltando >= 2:
            obs = "ATRASADO"
        elif info.codigo_sgs is None and faltando:
            obs = "tabela de tribunal: importar arquivo novo"
        linhas.append(LinhaStatus(info.codigo, info.nome, info.fonte, ultimo, faltando, alvo, obs))
    return linhas


def formatar_status(linhas: list[LinhaStatus]) -> str:
    if not linhas:
        return "Nenhum índice cadastrado. Rode --atualizar."
    alvo = linhas[0].alvo
    saida = [f"Status dos índices (alvo: {alvo:%m/%Y}, mês anterior ao atual)", ""]
    largura = max(12, max(len(l.codigo) for l in linhas) + 2)
    saida.append(f"{'Índice':<{largura}}{'Último mês':<12}{'Faltam':>7}  Fonte / observação")
    for l in linhas:
        ultimo = l.ultimo_mes.strftime("%m/%Y") if l.ultimo_mes else "—"
        extra = f"{l.fonte}" + (f" — {l.observacao}" if l.observacao else "")
        saida.append(f"{l.codigo:<{largura}}{ultimo:<12}{l.meses_faltando:>7}  {extra}")
    return "\n".join(saida)


# ---------------------------------------------------------------------------
# Linha de comando
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m calculo_judicial.indices.coletor_bcb",
        description="Coletor de índices (API SGS do BCB) e importador de tabelas de tribunal",
    )
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--atualizar", action="store_true", help="baixa os meses novos de todos os índices SGS")
    grupo.add_argument("--status", action="store_true", help="último mês gravado por índice")
    grupo.add_argument("--importar-csv", metavar="ARQUIVO", help="importa CSV/XLSX (mes_referencia,taxa_pct|fator)")
    parser.add_argument("--indice", help="código do índice para --importar-csv (ex.: TJGO)")
    parser.add_argument("--nome", help="nome do índice importado (cadastro novo)")
    parser.add_argument("--fonte", help="fonte do índice importado (ex.: 'TJGO Corregedoria')")
    parser.add_argument("--url-fonte", help="URL de origem do arquivo importado")
    parser.add_argument("--tipo", default="correcao", choices=["correcao", "juros", "composto"])
    parser.add_argument("--convencao-fator", default="fator_fim_do_mes",
                        choices=["fator_fim_do_mes", "fator_inicio_do_mes", "coeficiente_multiplicador"])
    parser.add_argument("--substituir", action="store_true",
                        help="sobrescreve meses já gravados (ou uma tabela composta) na importação")
    parser.add_argument("--aceitar-fora-da-faixa", action="store_true",
                        help="aceita meses fora da faixa de validação (tabelas oficiais com inflação alta); fica no log")
    parser.add_argument("--desde", default=None, help="importa só a partir deste mês (mm/aaaa)")
    parser.add_argument("--db", default=None, help="caminho do banco (padrão: indices.db na raiz ou INDICES_DB)")
    parser.add_argument("--hoje", default=None, help="data de referência dd/mm/aaaa (testes)")
    args = parser.parse_args(argv)

    hoje = parse_data(args.hoje) if args.hoje else date.today()
    conn = conectar(args.db)
    try:
        if args.atualizar:
            for r in atualizar(conn, hoje=hoje):
                print(r)
            print()
            print(formatar_status(status(conn, hoje=hoje)))
            return 0
        if args.status:
            print(formatar_status(status(conn, hoje=hoje)))
            return 0
        if args.importar_csv:
            if not args.indice:
                parser.error("--importar-csv exige --indice")
            from calculo_judicial.indices.importador_csv import ErroImportacao, importar_arquivo

            try:
                r = importar_arquivo(
                    conn,
                    args.importar_csv,
                    args.indice,
                    nome=args.nome,
                    fonte=args.fonte,
                    url_fonte=args.url_fonte,
                    tipo=args.tipo,
                    convencao_fator=args.convencao_fator,
                    substituir=args.substituir,
                    aceitar_fora_da_faixa=args.aceitar_fora_da_faixa,
                    desde=primeiro_dia(parse_data(args.desde)) if args.desde else None,
                )
            except (ErroImportacao, FileNotFoundError) as erro:
                print(f"Importação recusada, nada foi gravado: {erro}", file=sys.stderr)
                return 2
            print(r.resumo())
            return 0
    finally:
        conn.close()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
