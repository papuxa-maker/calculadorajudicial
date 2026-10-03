"""Modelo do banco de índices (SQLite) e repositório de leitura usado pelo motor.

Tabelas (SPEC, seção 5): ``indices``, ``taxas_mensais`` e ``tabelas_compostas``.
O motor de cálculo lê os índices somente por meio de um repositório
(``RepositorioSQLite`` ou ``RepositorioMemoria``); nunca acessa a internet.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from calculo_judicial.datas import parse_data, primeiro_dia
from calculo_judicial.decimais import para_decimal

RAIZ_PROJETO = Path(__file__).resolve().parents[3]  # pasta do código (projeto ou plugin instalado)
# Pasta dos dados de quem usa (índices, cálculos salvos, logs, configurações). No projeto é a própria raiz;
# no plugin do Claude Code fica fora do código (que é substituído a cada atualização): CALCULADORA_DADOS.
ENV_PASTA_DADOS = "CALCULADORA_DADOS"
PASTA_DADOS = Path(os.environ[ENV_PASTA_DADOS]) if os.environ.get(ENV_PASTA_DADOS, "").strip() else RAIZ_PROJETO
CAMINHO_PADRAO_DB = PASTA_DADOS / "indices.db"
DATA_BASE_FATOR = date(1994, 7, 1)

SQL_CRIACAO = """
CREATE TABLE IF NOT EXISTS indices (
  codigo TEXT PRIMARY KEY,
  nome TEXT NOT NULL,
  fonte TEXT NOT NULL,
  tipo TEXT NOT NULL,
  codigo_sgs INTEGER,
  url_fonte TEXT
);
CREATE TABLE IF NOT EXISTS taxas_mensais (
  indice_codigo TEXT REFERENCES indices(codigo),
  mes_referencia DATE NOT NULL,
  taxa_pct TEXT NOT NULL,
  fator_acumulado TEXT,
  projecao INTEGER DEFAULT 0,
  data_coleta TEXT NOT NULL,
  PRIMARY KEY (indice_codigo, mes_referencia)
);
CREATE TABLE IF NOT EXISTS tabelas_compostas (
  nome TEXT PRIMARY KEY,
  periodos_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS procedencia_tabelas (
  codigo TEXT PRIMARY KEY,
  origem TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verificacoes_indices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  inicio TEXT NOT NULL,
  fim TEXT,
  origem TEXT NOT NULL,
  situacao TEXT NOT NULL,
  novos TEXT NOT NULL DEFAULT '{}',
  erros TEXT NOT NULL DEFAULT '[]',
  atrasados TEXT NOT NULL DEFAULT '[]',
  email_enviado INTEGER NOT NULL DEFAULT 0,
  processo INTEGER,
  alerta INTEGER NOT NULL DEFAULT 0
);
"""
# colunas acrescentadas depois da criação da tabela (bancos já existentes ganham a coluna ao abrir)
COLUNAS_NOVAS = {"verificacoes_indices": [("processo", "INTEGER"), ("alerta", "INTEGER NOT NULL DEFAULT 0")]}

TIPOS_INDICE = ("correcao", "juros", "composto")


# nome de exibição dos códigos internos dos índices (memória, PDF e Excel)
NOMES_CURTOS = {
    "IPCA_E": "IPCA-E", "IPCA_15": "IPCA-15", "IGPM": "IGP-M", "TAXA_LEGAL": "taxa legal", "JUROS_POUPANCA": "juros da poupança",
    "JF": "tabela da JF", "JF_PREV": "tabela da JF (previdenciário)", "TJSP": "tabela do TJSP",
    "TJGO": "tabela do TJGO", "IPC_R": "IPC-r",
}


# séries oficiais encerradas: nunca há mês novo nem projeção (só entram dentro das tabelas de tribunal)
SERIES_ENCERRADAS = {"IPC_R": date(1995, 6, 1)}
SIGLAS_TJ = {"TJAC", "TJAL", "TJAM", "TJAP", "TJBA", "TJCE", "TJDFT", "TJES", "TJGO", "TJMA", "TJMG", "TJMS", "TJMT",
             "TJPA", "TJPB", "TJPE", "TJPI", "TJPR", "TJRJ", "TJRN", "TJRO", "TJRR", "TJRS", "TJSC", "TJSE", "TJSP", "TJTO"}


def nome_curto(codigo: str | None) -> str:
    """Nome legível do índice (``IPCA_E`` → ``IPCA-E``); códigos sem nome próprio ficam como estão (``IPCA``, ``SELIC``)."""
    codigo = codigo or ""
    if codigo in NOMES_CURTOS:
        return NOMES_CURTOS[codigo]
    if codigo in SIGLAS_TJ:
        return f"tabela do {codigo}"  # tabela de tribunal (montada ou importada)
    return codigo


class IndiceAusente(LookupError):
    """Falta a taxa de ``indice`` no mês ``mes`` no banco local.

    Regra do projeto: nenhum índice é inventado, estimado ou interpolado; o
    cálculo para e informa qual mês falta (ou usa projeção, se o usuário pedir).
    """

    def __init__(self, indice: str, mes: date, motivo: str = ""):
        self.indice = indice
        self.mes = mes
        self.motivo = motivo
        texto = f"Índice {indice} sem taxa para {mes.strftime('%m/%Y')} no banco local"
        if motivo:
            texto += f" ({motivo})"
        super().__init__(texto)


class IndiceDesconhecido(KeyError):
    """Código de índice não cadastrado."""


@dataclass(frozen=True)
class InfoIndice:
    codigo: str
    nome: str
    fonte: str
    tipo: str
    codigo_sgs: int | None = None
    url_fonte: str | None = None


@dataclass(frozen=True)
class TaxaMensal:
    """Taxa de um índice em um mês de referência (sempre dia 01)."""

    indice: str
    mes: date
    taxa_pct: Decimal
    fator_acumulado: Decimal | None = None
    projecao: bool = False
    data_coleta: str = ""
    indice_origem: str | None = None  # índice real quando ``indice`` é tabela composta


@dataclass(frozen=True)
class PeriodoComposto:
    de: date
    ate: date | None
    indice: str


@dataclass
class TabelaComposta:
    """Tabela encadeada (ex.: TJGO = INPC até X, IPCA depois).

    Convenção de meses: o mês M usa o índice do período mais recente cujo
    ``de`` cai no mês M ou antes — a mesma convenção do motor (o mês de ``de``
    entra inteiro no período que começa nele).
    """

    nome: str
    periodos: list[PeriodoComposto] = field(default_factory=list)

    def indice_do_mes(self, mes: date) -> str | None:
        escolhido = None
        for p in sorted(self.periodos, key=lambda x: x.de):
            if primeiro_dia(p.de) <= mes:
                escolhido = p
        if escolhido is None:
            return None
        if escolhido.ate is not None and mes > primeiro_dia(escolhido.ate):
            return None
        return escolhido.indice

    def indice_do_dia(self, dia: date) -> str | None:
        """Índice vigente no dia (usado no pró-rata, quando o mês é dividido entre dois períodos)."""
        for p in sorted(self.periodos, key=lambda x: x.de):
            if p.de <= dia and (p.ate is None or dia <= p.ate):
                return p.indice
        return None

    def para_json(self) -> str:
        return json.dumps(
            [
                {"de": p.de.isoformat(), "ate": p.ate.isoformat() if p.ate else None, "indice": p.indice}
                for p in self.periodos
            ],
            ensure_ascii=False,
        )

    @classmethod
    def de_json(cls, nome: str, texto: str) -> "TabelaComposta":
        dados = json.loads(texto)
        periodos = [
            PeriodoComposto(parse_data(p["de"]), parse_data(p["ate"]) if p.get("ate") else None, p["indice"])
            for p in dados
        ]
        return cls(nome, periodos)


# ---------------------------------------------------------------------------
# Repositórios
# ---------------------------------------------------------------------------


class RepositorioIndices:
    """Interface de leitura de índices usada pelo motor (somente banco local)."""

    def info(self, codigo: str) -> InfoIndice:  # pragma: no cover - abstrato
        raise NotImplementedError

    def _taxa_gravada(self, codigo: str, mes: date) -> TaxaMensal | None:  # pragma: no cover
        raise NotImplementedError

    def ultimo_mes(self, codigo: str) -> date | None:  # pragma: no cover
        raise NotImplementedError

    def tabela_composta(self, nome: str) -> TabelaComposta | None:  # pragma: no cover
        raise NotImplementedError

    def primeiro_mes(self, codigo: str) -> date | None:
        """Primeiro mês com taxa gravada (``None`` se o repositório não souber dizer)."""
        return None

    def existe(self, codigo: str) -> bool:
        try:
            self.info(codigo)
            return True
        except IndiceDesconhecido:
            return self.tabela_composta(codigo) is not None

    def taxa(self, codigo: str, mes: date, permitir_projecao: bool = False, dia: date | None = None) -> TaxaMensal:
        """Taxa de ``codigo`` no mês ``mes``.

        - Tabela composta: resolve o índice do período e devolve a taxa dele. Com
          ``dia`` (fração de mês no pró-rata), usa o índice vigente naquele dia; sem
          ``dia``, o mês inteiro vai para o período que começa nele.
        - Mês ausente depois do último mês gravado: se ``permitir_projecao``,
          repete a taxa do último mês disponível marcada como projeção; senão
          levanta ``IndiceAusente``.
        - Mês ausente no meio da série (lacuna) sempre levanta ``IndiceAusente``.
        """
        mes = primeiro_dia(mes)
        composta = self.tabela_composta(codigo)
        if composta is not None:
            origem = composta.indice_do_dia(dia) if dia is not None else composta.indice_do_mes(mes)
            if origem is None:
                raise IndiceAusente(codigo, mes, "nenhum período da tabela composta cobre o mês")
            t = self.taxa(origem, mes, permitir_projecao)
            return TaxaMensal(codigo, mes, t.taxa_pct, None, t.projecao, t.data_coleta, origem)
        self.info(codigo)  # valida existência
        gravada = self._taxa_gravada(codigo, mes)
        if gravada is not None:
            if gravada.projecao and not permitir_projecao:
                raise IndiceAusente(codigo, mes, "mês disponível apenas como projeção")
            return gravada
        ultimo = self.ultimo_mes(codigo)
        if ultimo is not None and mes > ultimo and codigo in SERIES_ENCERRADAS:
            raise IndiceAusente(codigo, mes, f"série encerrada em {ultimo:%m/%Y}; não há projeção")
        if ultimo is not None and mes > ultimo and permitir_projecao:
            base = self._taxa_gravada(codigo, ultimo)
            assert base is not None
            return TaxaMensal(codigo, mes, base.taxa_pct, None, True, base.data_coleta, codigo)
        if ultimo is None:
            motivo = "índice sem nenhuma taxa gravada"
        elif mes > ultimo:
            motivo = f"último mês gravado é {ultimo.strftime('%m/%Y')}"
        elif (primeiro := self.primeiro_mes(codigo)) is not None and mes < primeiro:
            motivo = f"a série começa em {primeiro:%m/%Y}"
        else:
            motivo = "lacuna na série"
        raise IndiceAusente(codigo, mes, motivo)


class RepositorioMemoria(RepositorioIndices):
    """Repositório em memória (testes e cálculos com séries fornecidas à mão)."""

    def __init__(self) -> None:
        self._info: dict[str, InfoIndice] = {}
        self._taxas: dict[str, dict[date, TaxaMensal]] = {}
        self._compostas: dict[str, TabelaComposta] = {}

    def cadastrar(self, info: InfoIndice) -> None:
        self._info[info.codigo] = info
        self._taxas.setdefault(info.codigo, {})

    def gravar(self, codigo: str, mes: date, taxa_pct: Decimal | str, projecao: bool = False) -> None:
        if codigo not in self._info:
            self.cadastrar(InfoIndice(codigo, codigo, "teste", "correcao"))
        mes = primeiro_dia(mes)
        self._taxas[codigo][mes] = TaxaMensal(codigo, mes, para_decimal(taxa_pct), None, projecao, "teste")

    def gravar_serie(self, codigo: str, inicio: date, taxas: list[str | Decimal]) -> None:
        from calculo_judicial.datas import somar_meses

        for i, t in enumerate(taxas):
            self.gravar(codigo, somar_meses(primeiro_dia(inicio), i), t)

    def cadastrar_composta(self, tabela: TabelaComposta) -> None:
        self._compostas[tabela.nome] = tabela

    def info(self, codigo: str) -> InfoIndice:
        try:
            return self._info[codigo]
        except KeyError:
            if codigo in self._compostas:
                return InfoIndice(codigo, f"Tabela composta {codigo}", "tabela composta", "correcao")
            raise IndiceDesconhecido(codigo) from None

    def _taxa_gravada(self, codigo: str, mes: date) -> TaxaMensal | None:
        return self._taxas.get(codigo, {}).get(mes)

    def ultimo_mes(self, codigo: str) -> date | None:
        oficiais = [m for m, t in self._taxas.get(codigo, {}).items() if not t.projecao]
        return max(oficiais) if oficiais else None

    def primeiro_mes(self, codigo: str) -> date | None:
        return min(self._taxas.get(codigo, {}), default=None)

    def tabela_composta(self, nome: str) -> TabelaComposta | None:
        return self._compostas.get(nome)


class RepositorioSQLite(RepositorioIndices):
    """Repositório somente-leitura sobre ``indices.db``; carrega cada índice uma vez."""

    def __init__(self, caminho: str | Path | None = None, conn: sqlite3.Connection | None = None):
        self.conn = conn or conectar(caminho)
        self._cache: dict[str, dict[date, TaxaMensal]] = {}
        self._info: dict[str, InfoIndice] | None = None
        self._compostas: dict[str, TabelaComposta] | None = None

    def _carregar_info(self) -> dict[str, InfoIndice]:
        if self._info is None:
            linhas = self.conn.execute(
                "SELECT codigo, nome, fonte, tipo, codigo_sgs, url_fonte FROM indices"
            ).fetchall()
            self._info = {l[0]: InfoIndice(*l) for l in linhas}
        return self._info

    def _carregar_compostas(self) -> dict[str, TabelaComposta]:
        if self._compostas is None:
            linhas = self.conn.execute("SELECT nome, periodos_json FROM tabelas_compostas").fetchall()
            self._compostas = {n: TabelaComposta.de_json(n, j) for n, j in linhas}
        return self._compostas

    def _carregar_taxas(self, codigo: str) -> dict[date, TaxaMensal]:
        if codigo not in self._cache:
            linhas = self.conn.execute(
                "SELECT mes_referencia, taxa_pct, fator_acumulado, projecao, data_coleta "
                "FROM taxas_mensais WHERE indice_codigo = ?",
                (codigo,),
            ).fetchall()
            self._cache[codigo] = {
                parse_data(m): TaxaMensal(
                    codigo,
                    parse_data(m),
                    Decimal(t),
                    Decimal(f) if f is not None else None,
                    bool(p),
                    c,
                )
                for m, t, f, p, c in linhas
            }
        return self._cache[codigo]

    def info(self, codigo: str) -> InfoIndice:
        infos = self._carregar_info()
        if codigo in infos:
            return infos[codigo]
        if codigo in self._carregar_compostas():
            return InfoIndice(codigo, f"Tabela composta {codigo}", "tabela composta", "correcao")
        raise IndiceDesconhecido(codigo)

    def _taxa_gravada(self, codigo: str, mes: date) -> TaxaMensal | None:
        return self._carregar_taxas(codigo).get(mes)

    def ultimo_mes(self, codigo: str) -> date | None:
        """Último mês com taxa oficial (linhas marcadas como projeção não contam)."""
        oficiais = [m for m, t in self._carregar_taxas(codigo).items() if not t.projecao]
        return max(oficiais) if oficiais else None

    def primeiro_mes(self, codigo: str) -> date | None:
        return min(self._carregar_taxas(codigo), default=None)

    def tabela_composta(self, nome: str) -> TabelaComposta | None:
        return self._carregar_compostas().get(nome)

    def listar_indices(self) -> list[InfoIndice]:
        return sorted(self._carregar_info().values(), key=lambda i: i.codigo)


# ---------------------------------------------------------------------------
# Conexão, criação e escrita
# ---------------------------------------------------------------------------


_ENV_CARREGADO = False


def carregar_env() -> None:
    """Carrega ``.env`` da raiz do projeto uma única vez (variáveis já definidas prevalecem)."""
    global _ENV_CARREGADO
    if _ENV_CARREGADO:
        return
    _ENV_CARREGADO = True
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv(PASTA_DADOS / ".env", override=False)


def caminho_db(caminho: str | Path | None = None) -> Path:
    """Caminho do banco: argumento, senão ``INDICES_DB`` (ambiente ou ``.env``), senão ``indices.db`` na raiz."""
    if caminho:
        return Path(caminho)
    carregar_env()
    env = os.environ.get("INDICES_DB", "").strip()
    return Path(env) if env else CAMINHO_PADRAO_DB


def conectar(caminho: str | Path | None = None) -> sqlite3.Connection:
    """Abre (e cria, se preciso) o banco de índices com o esquema do SPEC."""
    destino = caminho_db(caminho)
    if str(destino) != ":memory:":
        destino.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(destino))
    conn.execute("PRAGMA foreign_keys = ON")
    criar_tabelas(conn)
    return conn


def criar_tabelas(conn: sqlite3.Connection) -> None:
    conn.executescript(SQL_CRIACAO)
    for tabela, colunas in COLUNAS_NOVAS.items():
        existentes = {linha[1] for linha in conn.execute(f"PRAGMA table_info({tabela})")}
        for nome, tipo in colunas:
            if nome not in existentes:
                conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")
    conn.commit()


def registrar_indice(conn: sqlite3.Connection, info: InfoIndice, commit: bool = True) -> None:
    if info.tipo not in TIPOS_INDICE:
        raise ValueError(f"tipo inválido: {info.tipo}")
    conn.execute(
        "INSERT INTO indices (codigo, nome, fonte, tipo, codigo_sgs, url_fonte) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(codigo) DO UPDATE SET nome=excluded.nome, fonte=excluded.fonte, tipo=excluded.tipo, "
        "codigo_sgs=excluded.codigo_sgs, url_fonte=excluded.url_fonte",
        (info.codigo, info.nome, info.fonte, info.tipo, info.codigo_sgs, info.url_fonte),
    )
    if commit:
        conn.commit()


def gravar_taxa(
    conn: sqlite3.Connection,
    indice: str,
    mes: date,
    taxa_pct: Decimal,
    projecao: bool = False,
    data_coleta: str | None = None,
    substituir: bool = False,
) -> None:
    """Grava uma taxa mensal. Sem ``substituir``, duplicata levanta ``sqlite3.IntegrityError``."""
    if not isinstance(taxa_pct, Decimal):
        raise TypeError("taxa_pct deve ser Decimal")
    mes = primeiro_dia(mes)
    coleta = data_coleta or datetime.now().isoformat(timespec="seconds")
    verbo = "INSERT OR REPLACE" if substituir else "INSERT"
    conn.execute(
        f"{verbo} INTO taxas_mensais (indice_codigo, mes_referencia, taxa_pct, fator_acumulado, projecao, data_coleta) "
        "VALUES (?, ?, ?, NULL, ?, ?)",
        (indice, mes.isoformat(), format(taxa_pct, "f"), int(projecao), coleta),
    )


def gravar_tabela_composta(conn: sqlite3.Connection, tabela: TabelaComposta) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO tabelas_compostas (nome, periodos_json) VALUES (?, ?)",
        (tabela.nome, tabela.para_json()),
    )
    conn.commit()


def ultimo_mes_gravado(conn: sqlite3.Connection, indice: str) -> date | None:
    linha = conn.execute(
        "SELECT MAX(mes_referencia) FROM taxas_mensais WHERE indice_codigo = ? AND projecao = 0", (indice,)
    ).fetchone()
    return parse_data(linha[0]) if linha and linha[0] else None


def listar_indices(conn: sqlite3.Connection) -> list[InfoIndice]:
    linhas = conn.execute(
        "SELECT codigo, nome, fonte, tipo, codigo_sgs, url_fonte FROM indices ORDER BY codigo"
    ).fetchall()
    return [InfoIndice(*l) for l in linhas]
