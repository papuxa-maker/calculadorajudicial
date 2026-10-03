"""Acessórios do cálculo: abatimentos (imputação), multa do art. 523, multa contratual e honorários.

Ordem de aplicação (CLAUDE.md): correção, juros sobre o corrigido, multa, honorários.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, localcontext

from calculo_judicial.decimais import CEM, CONTEXTO, ZERO, formatar_brl, q10
from calculo_judicial.motor.memoria import LinhaAbatimento
from calculo_judicial.motor.parcela import Abatimento

PERCENTUAL_MULTA_523 = Decimal("10")
PERCENTUAL_HONORARIOS_523 = Decimal("10")


def imputar(valor: Decimal, juros: Decimal, capital: Decimal, regra: str) -> tuple[Decimal, Decimal, Decimal]:
    """Divide um pagamento entre juros e capital; devolve (juros, capital, excedente).

    - ``juros_primeiro``: CC, art. 354 — "Havendo capital e juros, o pagamento
      imputar-se-á primeiro nos juros vencidos, e depois no capital, salvo
      estipulação em contrário, ou se o credor passar a quitação por conta do capital."
      O acréscimo da SELIC embutida conta como juros (Manual CJF: "juros SELIC").
    - ``capital_primeiro``: estipulação em contrário / quitação por conta do capital.
    - ``proporcional``: na proporção do saldo de juros e de capital.
    """
    with localcontext(CONTEXTO):
        juros = max(juros, ZERO)
        capital = max(capital, ZERO)
        if regra == "juros_primeiro":
            pj = min(valor, juros)
            pc = min(valor - pj, capital)
        elif regra == "capital_primeiro":
            pc = min(valor, capital)
            pj = min(valor - pc, juros)
        elif regra == "proporcional":
            total = juros + capital
            if total <= 0:
                pj = pc = ZERO
            else:
                pago = min(valor, total)
                pj = q10(pago * juros / total)
                pc = pago - pj
        else:
            raise ValueError(f"regra de imputação inválida: {regra}")
        return q10(pj), q10(pc), q10(valor - pj - pc)


def distribuir(valor: Decimal, pesos: list[Decimal]) -> list[Decimal]:
    """Reparte ``valor`` na proporção de ``pesos``; a sobra de arredondamento vai para o maior peso."""
    total = sum(pesos, ZERO)
    if valor == 0 or total <= 0:
        return [ZERO for _ in pesos]
    with localcontext(CONTEXTO):
        partes = [q10(valor * p / total) for p in pesos]
        sobra = valor - sum(partes, ZERO)
        if sobra:
            maior = max(range(len(pesos)), key=lambda i: pesos[i])
            partes[maior] += sobra
    return partes


def aplicar_abatimentos(simuladores, abatimentos: list[Abatimento], imputacao: str, data_calculo: date, eventos=None):
    """Aplica os abatimentos em ordem de data (SPEC, seção 6).

    Para cada abatimento: atualiza o saldo de todas as parcelas até a data, imputa
    conforme a regra (padrão: juros primeiro, CC art. 354), reparte juros e capital
    entre as parcelas na proporção dos respectivos saldos e continua com o saldo.
    ``eventos``: {data: função} chamada depois dos abatimentos daquela data (ex.:
    retrato do débito no decurso do art. 523). Devolve (linhas, avisos).
    """
    linhas: list[LinhaAbatimento] = []
    avisos: list[str] = []
    eventos = dict(eventos or {})
    datas = sorted({a.data for a in abatimentos} | set(eventos))
    for d in datas:
        if d > data_calculo:
            for a in abatimentos:
                if a.data == d:
                    avisos.append(f"abatimento de {d:%d/%m/%Y} ({a.descricao}) é posterior à data do cálculo e foi ignorado")
            continue
        for s in simuladores:
            s.avancar(d)
        for a in sorted((a for a in abatimentos if a.data == d), key=lambda x: x.descricao):
            ativos = [s for s in simuladores if s.ativa]
            juros_total = sum((s.J for s in ativos), ZERO)
            capital_total = sum((s.C for s in ativos), ZERO)
            saldo_antes = juros_total + capital_total
            pj, pc, excedente = imputar(a.valor, juros_total, capital_total, imputacao)
            partes_j = distribuir(pj, [s.J for s in ativos])
            partes_c = distribuir(pc, [s.C for s in ativos])
            for s, j, c in zip(ativos, partes_j, partes_c):
                s.aplicar_pagamento(d, j, c, a.descricao)
            if excedente > 0:
                avisos.append(
                    f"abatimento de {d:%d/%m/%Y} ({a.descricao}) excede o saldo devido em {formatar_brl(excedente)}; "
                    "o excedente não foi compensado com parcelas futuras"
                )
            linhas.append(
                LinhaAbatimento(
                    data=d,
                    descricao=a.descricao,
                    valor=a.valor,
                    saldo_antes=saldo_antes,
                    juros_imputados=pj,
                    capital_imputado=pc,
                    excedente=excedente,
                    saldo_apos=saldo_antes - pj - pc,
                    imputacao=imputacao,
                )
            )
        if d in eventos:
            eventos[d]()
    return linhas, avisos


def multa_contratual(base: Decimal, pct: Decimal | None) -> Decimal:
    """Multa contratual (cláusula penal) sobre o débito corrigido com juros (ordem: correção, juros, multa)."""
    if not pct:
        return ZERO
    with localcontext(CONTEXTO):
        return q10(base * pct / CEM)


def honorarios_percentuais(base: Decimal, pct: Decimal | None) -> Decimal:
    """Honorários de sucumbência fixados em percentual "sobre o total" (SPEC): principal corrigido + juros + multa contratual."""
    if not pct:
        return ZERO
    with localcontext(CONTEXTO):
        return q10(base * pct / CEM)


def multa_523_cabivel(area: str) -> tuple[bool, str]:
    """Se cabe a multa de 10% do art. 523, §1º, do CPC na área, com a razão.

    - Fazenda Pública devedora (área 'fazenda' e repetição de indébito em 'tributario'):
      não há multa — CPC, art. 534, §2º.
    - Processo do trabalho: o TST declarou a multa do art. 523, §1º, incompatível com o
      rito da CLT (art. 880) — IRR 1786-24.2015.5.04.0000 (Tema repetitivo 4).
    - Cível: cabe.
    """
    if area in ("fazenda", "tributario"):
        return False, "devedor Fazenda Pública: não há multa do art. 523 (CPC, art. 534, §2º)"
    if area == "trabalhista":
        return False, "processo do trabalho: multa do art. 523, §1º, incompatível com a CLT (TST, IRR 1786-24.2015.5.04.0000, Tema 4)"
    return True, "CPC, art. 523, §1º"


def honorarios_cumprimento_cabiveis(area: str, houve_impugnacao: bool) -> tuple[bool, str]:
    """Se cabem os honorários de 10% do art. 523, §1º, do CPC, com a razão.

    - Fazenda Pública devedora: sem impugnação não há honorários no cumprimento (STJ,
      Tema 1.190); com impugnação, os honorários seguem o art. 85, §§1º, 3º e 7º
      (percentual escalonado fixado pelo juiz), não os 10% do art. 523 — informe-os
      como honorários em quantia certa.
    - Processo do trabalho: rito próprio da CLT, sem o art. 523 (TST, Tema 4).
    - Cível: cabem.
    """
    if area in ("fazenda", "tributario"):
        if houve_impugnacao:
            return False, ("Fazenda Pública com impugnação: honorários do art. 85, §§1º, 3º e 7º, fixados pelo juiz — "
                           "não os 10% do art. 523; informe-os como honorários em quantia certa")
        return False, "Fazenda Pública sem impugnação: sem honorários no cumprimento (STJ, Tema 1.190)"
    if area == "trabalhista":
        return False, "processo do trabalho: honorários do art. 523, §1º, não se aplicam (rito da CLT; TST, Tema 4)"
    return True, "CPC, art. 523, §1º; base sem a multa (STJ, REsp 1.757.033)"


def multa_523(debito: Decimal) -> Decimal:
    """Multa de 10% do art. 523, §1º, do CPC sobre o débito não pago no prazo de 15 dias úteis.

    Com pagamento parcial no prazo, incide sobre o restante (§2º) — por isso a base é
    o débito já abatido dos pagamentos feitos até o decurso. Não se aplica à Fazenda
    Pública (art. 534, §2º).
    """
    with localcontext(CONTEXTO):
        return q10(debito * PERCENTUAL_MULTA_523 / CEM)


def honorarios_cumprimento(debito: Decimal) -> Decimal:
    """Honorários de 10% do art. 523, §1º, do CPC, sobre o débito SEM a multa de 10%.

    STJ, REsp 1.757.033/DF (3ª T., 09/10/2018): a base dos honorários do cumprimento
    é o valor da dívida (com custas), sem a multa; multa e honorários têm a mesma base.
    Contra a Fazenda, só há honorários do cumprimento se houver impugnação (STJ, Tema 1.190).
    """
    with localcontext(CONTEXTO):
        return q10(debito * PERCENTUAL_HONORARIOS_523 / CEM)
