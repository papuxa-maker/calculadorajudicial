"""Motor de cálculo (etapa 2): biblioteca pura, sem interface."""

from calculo_judicial.indices.modelo import IndiceAusente
from calculo_judicial.motor.calcular import ParametroInvalido, calcular
from calculo_judicial.motor.memoria import LinhaMemoria, Memoria, MemoriaParcela, Resumo
from calculo_judicial.motor.parcela import Abatimento, Parametros, Parcela, Periodo
from calculo_judicial.motor.regras.comum import ErroRegra

__all__ = [
    "Abatimento",
    "ErroRegra",
    "IndiceAusente",
    "LinhaMemoria",
    "Memoria",
    "MemoriaParcela",
    "ParametroInvalido",
    "Parametros",
    "Parcela",
    "Periodo",
    "Resumo",
    "calcular",
]
