"""Utilitários de Decimal usados em todo o projeto.

Regra do projeto: dinheiro e taxas sempre em ``decimal.Decimal`` com precisão 10;
nunca ``float``. Arredondar a 2 casas só na exibição.

Interpretação adotada para "precisão 10": fatores, taxas e valores intermediários
são quantizados em 10 casas decimais (``Decimal('1E-10')``). O contexto aritmético
usa 34 dígitos significativos para que valores grandes (ex.: R$ 123.456.789,12)
não percam centavos nas multiplicações — com ``prec=10`` puro, um valor de 11
dígitos seria arredondado antes de chegar aos centavos.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal, InvalidOperation, localcontext

CASAS_INTERNAS = Decimal("1E-10")
CENTAVO = Decimal("0.01")
ZERO = Decimal("0")
UM = Decimal("1")
CEM = Decimal("100")

# Contexto aritmético único do projeto.
CONTEXTO = Context(prec=34, rounding=ROUND_HALF_EVEN)


def para_decimal(valor: object) -> Decimal:
    """Converte ``valor`` em Decimal recusando ``float``.

    Aceita Decimal, int e str (com ponto ou vírgula decimal). ``float`` levanta
    ``TypeError`` porque a representação binária já perdeu exatidão.
    """
    if isinstance(valor, bool):
        raise TypeError("bool não é valor numérico")
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, float):
        raise TypeError(f"float não é permitido para dinheiro ou taxa: {valor!r}")
    if isinstance(valor, str):
        texto = valor.strip().replace(" ", "")
        if not texto:
            raise ValueError("texto vazio não é número")
        if "," in texto:
            if "." in texto and texto.rfind(".") > texto.rfind(","):
                # "1,234.56" (formato americano) seria lido como 1,23456 — recusar
                raise ValueError(f"número em formato ambíguo (ponto depois da vírgula): {valor!r}")
            # formato brasileiro: 1.234,56
            texto = texto.replace(".", "").replace(",", ".")
        try:
            return Decimal(texto)
        except InvalidOperation as erro:
            raise ValueError(f"não é número: {valor!r}") from erro
    raise TypeError(f"tipo não suportado: {type(valor).__name__}")


def q10(valor: Decimal) -> Decimal:
    """Quantiza em 10 casas decimais (precisão interna do projeto)."""
    with localcontext(CONTEXTO):
        return valor.quantize(CASAS_INTERNAS, rounding=ROUND_HALF_EVEN)


def arredondar_exibicao(valor: Decimal, casas: int = 2) -> Decimal:
    """Arredonda para exibição (padrão 2 casas, meio para cima — arredondamento comercial)."""
    with localcontext(CONTEXTO):
        return valor.quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP)


def fator_de_taxa(taxa_pct: Decimal) -> Decimal:
    """Converte taxa percentual em fator: 0,43% -> 1,0043."""
    with localcontext(CONTEXTO):
        return q10(UM + taxa_pct / CEM)


def taxa_de_fator(fator: Decimal) -> Decimal:
    """Converte fator em taxa percentual: 1,0043 -> 0,43%."""
    with localcontext(CONTEXTO):
        return q10((fator - UM) * CEM)


def formatar_brl(valor: Decimal) -> str:
    """Formata em reais: 1234.5 -> 'R$ 1.234,50'."""
    v = arredondar_exibicao(valor)
    sinal = "-" if v < 0 else ""
    inteiro, _, frac = f"{abs(v):.2f}".partition(".")
    grupos = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    return f"{sinal}R$ {'.'.join(grupos)},{frac}"


def formatar_decimal_br(valor: Decimal, casas: int) -> str:
    """Formata com vírgula decimal e ponto de milhar, com ``casas`` casas."""
    v = arredondar_exibicao(valor, casas)
    sinal = "-" if v < 0 else ""
    texto = f"{abs(v):.{casas}f}"
    inteiro, _, frac = texto.partition(".")
    grupos = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    return f"{sinal}{'.'.join(grupos)}{',' + frac if casas else ''}"
