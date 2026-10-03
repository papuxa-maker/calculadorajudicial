"""Tabelas de correção dos Tribunais de Justiça montadas com índices oficiais.

Levantamento de 03/10/2026: um pesquisador por tribunal nas fontes primárias (provimentos, portarias,
códigos de normas, a própria tabela publicada) e um verificador independente que refez a checagem e,
quando havia tabela publicada, comparou as taxas mês a mês com as séries oficiais. Entram aqui só os
tribunais cuja tabela é a cadeia de índices oficiais (IBGE/BCB). Ficam de fora, com orientação na skill:

- TJSP: a tabela prática oficial já vem com a calculadora (código ``TJSP``);
- TJRJ (UFIR-RJ e fatores próprios), TJPI (tabela do CJF com UFIR e IPCA-E), TJPR (média INPC/IGP-DI,
  Decreto 1.544/95, sem tabela publicada), TJRS (IGP-M e IPCA, data da troca não confirmada) e TJMS (sem
  tabela; o índice é o do título): importar a tabela oficial ou informar os períodos do título;
- TJGO: cadeia própria já existente em ``coletor_bcb`` (INPC desde 07/1994, IPCA desde 30/08/2024).

Convenção de meses (``TabelaComposta``): o mês da data ``de`` entra inteiro no período que começa
nele. As tabelas mensais do ENCOGE e dos tribunais trocam o INPC pelo IPCA na competência 09/2024
(agosto/2024 inteiro no INPC); o TJSC divide agosto/2024 por dias (IPCA só em 30 e 31/08/2024).
O IPC-r (jul/1994 a jun/1995) vem do arquivo oficial do IBGE (``dados/ibge_ipcr_*``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from calculo_judicial.indices.modelo import InfoIndice, PeriodoComposto, TabelaComposta

FONTES = {"IPC_R": "IPC-r (IBGE)", "INPC": "INPC (BCB SGS 188)", "IPCA": "IPCA (BCB SGS 433)"}
URL_IPCR = "https://ftp.ibge.gov.br/Precos_Indices_de_Precos_ao_Consumidor/Numeros_Indices/Numind_IPC_r/"


@dataclass(frozen=True)
class TabelaTribunal:
    codigo: str
    cadeia: tuple[tuple[date, date | None, str], ...]
    descricao: str  # a cadeia em palavras (vai para a memória de cálculo)
    base: str  # ato normativo ou prática que fixa a cadeia
    url: str


IPCR_INPC_IPCA = (
    (date(1994, 7, 1), date(1995, 6, 30), "IPC_R"),
    (date(1995, 7, 1), date(2024, 8, 31), "INPC"),
    (date(2024, 9, 1), None, "IPCA"),
)
TEXTO_IPCR_INPC_IPCA = "IPC-r de 07/1994 a 06/1995, INPC de 07/1995 a 08/2024 e IPCA desde 09/2024"
INPC_IPCA_LEI = (  # sem tabela publicada: a lei (30/08/2024) na convenção mensal das tabelas (D13a)
    (date(1994, 7, 1), date(2024, 8, 31), "INPC"),
    (date(2024, 9, 1), None, "IPCA"),
)
TEXTO_INPC_IPCA_LEI = "INPC até 08/2024 e IPCA desde 09/2024 (CC, art. 389)"
IPCR_INPC = (
    (date(1994, 7, 1), date(1995, 6, 30), "IPC_R"),
    (date(1995, 7, 1), None, "INPC"),
)
TEXTO_IPCR_INPC = "IPC-r de 07/1994 a 06/1995 e INPC desde 07/1995 (a tabela do tribunal não adotou o IPCA)"

TABELAS_TRIBUNAIS = [
    TabelaTribunal("TJAC", IPCR_INPC_IPCA, TEXTO_IPCR_INPC_IPCA,
                   "Prov. COGER/TJAC 19/1997 e Manual de Orientação (Prov. COGER 23/2020)",
                   "https://www.tjac.jus.br/wp-content/uploads/2020/11/Manual-calculo-custas-3-edicao.pdf"),
    TabelaTribunal("TJAP", IPCR_INPC_IPCA, TEXTO_IPCR_INPC_IPCA, "tabela do ENCOGE adotada pelo TJAP (contadoria)",
                   "https://www.tjap.jus.br/portal/contadoria-unica-atualizacao-monetaria.html"),
    TabelaTribunal("TJBA", IPCR_INPC_IPCA, TEXTO_IPCR_INPC_IPCA, "tabela do ENCOGE do sistema LAURA (TJBA)",
                   "https://laura.tjba.jus.br/"),
    TabelaTribunal("TJCE", IPCR_INPC_IPCA, TEXTO_IPCR_INPC_IPCA,
                   "tabela da Contadoria do Fórum Clóvis Beviláqua (TJCE)", "https://www.tjce.jus.br/indices-contadoria/"),
    TabelaTribunal("TJDFT", IPCR_INPC_IPCA, TEXTO_IPCR_INPC_IPCA, "índices da Contadoria do TJDFT",
                   "https://www.tjdft.jus.br/servicos/atualizacao-monetaria-1/indices-da-contadoria"),
    TabelaTribunal("TJMA", IPCR_INPC_IPCA, TEXTO_IPCR_INPC_IPCA, "Prov. CGJ/MA 3/2026, art. 4º, VI (tabela do ENCOGE)",
                   "https://www.tjma.jus.br/atualizacao-monetaria/tj"),
    # a tabela da CGJ-MG continua no INPC depois de 08/2024 (comunicado mensal no DJe; o IPCA fica "a critério de
    # cada julgador"): conferido com a tabela oficial de set/2026 nos casos-ouro tj_mg_fator_*
    TabelaTribunal("TJMG", IPCR_INPC, TEXTO_IPCR_INPC, "fator de atualização monetária da CGJ-MG (comunicado mensal no DJe)",
                   "https://www.tjmg.jus.br/portal-tjmg/processos/indicadores/fator-de-atualizacao-monetaria.htm"),
    TabelaTribunal("TJMT", IPCR_INPC_IPCA, TEXTO_IPCR_INPC_IPCA, "tabela adotada pela CGJ-MT (Ofício Circular 123/97-CGJ)",
                   "https://corregedoria.tjmt.jus.br/pagina/208"),
    TabelaTribunal("TJPA", IPCR_INPC_IPCA, TEXTO_IPCR_INPC_IPCA, "Portaria Conjunta TJPA 004/2013 (tabela do 11º ENCOGE)",
                   "https://www.tjpa.jus.br/CMSPortal/VisualizarArquivo?idArquivo=976747"),
    TabelaTribunal("TJPE", IPCR_INPC_IPCA, TEXTO_IPCR_INPC_IPCA, "tabela do ENCOGE (IS TJPE 08/2011 e Súmulas do TJPE)",
                   "https://gilbertomelo.com.br/notas-explicativas-jebrn/"),
    TabelaTribunal("TJRO", IPCR_INPC_IPCA, TEXTO_IPCR_INPC_IPCA,
                   "Prov. CG/TJRO 013/1998 e Manual da Contadoria Judicial (2024)",
                   "https://cdn.tjro.jus.br/portal-tjro/arquivos/cgj/contadoria-judicial/Manual_da_Contadoria_Judicial.pdf"),
    TabelaTribunal("TJRR", IPCR_INPC_IPCA, TEXTO_IPCR_INPC_IPCA, "Portaria TJRR/PR 2176/2017 (tabela do ENCOGE)",
                   "https://www.tjrr.jus.br/index.php/fator-de-atualizacao-monetaria"),
    TabelaTribunal("TJTO", IPCR_INPC_IPCA, TEXTO_IPCR_INPC_IPCA, "IN TJTO 13/2025, Anexo II",
                   "https://www.tjto.jus.br/servicos/tabelas-correcao-monetaria"),
    TabelaTribunal("TJPB", INPC_IPCA_LEI, TEXTO_INPC_IPCA_LEI, "padrão da calculadora TJCALC (TJPB); sem ato normativo",
                   "https://app.tjpb.jus.br/tjcalc/paginas/publico/calculo/novoCalculo.jsf"),
    TabelaTribunal("TJRN", INPC_IPCA_LEI, TEXTO_INPC_IPCA_LEI, "Prov. CGJ/RN 154/2016, art. 91, IV",
                   "https://atos-corregedoria.tjrn.jus.br/atos/detalhar/33260"),
    TabelaTribunal("TJSE", INPC_IPCA_LEI, TEXTO_INPC_IPCA_LEI, "padrão da calculadora do TJSE; sem ato normativo",
                   "https://www.tjse.jus.br/portal/servicos/judiciais/calculos-juridicos-e-penas"),
    TabelaTribunal("TJAL", IPCR_INPC, TEXTO_IPCR_INPC, "Prov. CGJ/AL 10/2002 e Tabela Prática TJAL/FUNJURIS",
                   "https://funjuris.tjal.jus.br/?pag=tabelas"),
    TabelaTribunal("TJAM", IPCR_INPC, TEXTO_IPCR_INPC, "Portaria TJAM 1855/2016-PTJ, art. 3º",
                   "https://sistemas.tjam.jus.br/cmonetaria/index.php"),
    TabelaTribunal("TJES", ((date(1999, 6, 1), None, "INPC"),),
                   "INPC desde 06/1999 (antes, IPC-FIPE, que a calculadora não tem; a tabela do tribunal não adotou o IPCA)",
                   "Código de Normas da CGJ-ES, Foro Judicial, art. 312, IV",
                   "https://sistemas.tjes.jus.br/sistemaspublicos/corregedoria/Atm/index.cfm"),
    TabelaTribunal("TJSC", ((date(1994, 7, 1), date(1995, 6, 30), "IPC_R"), (date(1995, 7, 1), date(2024, 8, 29), "INPC"),
                            (date(2024, 8, 30), None, "IPCA")),
                   "IPC-r de 07/1994 a 06/1995, INPC de 07/1995 a 29/08/2024 e IPCA desde 30/08/2024",
                   "Prov. CGJ/SC 13/1995 (INPC); IPCA desde 30/08/2024 (CC, art. 389)",
                   "https://www.tjsc.jus.br/web/corregedoria-geral-da-justica"),
]


# IPC-r, variação mensal (%), IBGE — Números-Índices do IPC-r (LIPCR.XLS; cópia em dados/ibge_ipcr_*).
IPCR_IBGE = {
    date(1994, 7, 1): "6.08", date(1994, 8, 1): "5.46", date(1994, 9, 1): "1.51", date(1994, 10, 1): "1.86",
    date(1994, 11, 1): "3.27", date(1994, 12, 1): "2.19", date(1995, 1, 1): "1.67", date(1995, 2, 1): "0.99",
    date(1995, 3, 1): "1.41", date(1995, 4, 1): "1.92", date(1995, 5, 1): "2.57", date(1995, 6, 1): "1.82",
}
INFO_IPCR = InfoIndice("IPC_R", "IPC-r — variação mensal (IBGE), jul/1994 a jun/1995 (série encerrada)",
                       "IBGE, Números-Índices do IPC-r (LIPCR.XLS)", "correcao", None, URL_IPCR)


def registrar_ipcr(conn) -> None:
    """Grava o IPC-r oficial se ainda faltar (banco novo); não mexe numa série já gravada."""
    from decimal import Decimal

    from calculo_judicial.indices.fatores import recalcular_fatores
    from calculo_judicial.indices.modelo import gravar_taxa, registrar_indice

    if conn.execute("SELECT 1 FROM taxas_mensais WHERE indice_codigo = 'IPC_R' LIMIT 1").fetchone():
        return
    registrar_indice(conn, INFO_IPCR)
    for mes, taxa in IPCR_IBGE.items():
        gravar_taxa(conn, "IPC_R", mes, Decimal(taxa))
    conn.commit()
    recalcular_fatores(conn, "IPC_R")


def _lista(itens: list[str]) -> str:
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]


def tabela_composta(t: TabelaTribunal) -> TabelaComposta:
    return TabelaComposta(t.codigo, [PeriodoComposto(de, ate, indice) for de, ate, indice in t.cadeia])


def info(t: TabelaTribunal) -> InfoIndice:
    """Nome (vai para a memória) com a marca de tabela montada pela calculadora (não é a publicada)."""
    return InfoIndice(
        t.codigo,
        f"Tabela do {t.codigo} montada pela calculadora com índices oficiais (não é a tabela publicada pelo tribunal): "
        f"{t.descricao}",
        f"Montada pela calculadora conforme {t.base}: " + _lista([FONTES[i] for i in dict.fromkeys(i for _, _, i in t.cadeia)]),
        "correcao", None, t.url,
    )
