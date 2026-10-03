"""Job mensal de atualização dos índices e alerta por e-mail.

SPEC, seção 5: executar ``--atualizar`` todo dia 15 às 08h; se qualquer índice
ficar 2 meses atrasado, enviar e-mail (SMTP configurável em ``.env``).

Uso:

    python -m calculo_judicial.indices.agendador --executar-agora
    python -m calculo_judicial.indices.agendador --iniciar        # processo contínuo (APScheduler)
    python -m calculo_judicial.indices.agendador --instrucoes-windows

No Windows, a alternativa ao processo contínuo é o Agendador de Tarefas
(``schtasks``) chamando ``--executar-agora`` todo dia 15 às 08h.
"""

from __future__ import annotations

import argparse
import logging
import os
import smtplib
import sys
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from calculo_judicial.indices import coletor_bcb
from calculo_judicial.indices.modelo import PASTA_DADOS, conectar

LIMITE_ATRASO_MESES = 2
DIA_EXECUCAO = 15
HORA_EXECUCAO = 8

log = logging.getLogger("calculo_judicial.agendador")


def carregar_env(caminho: Path | None = None) -> None:
    """Carrega ``.env`` da raiz do projeto, se existir."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv(caminho or PASTA_DADOS / ".env", override=False)


CHAVES_SMTP = ("SMTP_HOST", "SMTP_PORTA", "SMTP_USUARIO", "SMTP_SENHA", "SMTP_TLS", "SMTP_SSL", "ALERTA_REMETENTE",
               "ALERTA_DESTINATARIOS")


def _valores_smtp() -> dict[str, str]:
    """Variáveis SMTP atuais: o ``.env`` é relido a cada envio (o que foi salvo na tela Configurações vale na hora)."""
    valores = {k: os.environ.get(k, "") for k in CHAVES_SMTP}
    arquivo = PASTA_DADOS / ".env"
    if arquivo.exists():
        try:
            from dotenv import dotenv_values

            valores.update({k: (v or "") for k, v in dotenv_values(arquivo).items() if k in CHAVES_SMTP and v})
        except (ImportError, OSError):  # pragma: no cover
            pass
    return valores


def config_smtp() -> dict[str, str | int | bool | list[str]] | None:
    """Configuração SMTP (variáveis de ambiente e ``.env``); None se incompleta."""
    v = _valores_smtp()
    host = v["SMTP_HOST"].strip()
    destinatarios = [d.strip() for d in v["ALERTA_DESTINATARIOS"].split(",") if d.strip()]
    if not host or not destinatarios:
        return None
    porta = v["SMTP_PORTA"].strip()
    return {
        "host": host,
        "porta": int(porta) if porta.isdigit() else 587,
        "usuario": v["SMTP_USUARIO"],
        "senha": v["SMTP_SENHA"],
        "tls": (v["SMTP_TLS"].strip() or "1") not in ("0", "nao", "false"),
        # SSL implícito (porta 465): SMTP_SSL=1 ou porta 465
        "ssl": v["SMTP_SSL"].strip() in ("1", "sim", "true") or porta == "465",
        "remetente": v["ALERTA_REMETENTE"] or v["SMTP_USUARIO"],
        "destinatarios": destinatarios,
    }


def indices_atrasados(linhas_status: list[coletor_bcb.LinhaStatus], limite: int = LIMITE_ATRASO_MESES):
    return [l for l in linhas_status if l.meses_faltando >= limite]


def montar_email(atrasados: list[coletor_bcb.LinhaStatus], hoje: date, remetente: str, destinatarios: list[str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"[Calculadora judicial] {len(atrasados)} índice(s) com {LIMITE_ATRASO_MESES}+ meses de atraso"
    msg["From"] = remetente
    msg["To"] = ", ".join(destinatarios)
    linhas = [
        f"Verificação de {hoje:%d/%m/%Y}: os índices abaixo estão {LIMITE_ATRASO_MESES} ou mais meses atrasados.",
        "",
    ]
    for l in atrasados:
        ultimo = l.ultimo_mes.strftime("%m/%Y") if l.ultimo_mes else "nenhum"
        linhas.append(f"- {l.codigo} ({l.nome}): último mês gravado {ultimo}; faltam {l.meses_faltando} meses")
    linhas += ["", "A calculadora continua tentando sozinha. Tabelas de tribunal não vêm do Banco Central: chegam nas",
               "atualizações do plugin ou são importadas na tela Índices. Se o atraso continuar, avise quem dá suporte."]
    msg.set_content("\n".join(linhas))
    return msg


def enviar_email(msg: EmailMessage, config: dict) -> None:
    if config.get("ssl"):
        with smtplib.SMTP_SSL(config["host"], config["porta"], timeout=30) as smtp:
            if config["usuario"]:
                smtp.login(config["usuario"], config["senha"])
            smtp.send_message(msg)
        return
    with smtplib.SMTP(config["host"], config["porta"], timeout=30) as smtp:
        if config["tls"]:
            smtp.starttls()
        if config["usuario"]:
            smtp.login(config["usuario"], config["senha"])
        smtp.send_message(msg)


def executar_job(caminho_db: str | Path | None = None, hoje: date | None = None, enviar=enviar_email) -> dict:
    """Roda a atualização, verifica atrasos e envia alerta se preciso.

    Uma falha inesperada na atualização vira alerta em log/alertas.log e não impede
    a verificação de atraso nem o e-mail. Devolve um dicionário com o resultado.
    """
    carregar_env()
    hoje = hoje or date.today()
    conn = conectar(caminho_db)
    resultado_atualizacao: list = []
    falha = None
    try:
        try:
            resultado_atualizacao = coletor_bcb.atualizar(conn, hoje=hoje)
        except Exception as erro:  # noqa: BLE001 - o job mensal não pode morrer calado
            falha = f"{type(erro).__name__}: {erro}"
            coletor_bcb.registrar_alerta("agendador", f"falha na atualização: {falha}")
        status = coletor_bcb.status(conn, hoje=hoje)
    finally:
        conn.close()
    atrasados = indices_atrasados(status)
    enviado = False
    if atrasados:
        config = config_smtp()
        if config is None:
            coletor_bcb.registrar_alerta(
                "agendador", f"{len(atrasados)} índice(s) atrasados, mas SMTP não configurado no .env"
            )
        else:
            msg = montar_email(atrasados, hoje, config["remetente"], config["destinatarios"])
            try:
                enviar(msg, config)
                enviado = True
            except Exception as erro:  # noqa: BLE001 - qualquer falha de SMTP vira alerta
                coletor_bcb.registrar_alerta("agendador", f"falha ao enviar e-mail: {erro}")
    return {"atualizacao": resultado_atualizacao, "atrasados": atrasados, "email_enviado": enviado, "falha": falha}


def iniciar(caminho_db: str | Path | None = None) -> None:  # pragma: no cover - processo contínuo
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    agendador = BlockingScheduler(timezone="America/Sao_Paulo")
    agendador.add_job(
        executar_job,
        CronTrigger(day=DIA_EXECUCAO, hour=HORA_EXECUCAO, minute=0),
        kwargs={"caminho_db": caminho_db},
        id="atualizar_indices",
        misfire_grace_time=6 * 3600,
        coalesce=True,
    )
    print(f"Agendador iniciado: --atualizar todo dia {DIA_EXECUCAO} às {HORA_EXECUCAO:02d}h. Ctrl+C para sair.")
    agendador.start()


def instrucoes_windows() -> str:
    python = Path(sys.executable)
    return (
        "Para agendar no Agendador de Tarefas do Windows (todo dia 15 às 08h), rode uma vez:\n"
        f'schtasks /Create /TN "CalculadoraJudicial-Indices" /SC MONTHLY /D {DIA_EXECUCAO} /ST 08:00 '
        f'/TR "\\"{python}\\" -m calculo_judicial.indices.agendador --executar-agora" /F'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Job mensal de atualização de índices")
    parser.add_argument("--executar-agora", action="store_true")
    parser.add_argument("--iniciar", action="store_true")
    parser.add_argument("--instrucoes-windows", action="store_true")
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.instrucoes_windows:
        print(instrucoes_windows())
        return 0
    if args.iniciar:
        iniciar(args.db)
        return 0
    if args.executar_agora:
        r = executar_job(args.db)
        for linha in r["atualizacao"]:
            print(linha)
        if r["atrasados"]:
            print(f"Atrasados: {', '.join(l.codigo for l in r['atrasados'])}; e-mail enviado: {r['email_enviado']}")
        else:
            print("Nenhum índice com 2+ meses de atraso.")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
