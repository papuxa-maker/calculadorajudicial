"""Atualização automática dos índices feita pela própria calculadora (sem tarefa do Windows).

Enquanto a calculadora estiver aberta, um processo em segundo plano confere, ao abrir
e depois a cada 30 minutos, se algum índice tem mês novo previsto pelo calendário de
divulgação (``DefinicaoSerie.dia_divulgacao``). Só então consulta o Banco Central:

- nada previsto: não acessa a internet;
- mês previsto e ainda não publicado: tenta de novo a cada 6 horas;
- sem internet ou Banco Central fora do ar/lento: tenta de novo em 30 minutos (os cálculos
  seguem com os índices gravados); a consulta tem tempo-limite curto e é interrompida se o
  site estiver instável, para não prender a rotina por muito tempo;
- valor publicado que não passa na conferência do coletor: não é gravado e aparece como problema;
- índice (ou tabela de tribunal) com 2 ou mais meses de atraso: alerta na tela, em
  ``log/alertas.log`` e, se o SMTP estiver configurado, por e-mail (uma vez por dia; se o
  envio falhar, tenta de novo na conferência seguinte).

Cada conferência fica registrada na tabela ``verificacoes_indices`` do ``indices.db`` (com o
processo que a fez, para reconhecer conferência interrompida quando a calculadora é fechada).
Regra do projeto (CLAUDE.md): o cálculo nunca baixa índice; esta rotina roda separada e o motor
só lê o banco local. Nenhum índice é estimado: o que não foi publicado não é gravado.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from calculo_judicial.datas import mes_anterior, primeiro_dia, proximo_mes, somar_meses
from calculo_judicial.indices import coletor_bcb
from calculo_judicial.indices.coletor_bcb import SERIES_BCB, DefinicaoSerie
from calculo_judicial.indices.modelo import conectar, ultimo_mes_gravado, SERIES_ENCERRADAS

INTERVALO_LACO = timedelta(minutes=30)  # de quanto em quanto tempo o processo acorda para conferir
ESPERA_APOS_FALHA = timedelta(minutes=30)  # sem internet, Banco Central fora do ar, erro: nova tentativa
ESPERA_SEM_NOVIDADE = timedelta(hours=6)  # mês previsto mas ainda não publicado pelo órgão
ANTECEDENCIA_DIAS = 3  # começa a procurar alguns dias antes do dia usual de divulgação
TOLERANCIA_DIAS = 5  # passado isso do dia previsto, o índice "deveria ter saído" (aviso na tela)
ANDAMENTO_MAXIMO = timedelta(minutes=30)  # conferência "em andamento" sem processo identificado, mais velha que isso: interrompida
ANDAMENTO_ABSURDO = timedelta(hours=2)  # nenhuma conferência dura isso: interrompida mesmo com o processo vivo
LIMITE_ATRASO_MESES = 2
RETENCAO_HISTORICO = timedelta(days=90)
TEMPO_LIMITE_CONSULTA = 15.0  # segundos por requisição, na rotina automática e no botão
TENTATIVAS_CONSULTA = 2
ORCAMENTO_CICLO = timedelta(minutes=3)
FALHAS_PARA_INTERROMPER = 2
ENV_DESLIGAR = "CALCULADORA_ATUALIZACAO_AUTOMATICA"  # "0" desliga (usado nos testes)
URL_TESTE_BCB = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados/ultimos/1?formato=json"
FORMATO = "%Y-%m-%dT%H:%M:%S"
SITUACOES_CONCLUIDAS = ("ok", "sem_internet", "bcb_indisponivel", "com_erros", "falhou")
NOMES = {
    "IPCA": "IPCA", "INPC": "INPC", "IPCA_E": "IPCA-E", "IPCA_15": "IPCA-15", "IGPM": "IGP-M", "SELIC": "SELIC",
    "TR": "TR", "TAXA_LEGAL": "taxa legal", "POUPANCA_ANTIGA": "poupança (regra antiga)", "POUPANCA_NOVA": "poupança",
    "JUROS_POUPANCA": "juros da poupança", "TJSP": "tabela do TJSP", "JF": "tabela da Justiça Federal",
    "JF_PREV": "tabela previdenciária da Justiça Federal",
}

_TRAVA = threading.Lock()  # uma verificação por vez neste processo (automática ou botão "Atualizar agora")
_CONSULTANDO = threading.Event()  # ligado só enquanto o Banco Central está sendo consultado (aviso na tela)


def consultando_agora() -> bool:
    return _CONSULTANDO.is_set()


def nome(codigo: str) -> str:
    return NOMES.get(codigo, codigo)


def _lista(codigos: list[str], maximo: int = 4) -> str:
    nomes = [nome(c) for c in codigos]
    if len(nomes) <= maximo:
        return ", ".join(nomes)
    return ", ".join(nomes[:maximo]) + f" e mais {len(nomes) - maximo}"


def _alerta(mensagem: str) -> None:
    coletor_bcb.registrar_alerta("atualizacao_automatica", mensagem)  # nunca levanta exceção


@dataclass
class Verificacao:
    id: int
    inicio: datetime
    fim: datetime | None
    origem: str  # 'automatica' | 'manual'
    situacao: str  # 'em_andamento' | 'ok' | 'sem_internet' | 'bcb_indisponivel' | 'com_erros' | 'falhou' | 'interrompida'
    novos: dict[str, list[str]] = field(default_factory=dict)  # índice -> meses gravados ("2026-09")
    erros: list[str] = field(default_factory=list)
    atrasados: list[str] = field(default_factory=list)
    email_enviado: bool = False
    processo: int | None = None
    alerta: bool = False


@dataclass
class IndiceDevido:
    """Índice com mês novo previsto: ``mes`` já deveria (ou pode) ter sido divulgado."""

    codigo: str
    mes: date
    previsto_para: date


def desligada_por_ambiente() -> bool:
    return os.environ.get(ENV_DESLIGAR, "1").strip().lower() in ("0", "nao", "não", "false", "desligada")


def testar_conexao_bcb(timeout: float = 10) -> tuple[str, str]:
    """Situação do acesso ao Banco Central: ('ok' | 'sem_internet' | 'bcb_indisponivel', detalhe).

    Falha de conexão/DNS = sem internet; erro HTTP, demora ou resposta que não é a lista JSON
    esperada = o site do Banco Central fora do ar ou lento.
    """
    import httpx

    try:
        resposta = httpx.get(URL_TESTE_BCB, timeout=timeout, headers={"Accept": "application/json"})
    except httpx.ConnectError as erro:
        return "sem_internet", str(erro) or type(erro).__name__
    except httpx.HTTPError as erro:  # timeout, protocolo...
        return "bcb_indisponivel", str(erro) or type(erro).__name__
    if resposta.status_code != 200:
        return "bcb_indisponivel", f"HTTP {resposta.status_code}"
    try:
        dados = resposta.json()
    except ValueError:
        return "bcb_indisponivel", "resposta sem JSON"
    if not isinstance(dados, list):
        return "bcb_indisponivel", "resposta inesperada"
    return "ok", "HTTP 200"


def api_bcb_acessivel(timeout: float = 10) -> tuple[bool, str]:
    situacao, detalhe = testar_conexao_bcb(timeout)
    return situacao == "ok", detalhe


def _situacao_conexao(resultado) -> tuple[str, str]:
    """Aceita o formato novo ('ok'|...) e o antigo (True/False) de teste de conexão."""
    primeiro, detalhe = resultado
    if isinstance(primeiro, bool):
        return ("ok" if primeiro else "sem_internet"), detalhe
    return primeiro, detalhe


class BaixadorComOrcamento:
    """Downloads da conferência: tempo-limite curto, poucas tentativas e um limite total por ciclo.

    Com o Banco Central lento ou instável, a conferência termina em poucos minutos como
    'bcb_indisponivel' em vez de prender a rotina por uma hora; a próxima tentativa vem em 30 minutos.
    """

    def __init__(self, timeout: float = TEMPO_LIMITE_CONSULTA, tentativas: int = TENTATIVAS_CONSULTA,
                 orcamento: timedelta = ORCAMENTO_CICLO, falhas_max: int = FALHAS_PARA_INTERROMPER, baixar=None):
        self.timeout = timeout
        self.tentativas = tentativas
        self.orcamento = orcamento.total_seconds()
        self.falhas_max = falhas_max
        self.inicio = time.monotonic()
        self.falhas = 0
        self.interrompido = False
        self._baixar = baixar or coletor_bcb.baixar_serie

    def __call__(self, codigo_sgs, inicio, fim, **opcoes):
        import httpx

        if self.interrompido or time.monotonic() - self.inicio > self.orcamento:
            self.interrompido = True
            raise coletor_bcb.ErroAPI("conferência interrompida: o Banco Central está lento ou fora do ar")
        cliente = httpx.Client(timeout=httpx.Timeout(self.timeout, connect=10.0), follow_redirects=True,
                               headers={"Accept": "application/json", "User-Agent": "calculo-judicial/0.1"})
        try:
            return self._baixar(codigo_sgs, inicio, fim, cliente=cliente, tentativas=self.tentativas, **opcoes)
        except Exception as erro:
            texto = str(erro)
            transitorio = isinstance(erro, httpx.HTTPError) or any(
                t in texto for t in ("falha de rede", "HTTP 5", "sem JSON", "HTTP 408", "HTTP 429", "timed out"))
            if transitorio:
                self.falhas += 1
                if self.falhas >= self.falhas_max:
                    self.interrompido = True
            raise
        finally:
            cliente.close()


# ---------------------------------------------------------------------------
# Calendário: o que já deveria ter sido divulgado
# ---------------------------------------------------------------------------


def data_prevista(definicao: DefinicaoSerie, mes: date) -> date:
    """Dia aproximado em que o órgão divulga a taxa do ``mes`` (no mês seguinte)."""
    seguinte = proximo_mes(primeiro_dia(mes))
    return seguinte.replace(day=max(1, min(definicao.dia_divulgacao, 28)))


def proximo_mes_esperado(conn: sqlite3.Connection, definicao: DefinicaoSerie) -> date:
    ultimo = ultimo_mes_gravado(conn, definicao.codigo)
    return somar_meses(ultimo, 1) if ultimo else primeiro_dia(definicao.inicio)


def indices_devidos(conn: sqlite3.Connection, hoje: date, series: list[DefinicaoSerie] | None = None) -> list[IndiceDevido]:
    """Índices cujo próximo mês já pode ter sido divulgado (com alguns dias de antecedência)."""
    devidos = []
    limite = mes_anterior(hoje)  # o mês corrente nunca é gravado (pode estar incompleto)
    for definicao in series or SERIES_BCB:
        mes = proximo_mes_esperado(conn, definicao)
        if mes > limite:
            continue
        previsto = data_prevista(definicao, mes)
        if mes < limite or hoje >= previsto - timedelta(days=ANTECEDENCIA_DIAS):
            devidos.append(IndiceDevido(definicao.codigo, mes, previsto))
    return devidos


def proximas_divulgacoes(conn: sqlite3.Connection, hoje: date, series: list[DefinicaoSerie] | None = None) -> list[IndiceDevido]:
    """Próximo mês de cada índice ainda não previsto, com a data aproximada de divulgação."""
    saida = []
    devidos = {d.codigo for d in indices_devidos(conn, hoje, series)}
    for definicao in series or SERIES_BCB:
        if definicao.codigo in devidos:
            continue
        mes = proximo_mes_esperado(conn, definicao)
        saida.append(IndiceDevido(definicao.codigo, mes, data_prevista(definicao, mes)))
    return sorted(saida, key=lambda d: d.previsto_para)


def indices_atrasados(conn: sqlite3.Connection, hoje: date, series: list[DefinicaoSerie] | None = None) -> list[str]:
    """Índices do BCB com ``LIMITE_ATRASO_MESES`` ou mais meses faltando até o mês anterior ao atual."""
    linhas = coletor_bcb.status(conn, hoje=hoje, series=series)
    codigos = {d.codigo for d in (series or SERIES_BCB)}
    return [l.codigo for l in linhas if l.codigo in codigos and l.meses_faltando >= LIMITE_ATRASO_MESES]


def tabelas_tribunal_atrasadas(conn: sqlite3.Connection, hoje: date) -> list[tuple[str, date | None]]:
    """Tabelas importadas de arquivo (TJSP, JF...) com 2+ meses de atraso: (código, último mês)."""
    compostas = {n for (n,) in conn.execute("SELECT nome FROM tabelas_compostas")}
    saida = []
    for codigo, codigo_sgs in conn.execute("SELECT codigo, codigo_sgs FROM indices ORDER BY codigo"):
        if codigo_sgs is not None or codigo in compostas or codigo in SERIES_ENCERRADAS:
            continue
        ultimo = ultimo_mes_gravado(conn, codigo)
        if ultimo is None:
            continue
        from calculo_judicial.datas import indice_mes

        if indice_mes(mes_anterior(hoje)) - indice_mes(ultimo) >= LIMITE_ATRASO_MESES:
            saida.append((codigo, ultimo))
    return saida


# ---------------------------------------------------------------------------
# Histórico (tabela verificacoes_indices)
# ---------------------------------------------------------------------------

_COLUNAS = "id, inicio, fim, origem, situacao, novos, erros, atrasados, email_enviado, processo, alerta"


def _ler(linha) -> Verificacao:
    return Verificacao(
        id=linha[0],
        inicio=datetime.strptime(linha[1], FORMATO),
        fim=datetime.strptime(linha[2], FORMATO) if linha[2] else None,
        origem=linha[3],
        situacao=linha[4],
        novos=json.loads(linha[5] or "{}"),
        erros=json.loads(linha[6] or "[]"),
        atrasados=json.loads(linha[7] or "[]"),
        email_enviado=bool(linha[8]),
        processo=linha[9],
        alerta=bool(linha[10]),
    )


def historico(conn: sqlite3.Connection, limite: int = 10) -> list[Verificacao]:
    linhas = conn.execute(f"SELECT {_COLUNAS} FROM verificacoes_indices ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
    return [_ler(l) for l in linhas]


def ultima_verificacao(conn: sqlite3.Connection) -> Verificacao | None:
    """Última conferência que terminou (em andamento e interrompidas ficam de fora)."""
    marcadores = ",".join("?" * len(SITUACOES_CONCLUIDAS))
    linha = conn.execute(f"SELECT {_COLUNAS} FROM verificacoes_indices WHERE situacao IN ({marcadores}) ORDER BY id DESC LIMIT 1",
                         SITUACOES_CONCLUIDAS).fetchone()
    return _ler(linha) if linha else None


def ultima_com_novidade(conn: sqlite3.Connection) -> Verificacao | None:
    linha = conn.execute(f"SELECT {_COLUNAS} FROM verificacoes_indices WHERE novos != '{{}}' ORDER BY id DESC LIMIT 1").fetchone()
    return _ler(linha) if linha else None


def _processo_vivo(pid: int | None) -> bool:
    if pid is None:
        return False
    if pid == os.getpid():
        return _TRAVA.locked()
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        alca = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not alca:
            return False
        try:
            codigo = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(alca, ctypes.byref(codigo))) and codigo.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(alca)
    try:
        os.kill(pid, 0)  # no Windows os.kill encerraria o processo: só em POSIX
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _em_andamento_vivas(conn: sqlite3.Connection, agora: datetime) -> list[Verificacao]:
    """Conferências 'em andamento' que de fato estão rodando (em outro processo ou neste)."""
    vivas = []
    for v in (_ler(l) for l in conn.execute(f"SELECT {_COLUNAS} FROM verificacoes_indices WHERE situacao='em_andamento'")):
        idade = agora - v.inicio
        if v.processo is None:
            if idade < ANDAMENTO_MAXIMO:
                vivas.append(v)
        elif idade < ANDAMENTO_ABSURDO and _processo_vivo(v.processo):
            vivas.append(v)
    return vivas


def _marcar_interrompidas(conn: sqlite3.Connection, agora: datetime) -> None:
    """Conferência 'em andamento' cujo processo acabou (calculadora fechada no meio): fica 'interrompida'."""
    vivas = {v.id for v in _em_andamento_vivas(conn, agora)}
    for (id_,) in conn.execute("SELECT id FROM verificacoes_indices WHERE situacao='em_andamento'").fetchall():
        if id_ not in vivas:
            conn.execute("UPDATE verificacoes_indices SET situacao='interrompida', fim=? WHERE id=?", (agora.strftime(FORMATO), id_))
    conn.commit()


def _registrar_inicio(conn: sqlite3.Connection, agora: datetime, origem: str) -> int:
    cur = conn.execute(
        "INSERT INTO verificacoes_indices (inicio, origem, situacao, processo) VALUES (?, ?, 'em_andamento', ?)",
        (agora.strftime(FORMATO), origem, os.getpid()),
    )
    conn.commit()
    return int(cur.lastrowid)


def _registrar_fim(conn: sqlite3.Connection, v: Verificacao) -> None:
    conn.execute(
        "UPDATE verificacoes_indices SET fim=?, situacao=?, novos=?, erros=?, atrasados=?, email_enviado=?, alerta=? WHERE id=?",
        (v.fim.strftime(FORMATO) if v.fim else None, v.situacao, json.dumps(v.novos, ensure_ascii=False),
         json.dumps(v.erros, ensure_ascii=False), json.dumps(v.atrasados), int(v.email_enviado), int(v.alerta), v.id),
    )
    conn.commit()


def _podar_historico(conn: sqlite3.Connection, agora: datetime) -> None:
    conn.execute("DELETE FROM verificacoes_indices WHERE inicio < ? AND situacao != 'em_andamento'",
                 ((agora - RETENCAO_HISTORICO).strftime(FORMATO),))
    conn.commit()


def _alerta_ja_dado_hoje(conn: sqlite3.Connection, hoje: date, atrasados: list[str], id_atual: int, por_email: bool) -> bool:
    """O alerta do dia já foi dado: por e-mail (se o SMTP estiver configurado) ou no log (se não estiver)."""
    for v in historico(conn, 100):
        if v.id == id_atual or v.inicio.date() != hoje or sorted(v.atrasados) != sorted(atrasados):
            continue
        if (por_email and v.email_enviado) or (not por_email and v.alerta):
            return True
    return False


# ---------------------------------------------------------------------------
# Decisão e execução
# ---------------------------------------------------------------------------


def _espera(situacao: str) -> timedelta:
    return ESPERA_SEM_NOVIDADE if situacao == "ok" else ESPERA_APOS_FALHA


def precisa_verificar(conn: sqlite3.Connection, agora: datetime, series: list[DefinicaoSerie] | None = None) -> tuple[bool, str]:
    """Se é hora de consultar o Banco Central, e por quê."""
    devidos = indices_devidos(conn, agora.date(), series)
    if not devidos:
        return False, "nenhum índice novo previsto para hoje"
    if _em_andamento_vivas(conn, agora):
        return False, "verificação em andamento"
    ultima = ultima_verificacao(conn)
    if ultima is None:
        return True, "primeira verificação"
    espera = _espera(ultima.situacao)
    if agora - ultima.inicio >= espera:
        return True, "meses previstos: " + ", ".join(f"{d.codigo} {d.mes:%m/%Y}" for d in devidos)
    return False, f"próxima tentativa por volta de {(ultima.inicio + espera):%d/%m %H:%M}"


def verificar_e_atualizar(
    caminho_db: str | Path | None = None,
    agora: datetime | None = None,
    origem: str = "automatica",
    series: list[DefinicaoSerie] | None = None,
    baixar=None,
    testar_conexao=None,
    enviar=None,
    forcar: bool = False,
) -> Verificacao | None:
    """Uma conferência completa: decide, baixa o que houver de novo, registra e alerta.

    Devolve ``None`` se não era hora (e ``forcar`` é falso) ou se outra conferência está em andamento.
    A conferência sempre termina registrada (mesmo com erro inesperado): nunca fica "em andamento" à toa.
    """
    if not _TRAVA.acquire(blocking=False):
        return None
    try:
        relogio_real = agora is None
        agora = agora or datetime.now()
        conn = conectar(caminho_db)
        try:
            _marcar_interrompidas(conn, agora)
            if _em_andamento_vivas(conn, agora):
                return None  # outra calculadora (outro processo) está conferindo agora
            devidos = indices_devidos(conn, agora.date(), series)
            if not forcar:
                sim, _motivo = precisa_verificar(conn, agora, series)
                if not sim:
                    return None
            v = Verificacao(id=_registrar_inicio(conn, agora, origem), inicio=agora, fim=None, origem=origem,
                            situacao="em_andamento", processo=os.getpid())
            _CONSULTANDO.set()
            try:
                _conferir(conn, v, agora, series, devidos, forcar, baixar, testar_conexao, enviar)
            except Exception as erro:  # noqa: BLE001 - registrado e mostrado na tela; nunca "em andamento" para sempre
                v.situacao = "falhou"
                v.erros.append(f"falha inesperada: {type(erro).__name__}: {erro}")
                _alerta(f"conferência falhou: {type(erro).__name__}: {erro}")
            finally:
                _CONSULTANDO.clear()
                v.fim = datetime.now() if relogio_real else agora
                try:
                    _registrar_fim(conn, v)
                    _podar_historico(conn, agora)
                except sqlite3.Error as erro:
                    _alerta(f"não foi possível registrar o fim da conferência: {erro}")
            return v
        finally:
            conn.close()
    finally:
        _TRAVA.release()


def _conferir(conn, v: Verificacao, agora: datetime, series, devidos, forcar, baixar, testar_conexao, enviar) -> None:
    situacao, detalhe = _situacao_conexao((testar_conexao or testar_conexao_bcb)())
    if situacao != "ok":
        v.situacao = situacao
        v.erros = [("sem acesso à internet" if situacao == "sem_internet" else "o site do Banco Central não respondeu")
                   + f" ({detalhe})"]
    else:
        baixador = baixar or BaixadorComOrcamento()
        # rotina automática: só as séries com mês previsto (as outras não têm o que baixar); botão: todas
        alvo = series if forcar or not devidos else [d for d in (series or SERIES_BCB) if d.codigo in {x.codigo for x in devidos}]
        resultados = coletor_bcb.atualizar(conn, hoje=agora.date(), series=alvo, baixar=baixador)
        for r in resultados:
            if r.gravados:
                v.novos[r.codigo] = [m.strftime("%Y-%m") for m in r.gravados]
            if r.erro:
                v.erros.append(f"{nome(r.codigo)}: {r.erro}")
            if r.rejeitados:
                v.erros.append(f"{nome(r.codigo)}: o Banco Central publicou, mas o valor não passou na conferência e não foi "
                               f"gravado ({'; '.join(r.rejeitados[:3])})")
        if isinstance(baixador, BaixadorComOrcamento) and baixador.interrompido:
            v.situacao = "bcb_indisponivel"
        else:
            v.situacao = "com_erros" if v.erros else "ok"
    if v.novos:
        _alerta(f"novos meses gravados: {resumo_novos(v.novos)}")
    v.atrasados = indices_atrasados(conn, agora.date(), series) + [c for c, _ in tabelas_tribunal_atrasadas(conn, agora.date())]
    if v.atrasados:
        _alertar_atraso(conn, v, agora.date(), series, enviar)


def _alertar_atraso(conn, v: Verificacao, hoje: date, series, enviar) -> None:
    """Alerta de índice atrasado: no log e, se o SMTP estiver configurado, por e-mail (uma vez por dia)."""
    from calculo_judicial.indices import agendador

    config = agendador.config_smtp()
    if _alerta_ja_dado_hoje(conn, hoje, v.atrasados, v.id, por_email=config is not None):
        return
    _alerta(f"{len(v.atrasados)} índice(s) com {LIMITE_ATRASO_MESES}+ meses de atraso: {_lista(v.atrasados, 20)}")
    v.alerta = True
    if config is None:
        return
    linhas = [l for l in coletor_bcb.status(conn, hoje=hoje) if l.codigo in v.atrasados]
    msg = agendador.montar_email(linhas, hoje, config["remetente"], config["destinatarios"])
    try:
        (enviar or agendador.enviar_email)(msg, config)
        v.email_enviado = True
    except Exception as erro:  # noqa: BLE001 - falha de SMTP vira alerta e nova tentativa na próxima conferência
        v.erros.append(f"e-mail de alerta não enviado: {erro}")
        if v.situacao == "ok":
            v.situacao = "com_erros"  # nova tentativa (e novo e-mail) em 30 minutos, não em 6 horas
        _alerta(f"falha ao enviar e-mail de alerta: {erro}")


# ---------------------------------------------------------------------------
# Processo em segundo plano
# ---------------------------------------------------------------------------


class Atualizador:
    """Confere os índices ao iniciar e a cada ``intervalo`` enquanto a calculadora estiver aberta."""

    def __init__(self, caminho_db: str | Path | None = None, intervalo: timedelta = INTERVALO_LACO, executar=None):
        self.caminho_db = caminho_db
        self.intervalo = intervalo
        self._executar = executar or verificar_e_atualizar
        self._acordar = threading.Event()
        self._parar = threading.Event()
        self._forcar = False
        self._thread: threading.Thread | None = None
        self.ultimo_resultado: Verificacao | None = None
        self.ultimo_erro: str | None = None

    def iniciar(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._parar.clear()
        self._thread = threading.Thread(target=self._laco, name="atualizacao-indices", daemon=True)
        self._thread.start()

    def solicitar_agora(self) -> None:
        """Botão "Atualizar agora": verifica já, mesmo sem mês previsto."""
        self._forcar = True
        self._acordar.set()
        self.iniciar()

    def parar(self) -> None:
        self._parar.set()
        self._acordar.set()

    def vivo(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def rodando(self) -> bool:
        """Consultando o Banco Central neste momento (conferir só o calendário não conta)."""
        return consultando_agora()

    def _laco(self) -> None:
        while not self._parar.is_set():
            forcar, self._forcar = self._forcar, False
            try:
                resultado = self._executar(caminho_db=self.caminho_db, origem="manual" if forcar else "automatica",
                                           forcar=forcar)
                if resultado is not None:
                    self.ultimo_resultado = resultado
                self.ultimo_erro = None
            except Exception as erro:  # noqa: BLE001 - o laço nunca morre; o erro aparece no painel
                self.ultimo_erro = f"{type(erro).__name__}: {erro}"
                _alerta(f"falha inesperada na rotina automática: {self.ultimo_erro}")
            self._acordar.wait(self.intervalo.total_seconds())
            self._acordar.clear()


# ---------------------------------------------------------------------------
# Texto para a tela
# ---------------------------------------------------------------------------


@dataclass
class Painel:
    nivel: str  # 'ok' | 'info' | 'aviso' | 'erro'
    titulo: str
    detalhes: list[str] = field(default_factory=list)


def _mes(texto: str) -> str:
    return f"{texto[5:7]}/{texto[:4]}"


def resumo_novos(novos: dict[str, list[str]]) -> str:
    """'IPCA 09/2026; SELIC 09/2026' — ou, quando são muitos meses, 'IPCA: 387 meses (07/1994 a 09/2026)'."""
    partes = []
    for codigo, meses in novos.items():
        if len(meses) <= 3:
            partes.append(f"{nome(codigo)} {', '.join(_mes(m) for m in meses)}")
        else:
            partes.append(f"{nome(codigo)}: {len(meses)} meses ({_mes(meses[0])} a {_mes(meses[-1])})")
    return "; ".join(partes)


def _plugin() -> bool:
    return bool(os.environ.get("CALCULADORA_VERSAO"))


def painel(conn: sqlite3.Connection, agora: datetime, rodando: bool = False, ligada: bool = True,
           series: list[DefinicaoSerie] | None = None, erro_rotina: str | None = None) -> Painel:
    """Resumo da situação dos índices para a barra lateral (função pura, testável)."""
    hoje = agora.date()
    ultima = ultima_verificacao(conn)
    atrasados = indices_atrasados(conn, hoje, series)
    devidos = indices_devidos(conn, hoje, series)
    tribunal = tabelas_tribunal_atrasadas(conn, hoje)
    detalhes = []
    novidade = ultima_com_novidade(conn)
    if novidade is not None and (agora - novidade.inicio) < timedelta(days=7):
        detalhes.append("Novos meses gravados: " + resumo_novos(novidade.novos) + ".")
    proximos = [p for p in proximas_divulgacoes(conn, hoje, series) if p.codigo in ("IPCA", "INPC", "SELIC", "TAXA_LEGAL")]
    if proximos:
        p = proximos[0]
        mesmos = [q.codigo for q in proximos if q.previsto_para == p.previsto_para and q.mes == p.mes]
        detalhes.append(f"Próximo: {' e '.join(nome(c) for c in mesmos)} de {p.mes:%m/%Y}, previsto por volta de "
                        f"{p.previsto_para:%d/%m/%Y}.")
    if tribunal:
        onde = "chegam nas atualizações do plugin" if _plugin() else "importe a tabela nova na tela Índices"
        detalhes.append("Tabelas de tribunal desatualizadas (só importam se você usar essas tabelas): " + "; ".join(
            f"{nome(c)} até {u:%m/%Y}" for c, u in tribunal) + f" — não vêm do Banco Central: {onde}.")
    if not ligada:
        detalhes.insert(0, "Atualização automática desligada; use o botão na tela Índices.")
    conferidos = [f"Conferidos em {ultima.inicio:%d/%m/%Y às %H:%M}."] if ultima else []

    if erro_rotina:
        return Painel("aviso", "A conferência automática teve um problema", [
            f"{erro_rotina}. A calculadora tenta de novo em até 30 minutos; os cálculos usam os índices já gravados."] + detalhes)
    if rodando or _em_andamento_vivas(conn, agora):
        return Painel("info", "Atualizando índices do Banco Central…", detalhes)
    if atrasados:
        return Painel("erro", f"Índice atrasado: {_lista(atrasados)}", [
            f"Faltam {LIMITE_ATRASO_MESES} ou mais meses. A calculadora continua tentando; veja a tela Índices."] + detalhes)
    if ultima is not None and devidos and ultima.situacao in ("sem_internet", "bcb_indisponivel"):
        titulo = "Sem internet na última conferência" if ultima.situacao == "sem_internet" else \
            "O site do Banco Central não respondeu"
        return Painel("aviso", titulo, [
            f"Tentativa em {ultima.inicio:%d/%m/%Y às %H:%M}; nova tentativa em até 30 minutos. "
            "Os cálculos usam os índices já gravados."] + detalhes)
    if ultima is not None and ultima.situacao in ("com_erros", "falhou") and agora - ultima.inicio < timedelta(days=1):
        return Painel("aviso", "A última conferência teve problemas",
                      [f"Em {ultima.inicio:%d/%m/%Y às %H:%M}: {ultima.erros[0] if ultima.erros else 'erro'}"] + detalhes)
    atrasando = [d for d in devidos if hoje > d.previsto_para + timedelta(days=TOLERANCIA_DIAS)]
    if atrasando:
        d = atrasando[0]
        return Painel("aviso", f"{nome(d.codigo)} de {d.mes:%m/%Y} ainda não chegou", conferidos + [
            f"Deveria ter saído por volta de {d.previsto_para:%d/%m/%Y}. A calculadora continua tentando sozinha "
            f"({_lista([x.codigo for x in atrasando])})."] + detalhes)
    if devidos:
        meses = [f"{nome(d.codigo)} {d.mes:%m/%Y}" for d in devidos]
        texto = ", ".join(meses[:4]) + (f" e mais {len(meses) - 4}" if len(meses) > 4 else "")
        return Painel("info", "Aguardando divulgação de índice novo", conferidos + [
            f"{texto}: previsto para estes dias; a calculadora confere sozinha até sair."] + detalhes)
    if tribunal:
        return Painel("aviso", "Índices do Banco Central em dia", conferidos + detalhes)
    return Painel("ok", "Índices em dia", conferidos + detalhes)


def projecoes_ja_divulgadas(repo, fontes: list[tuple[str, list[date]]]) -> list[str]:
    """Meses usados como projeção cujo índice oficial já está no banco (o cálculo deve ser refeito)."""
    from calculo_judicial.indices.modelo import IndiceAusente

    saida = []
    for codigo, meses in fontes:
        for mes in meses:
            try:
                repo.taxa(codigo, mes)  # sem permitir projeção: só o valor oficial
            except (IndiceAusente, KeyError, LookupError):
                continue
            saida.append(f"{codigo} {mes:%m/%Y}")
    return saida
