"""Linha de comando da calculadora (usada pela skill do Claude Code e por quem preferir o terminal).

    python -m calculo_judicial.cli status
    python -m calculo_judicial.cli indices [--forcar]
    python -m calculo_judicial.cli modelo [--area civel]
    python -m calculo_judicial.cli conferir PEDIDO.json
    python -m calculo_judicial.cli calcular PEDIDO.json [--saida PASTA] [--nao-salvar] [--json]
    python -m calculo_judicial.cli salvos [--n 10]
    python -m calculo_judicial.cli atualizar-salvo ID [--data dd/mm/aaaa] [--saida PASTA]
    python -m calculo_judicial.cli configurar [--nome ...] [--oab ...] [--endereco ...] [--logo arquivo.png] [--ressalva ...]
    python -m calculo_judicial.cli abrir [--porta 8765]  |  fechar

Códigos de saída: 0 ok; 2 pedido com erro; 3 o cálculo não roda (índice ausente, regra sem dado); 1 falha inesperada.
Os dados (índices, cálculos salvos, configurações) ficam na pasta de CALCULADORA_DADOS, ou na raiz do projeto.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

from calculo_judicial import pedido as mod_pedido
from calculo_judicial.app import configuracoes, formulario, persistencia, serializacao
from calculo_judicial.datas import formatar_data
from calculo_judicial.decimais import formatar_brl, para_decimal, q10
from calculo_judicial.indices import atualizacao_automatica as auto
from calculo_judicial.indices.modelo import (
    PASTA_DADOS, IndiceAusente, IndiceDesconhecido, RepositorioSQLite, caminho_db, conectar, nome_curto,
)
from calculo_judicial.motor import ErroRegra, ParametroInvalido

VERSAO = os.environ.get("CALCULADORA_VERSAO", "desenvolvimento")
OK, FALHA, ERRO_PEDIDO, NAO_RODA = 0, 1, 2, 3


def _imprimir(texto: str = "") -> None:
    print(texto, flush=True)


# ---------------------------------------------------------------------------
# Índices
# ---------------------------------------------------------------------------


def atualizar_indices(forcar: bool = False, silencioso: bool = False) -> list[str]:
    """Confere o calendário e, se houver mês novo previsto (ou se ``forcar``), busca no Banco Central."""
    linhas = []
    v = auto.verificar_e_atualizar(origem="manual" if forcar else "automatica", forcar=forcar)
    if v is not None:
        if v.situacao == "sem_internet":
            linhas.append("Sem acesso ao Banco Central agora: o cálculo usa os índices já gravados.")
        elif v.novos:
            linhas.append("Índices novos gravados: " + "; ".join(
                f"{c} {', '.join(m[5:] + '/' + m[:4] for m in ms)}" for c, ms in v.novos.items()))
        elif forcar:
            linhas.append("Conferido no Banco Central: nenhum mês novo publicado.")
        linhas.extend(f"Erro: {e}" for e in v.erros if v.situacao != "sem_internet")
    conn = conectar()
    try:
        painel = auto.painel(conn, datetime.now())
    finally:
        conn.close()
    linhas.append(f"Situação dos índices: {painel.titulo}.")
    linhas.extend(f"  {d}" for d in painel.detalhes)
    if not silencioso:
        for l in linhas:
            _imprimir(l)
    return linhas


def cmd_indices(args) -> int:
    atualizar_indices(forcar=args.forcar)
    return OK


def cmd_status(args) -> int:
    _imprimir(f"Calculadora Judicial {VERSAO}")
    _imprimir(f"Pasta de dados: {PASTA_DADOS}")
    _imprimir(f"Banco de índices: {caminho_db()}")
    _imprimir(f"Relatórios gravados em: {configuracoes.carregar().pasta_saida}")
    conn = conectar()
    try:
        repo = RepositorioSQLite(conn=conn)
        ultimos = []
        for codigo in ("IPCA", "INPC", "IPCA_E", "IPCA_15", "SELIC", "TR", "TAXA_LEGAL", "JUROS_POUPANCA", "TJSP", "JF"):
            try:
                ultimo = repo.ultimo_mes(codigo)
            except Exception:  # noqa: BLE001 - índice não cadastrado nesta instalação
                ultimo = None
            if ultimo:
                ultimos.append(f"{nome_curto(codigo)} {ultimo:%m/%Y}")
        _imprimir("Último mês por índice: " + " · ".join(ultimos))
        painel = auto.painel(conn, datetime.now())
        _imprimir(f"Situação: {painel.titulo}")
    finally:
        conn.close()
    conn = persistencia.conectar()
    try:
        _imprimir(f"Cálculos salvos: {len(persistencia.listar(conn))}")
    finally:
        conn.close()
    return OK


# ---------------------------------------------------------------------------
# Pedido: modelo, conferência e cálculo
# ---------------------------------------------------------------------------


def cmd_modelo(args) -> int:
    _imprimir(json.dumps(mod_pedido.modelo_pedido(args.area), ensure_ascii=False, indent=2))
    return OK


def _ler(caminho: str):
    try:
        return mod_pedido.ler_arquivo_pedido(Path(caminho))
    except mod_pedido.ErroPedido as erro:
        _imprimir("O pedido tem problemas (corrija e confira de novo):")
        for e in erro.erros:
            _imprimir(f"  ✗ {e}")
        return None


def cmd_conferir(args) -> int:
    pedido = _ler(args.pedido)
    if pedido is None:
        return ERRO_PEDIDO
    if not args.sem_atualizar:
        atualizar_indices(silencioso=False)
        _imprimir("")
    conn = conectar()
    try:
        texto, problemas = mod_pedido.texto_conferencia(pedido, RepositorioSQLite(conn=conn))
    finally:
        conn.close()
    _imprimir(texto)
    return NAO_RODA if problemas else OK


def _executar(pedido, saida: str | None, salvar: bool, como_json: bool, comando: str = "pedido") -> int:
    conn = conectar()
    try:
        memoria = mod_pedido.calcular_pedido(pedido, RepositorioSQLite(conn=conn))
    except IndiceAusente as erro:
        _imprimir("O cálculo não rodou: " + mod_pedido._texto_indice_ausente(erro, pedido.params, comando))
        return NAO_RODA
    except IndiceDesconhecido as erro:
        _imprimir(f"O cálculo não rodou: a tabela {erro} não está na calculadora. Veja as disponíveis com 'tabelas' ou "
                  "importe a do tribunal com 'importar'.")
        return NAO_RODA
    except (ErroRegra, ParametroInvalido) as erro:
        _imprimir(f"O cálculo não rodou: {erro}")
        return NAO_RODA
    finally:
        conn.close()
    config = configuracoes.carregar()
    pasta = _pasta(saida or config.pasta_saida)
    numero = None
    if salvar:  # salvo antes dos arquivos: uma pasta de saída inacessível não faz perder o cálculo
        c = persistencia.conectar()
        try:
            numero = persistencia.salvar(c, pedido.processo, memoria.parametros.area, memoria.parametros.data_calculo.isoformat(),
                                         format(memoria.total, "f"), mod_pedido.entrada_json(pedido),
                                         serializacao.memoria_para_json(memoria))
        finally:
            c.close()
    try:
        arquivos = mod_pedido.gravar_arquivos(memoria, pedido, pasta, config)
    except OSError as erro:
        _imprimir(f"O cálculo foi feito (total {formatar_brl(memoria.total)})"
                  + (f" e salvo como nº {numero}" if numero else "")
                  + f", mas não consegui gravar o PDF e o Excel em {pasta}: {erro}. Use --saida com outra pasta ou "
                    "'configurar --pasta-saida'" + (f" e depois 'atualizar-salvo {numero}' para gerar os relatórios." if numero else "."))
        return FALHA
    if como_json:
        _imprimir(json.dumps({
            "total": format(memoria.total, "f"), "total_exibido": formatar_brl(memoria.total), "calculo_salvo": numero,
            "arquivos": {k: str(v) for k, v in arquivos.items()},
            "resumo": {nome: format(q10(valor), "f") for nome, valor in memoria.resumo.linhas()},
            "resumo_exibido": {nome: formatar_brl(valor) for nome, valor in memoria.resumo.linhas()},
            "parcelas": [{"descricao": mp.parcela.descricao, "valor_original": format(mp.valor_original, "f"),
                          "principal_corrigido": format(mp.principal_corrigido, "f"), "juros": format(mp.juros, "f"),
                          "total": format(mp.total, "f"), "total_exibido": formatar_brl(mp.total),
                          "exibido": {"valor_original": formatar_brl(mp.valor_original),
                                      "principal_corrigido": formatar_brl(mp.principal_corrigido),
                                      "juros": formatar_brl(mp.juros), "total": formatar_brl(mp.total)}}
                         for mp in memoria.parcelas],
            "avisos": memoria.avisos,
            "projecoes": {f.codigo: [m.isoformat() for m in f.meses_projecao] for f in memoria.fontes if f.meses_projecao},
        }, ensure_ascii=False, indent=2))
        return OK
    _imprimir(mod_pedido.texto_resultado(memoria))
    _imprimir("")
    _imprimir("Memória de cálculo:")
    _imprimir(f"  PDF:   {arquivos['pdf']}")
    _imprimir(f"  Excel: {arquivos['excel']}")
    if numero:
        _imprimir(f"Cálculo salvo nº {numero} (para atualizar depois: atualizar-salvo {numero}).")
    return OK


def _pasta(caminho: str) -> Path:
    """Pasta de saída absoluta ("~" e caminhos relativos não dependem da pasta em que o comando roda)."""
    return Path(caminho).expanduser().resolve()


def cmd_calcular(args) -> int:
    pedido = _ler(args.pedido)
    if pedido is None:
        return ERRO_PEDIDO
    return _executar(pedido, args.saida, not args.nao_salvar, args.json)


def cmd_salvos(args) -> int:
    conn = persistencia.conectar()
    try:
        salvos = persistencia.listar(conn)[: args.n]
    finally:
        conn.close()
    if not salvos:
        _imprimir("Nenhum cálculo salvo ainda.")
        return OK
    for c in salvos:
        _imprimir(f"nº {c.id} — {c.numero_cnj or 'sem número'}{_descricao_salvo(c)} — "
                  f"{mod_pedido.NOMES_AREA.get(c.area, c.area)} — cálculo em {formatar_data(date.fromisoformat(c.data_calculo))} "
                  f"— total {formatar_brl(para_decimal(c.total))} — salvo em {_data_hora(c.atualizado_em)}")
    return OK


def _data_hora(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso


def _descricao_salvo(c) -> str:
    """Tipo e primeira parcela, para distinguir cálculos sem número de processo."""
    partes = [c.tipo] if c.tipo else []
    try:
        parcelas = json.loads(c.entrada_json).get("parcelas") or []
        if parcelas:
            primeira = parcelas[0]
            texto = f"{primeira.get('descricao', '')} {formatar_brl(para_decimal(str(primeira.get('valor_original'))))}"
            partes.append(texto + (f" + {len(parcelas) - 1} parcela(s)" if len(parcelas) > 1 else ""))
    except (ValueError, TypeError, AttributeError, ArithmeticError):
        pass
    return (" — " + " — ".join(partes)) if partes else ""


def cmd_atualizar_salvo(args) -> int:
    conn = persistencia.conectar()
    try:
        salvo = persistencia.abrir(conn, args.id)
    finally:
        conn.close()
    if salvo is None:
        _imprimir(f"Cálculo nº {args.id} não encontrado (veja o comando 'salvos').")
        return ERRO_PEDIDO
    try:
        nova = formulario.ler_data(args.data, "data do cálculo") if args.data else date.today()
    except ValueError as erro:
        _imprimir(str(erro))
        return ERRO_PEDIDO
    atualizar_indices()
    _imprimir("")
    pedido = mod_pedido.atualizar_ate(mod_pedido.pedido_de_entrada_salva(salvo.entrada_json), nova)
    if args.permitir_projecao:
        from dataclasses import replace

        pedido.params = replace(pedido.params, permitir_projecao=True)
    if args.conferir:  # mostra tudo antes, como no cálculo novo; o cálculo só roda sem --conferir
        conn = conectar()
        try:
            texto, problemas = mod_pedido.texto_conferencia(pedido, RepositorioSQLite(conn=conn), "atualizar-salvo")
        finally:
            conn.close()
        _imprimir(f"Atualização do cálculo nº {salvo.id} (de {formatar_data(date.fromisoformat(salvo.data_calculo))} "
                  f"para {formatar_data(nova)}, mesmos critérios):")
        _imprimir(texto)
        return NAO_RODA if problemas else OK
    _imprimir(f"Atualizando o cálculo nº {salvo.id} de {formatar_data(date.fromisoformat(salvo.data_calculo))} "
              f"para {formatar_data(nova)} (mesmos critérios; o original é mantido).")
    return _executar(pedido, args.saida, True, args.json, "atualizar-salvo")


# ---------------------------------------------------------------------------
# Tabelas de correção (índices oficiais e tabelas de tribunal)
# ---------------------------------------------------------------------------


def cmd_tabelas(args) -> int:
    from calculo_judicial.indices.tabelas import tabelas_de_correcao

    conn = conectar()
    try:
        tabelas = tabelas_de_correcao(conn)
    finally:
        conn.close()
    _imprimir("Tabelas e índices de correção disponíveis (o código vai em \"indice_correcao_civel\" ou nos períodos):")
    for t in tabelas:
        meses = f"{t.primeiro:%m/%Y} a {t.ultimo:%m/%Y}" if t.primeiro else ""
        origem = "" if t.cadeia else f" — {t.origem}"  # tabela montada: o nome já diz como foi feita
        _imprimir(f"  {t.codigo} — {t.nome}{origem}" + (f" — {meses}" if meses else ""))
    _imprimir("Tabela de outro tribunal: comando 'importar' com o arquivo oficial (CSV ou planilha).")
    return OK


AVISO_CORTE_14905 = ("No cível com os cortes legais automáticos, a tabela vale até 29/08/2024; depois, IPCA + taxa legal "
                     "(Lei 14.905/2024). Se o título manda usar a tabela no período todo, informe os períodos do título.")


def cmd_importar(args) -> int:
    """Importa a tabela oficial de um tribunal. Sem --gravar, só ensaia (nada é gravado)."""
    from calculo_judicial.app import importacao
    from calculo_judicial.indices.tabelas import ensaiar_importacao

    caminho = Path(args.arquivo).expanduser()
    if not caminho.is_file():
        _imprimir(f"Arquivo não encontrado: {caminho}")
        return ERRO_PEDIDO
    try:
        codigo = importacao.validar_codigo(args.codigo)
    except importacao.ErroImportacaoInterface as erro:
        _imprimir(str(erro))
        return ERRO_PEDIDO
    conn = conectar()
    try:
        if not args.gravar:
            try:
                ensaio = ensaiar_importacao(conn, caminho, codigo, args.convencao, args.aceitar_fora_da_faixa, args.substituir)
            except Exception as erro:  # noqa: BLE001 - arquivo recusado: explica e não grava nada
                _imprimir(f"Importação recusada (nada foi gravado): {erro}")
                return NAO_RODA
            _imprimir(f"ENSAIO (nada foi gravado): {ensaio.resumo}")
            for aviso in ensaio.avisos:
                _imprimir(f"Atenção: {aviso}")
            _imprimir("Índice oficial que cada trecho do arquivo segue:")
            for trecho in ensaio.trechos[:10]:
                _imprimir(f"  {trecho}")
            if len(ensaio.trechos) > 10:
                _imprimir(f"  ... e mais {len(ensaio.trechos) - 10} trechos (as taxas variam sem seguir um índice só)")
            _imprimir("Últimos meses do arquivo comparados com os índices oficiais:")
            for linha in ensaio.comparacao:
                _imprimir(f"  {linha}")
            _imprimir(ensaio.sugestao)
            _imprimir(AVISO_CORTE_14905)
            _imprimir("Para gravar, repita o comando com --gravar (e --nome e --fonte).")
            return OK
        try:
            resultado, copia = importacao.importar_enviado(
                conn, caminho.name, caminho.read_bytes(), codigo, nome=args.nome, fonte=args.fonte,
                convencao_fator=args.convencao, substituir=args.substituir, aceitar_fora_da_faixa=args.aceitar_fora_da_faixa)
        except importacao.ErroImportacaoInterface as erro:
            _imprimir(f"Importação recusada (nada foi gravado): {erro}")
            return NAO_RODA
    finally:
        conn.close()
    _imprimir(f"Tabela gravada: {resultado.resumo()}. Cópia do arquivo em {copia}.")
    _imprimir(AVISO_CORTE_14905)
    _imprimir(f"Para usar: \"criterios\": {{\"indice_correcao_civel\": \"{codigo}\"}} (ou o código nos períodos do título).")
    return OK


# ---------------------------------------------------------------------------
# Configurações (cabeçalho do PDF/Excel)
# ---------------------------------------------------------------------------


def cmd_configurar(args) -> int:
    atual = configuracoes.carregar()
    mudou = False
    for campo in ("nome_escritorio", "oab", "endereco", "ressalva", "pasta_saida"):
        valor = getattr(args, campo)
        if valor is not None:
            setattr(atual, campo, str(_pasta(valor)) if campo == "pasta_saida" else valor)
            mudou = True
    if args.logo:
        try:
            atual.logo = str(configuracoes.salvar_logo(Path(args.logo).read_bytes()))
            mudou = True
        except (OSError, ValueError) as erro:
            _imprimir(f"Logo não gravado: {erro}")
            return ERRO_PEDIDO
    if mudou:
        configuracoes.salvar(atual)
        _imprimir("Configurações salvas.")
    _imprimir(f"Escritório: {atual.nome_escritorio or '—'}")
    _imprimir(f"OAB: {atual.oab or '—'}")
    _imprimir(f"Endereço: {atual.endereco or '—'}")
    _imprimir(f"Logo: {atual.logo or '—'}")
    _imprimir(f"Ressalva: {atual.ressalva}")
    _imprimir(f"Pasta dos relatórios: {atual.pasta_saida}")
    return OK


# ---------------------------------------------------------------------------
# Tela visual (Streamlit)
# ---------------------------------------------------------------------------

ARQ_PID = PASTA_DADOS / "interface.pid"


def _porta_respondendo(porta: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", porta)) == 0


def _porta_livre(inicial: int) -> int:
    porta = inicial
    while _porta_respondendo(porta) and porta < inicial + 50:
        porta += 1
    return porta


def _saude(porta: int) -> bool:
    """A porta é de uma calculadora (Streamlit) pronta para uso?"""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/_stcore/health", timeout=2) as resposta:
            return resposta.status == 200 and resposta.read().strip() == b"ok"
    except OSError:
        return False


def _linha_de_comando(pid: int) -> str:
    """Linha de comando do processo (para nunca encerrar outro programa com o mesmo número)."""
    try:
        # saída sempre em UTF-8: caminhos com acento ("Área de Trabalho") quebravam a leitura na página de código do Windows
        if os.name == "nt":
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
                               capture_output=True, encoding="utf-8", errors="replace", timeout=20)
        else:
            r = subprocess.run(["ps", "-o", "command=", "-p", str(int(pid))], capture_output=True, encoding="utf-8",
                               errors="replace", timeout=10)
        return (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


def _app() -> Path:
    return Path(__file__).resolve().parent / "app" / "streamlit_app.py"


def _e_a_calculadora(dados: dict) -> bool:
    linha = _linha_de_comando(dados.get("pid", -1))
    return "streamlit" in linha and "streamlit_app.py" in linha


def _ler_pid() -> dict | None:
    try:
        return json.loads(ARQ_PID.read_text(encoding="utf-8")) if ARQ_PID.exists() else None
    except (ValueError, OSError):
        return None


def _encerrar(dados: dict) -> bool:
    if not _e_a_calculadora(dados):
        return False
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(int(dados["pid"])), "/F"], capture_output=True)
        else:
            os.kill(int(dados["pid"]), 15)
        return True
    except (OSError, ValueError, KeyError):
        return False


def cmd_abrir(args) -> int:
    import webbrowser

    dados = _ler_pid()
    if dados:
        mesma_versao = dados.get("versao") == VERSAO and dados.get("app") == str(_app())
        if _e_a_calculadora(dados):
            if mesma_versao:
                for _ in range(120):  # já aberta ou ainda carregando: espera por ela em vez de abrir outra
                    if _saude(dados["porta"]):
                        url = f"http://localhost:{dados['porta']}"
                        webbrowser.open(url)
                        _imprimir(f"A calculadora já está aberta: {url}")
                        return OK
                    time.sleep(0.5)
            _encerrar(dados)  # versão antiga do plugin (ou travada): fecha e abre a atual
            _imprimir("Fechei a calculadora visual anterior para abrir a versão atual.")
        ARQ_PID.unlink(missing_ok=True)
    porta = _porta_livre(args.porta)
    log = PASTA_DADOS / "log" / "interface.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    comando = [sys.executable, "-m", "streamlit", "run", str(_app()), "--server.port", str(porta), "--server.headless", "true",
               "--browser.gatherUsageStats", "false", "--server.address", "localhost"]
    opcoes = {}
    if os.name == "nt":
        opcoes["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        opcoes["start_new_session"] = True
    with open(log, "a", encoding="utf-8") as saida:
        processo = subprocess.Popen(comando, stdout=saida, stderr=saida, stdin=subprocess.DEVNULL, close_fds=True, **opcoes)
    ARQ_PID.write_text(json.dumps({"pid": processo.pid, "porta": porta, "versao": VERSAO, "app": str(_app())}),
                       encoding="utf-8")
    for _ in range(120):
        if _saude(porta):
            break
        if processo.poll() is not None:
            ARQ_PID.unlink(missing_ok=True)
            _imprimir(f"A calculadora não abriu (detalhes em {log}).")
            return FALHA
        time.sleep(0.5)
    else:
        _imprimir(f"A calculadora ainda está carregando em http://localhost:{porta} — aguarde um pouco e atualize a página.")
        return OK
    url = f"http://localhost:{porta}"
    webbrowser.open(url)
    _imprimir(f"Calculadora aberta no navegador: {url}")
    _imprimir("Ela fica aberta em segundo plano (e atualiza os índices sozinha). Para fechar: comando 'fechar'.")
    return OK


def cmd_fechar(args) -> int:
    dados = _ler_pid()
    if not dados:
        _imprimir("A calculadora visual não está aberta.")
        return OK
    encerrou = _encerrar(dados)
    ARQ_PID.unlink(missing_ok=True)
    _imprimir("Calculadora visual fechada." if encerrou else "A calculadora visual não estava aberta.")
    return OK


# ---------------------------------------------------------------------------


def cmd_sincronizar(args) -> int:
    """Uso interno do plugin: acrescenta as tabelas de tribunal que vieram na versão nova do pacote."""
    from calculo_judicial.indices.pacote import sincronizar_com_pacote

    conn = conectar()
    try:
        for mudanca in sincronizar_com_pacote(conn, args.pacote):
            _imprimir(f"Tabelas do pacote: {mudanca}")
    finally:
        conn.close()
    return OK


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="calculadora", description="Calculadora judicial (cálculos de liquidação e atualização)")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("status", help="versão, pasta de dados e situação dos índices").set_defaults(funcao=cmd_status)
    p = sub.add_parser("indices", help="confere o calendário e busca índices novos no Banco Central")
    p.add_argument("--forcar", action="store_true", help="consulta mesmo sem mês novo previsto")
    p.set_defaults(funcao=cmd_indices)
    p = sub.add_parser("modelo", help="pedido de exemplo (JSON)")
    p.add_argument("--area", default="civel", choices=sorted(mod_pedido.NOMES_AREA))
    p.set_defaults(funcao=cmd_modelo)
    p = sub.add_parser("conferir", help="mostra tudo o que será usado no cálculo, para confirmação")
    p.add_argument("pedido")
    p.add_argument("--sem-atualizar", action="store_true", help="não confere índices novos antes")
    p.set_defaults(funcao=cmd_conferir)
    p = sub.add_parser("calcular", help="calcula e grava PDF e Excel da memória")
    p.add_argument("pedido")
    p.add_argument("--saida")
    p.add_argument("--nao-salvar", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(funcao=cmd_calcular)
    p = sub.add_parser("salvos", help="lista os cálculos salvos")
    p.add_argument("--n", type=int, default=10)
    p.set_defaults(funcao=cmd_salvos)
    p = sub.add_parser("atualizar-salvo", help="refaz um cálculo salvo até outra data (padrão: hoje)")
    p.add_argument("id", type=int)
    p.add_argument("--data")
    p.add_argument("--saida")
    p.add_argument("--conferir", action="store_true", help="só mostra a conferência, sem calcular")
    p.add_argument("--permitir-projecao", action="store_true", help="mês ainda sem índice usa o último disponível (marcado)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(funcao=cmd_atualizar_salvo)
    sub.add_parser("tabelas", help="tabelas e índices de correção disponíveis").set_defaults(funcao=cmd_tabelas)
    p = sub.add_parser("importar", help="importa a tabela oficial de um tribunal (CSV/planilha); sem --gravar só ensaia")
    p.add_argument("arquivo")
    p.add_argument("--codigo", required=True, help="ex.: TJMG, TJRJ")
    p.add_argument("--nome", help="ex.: \"TJMG — tabela da Corregedoria (fatores)\"")
    p.add_argument("--fonte", help="ex.: \"TJMG, Corregedoria-Geral de Justiça\"")
    p.add_argument("--convencao", default="fator_fim_do_mes",
                   choices=["fator_fim_do_mes", "fator_inicio_do_mes", "coeficiente_multiplicador"],
                   help="como ler a coluna 'fator' (a coluna 'taxa_pct' não precisa)")
    p.add_argument("--substituir", action="store_true", help="troca a tabela que já existe com esse código")
    p.add_argument("--aceitar-fora-da-faixa", action="store_true", help="meses de inflação alta (ex.: 1994)")
    p.add_argument("--gravar", action="store_true", help="grava de verdade (sem isto, só ensaia)")
    p.set_defaults(funcao=cmd_importar)
    p = sub.add_parser("configurar", help="cabeçalho do escritório no PDF/Excel")
    p.add_argument("--nome", dest="nome_escritorio")
    p.add_argument("--oab")
    p.add_argument("--endereco")
    p.add_argument("--logo")
    p.add_argument("--ressalva")
    p.add_argument("--pasta-saida", dest="pasta_saida")
    p.set_defaults(funcao=cmd_configurar)
    p = sub.add_parser("abrir", help="abre a calculadora visual no navegador")
    p.add_argument("--porta", type=int, default=8765)
    p.set_defaults(funcao=cmd_abrir)
    sub.add_parser("fechar", help="fecha a calculadora visual").set_defaults(funcao=cmd_fechar)
    p = sub.add_parser("_sincronizar", help=argparse.SUPPRESS)
    p.add_argument("pacote")
    p.set_defaults(funcao=cmd_sincronizar)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = criar_parser().parse_args(argv)
    try:
        return args.funcao(args)
    except KeyboardInterrupt:
        return FALHA


if __name__ == "__main__":
    raise SystemExit(main())
