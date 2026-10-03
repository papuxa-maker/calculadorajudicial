"""Perfis prontos por área (SPEC, seção 7): escolher um perfil preenche os campos, que continuam editáveis."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Perfil:
    nome: str
    area: str
    descricao: str
    valores: dict = field(default_factory=dict)  # campos de Parametros
    datas_exigidas: tuple[str, ...] = ()
    tipo_processo: str = "liquidação"


PERFIS: dict[str, Perfil] = {
    "civel": Perfil(
        "Cível pós-Lei 14.905",
        "civel",
        "Até 29/08/2024: tabela do tribunal (TJGO) + juros de 1% a.m. desde a citação; desde 30/08/2024: IPCA + taxa legal.",
        {
            "aplicar_cortes_legais": True,
            "responsabilidade": "contratual",
            "indice_correcao_civel": "TJGO",
            "indice_correcao_pos_14905": "IPCA",
            "juros_civel_pre_14905": "um_por_cento",
            "capitalizacao_selic": "simples",
            "selic_como": "juros",
            "arredondamento": "exato",
        },
        ("data_citacao",),
        "cumprimento",
    ),
    "trabalhista": Perfil(
        "Trabalhista ADC 58",
        "trabalhista",
        "Vencimento → ajuizamento: IPCA-E + juros pela TR; ajuizamento → 29/08/2024: SELIC; desde 30/08/2024: IPCA + taxa legal.",
        {
            "aplicar_cortes_legais": True,
            "indice_correcao_pos_14905": "IPCA",
            "trabalhista_pre_judicial_ate_corte": True,
            "capitalizacao_selic": "simples",
            "selic_como": "juros",
            "arredondamento": "exato",
        },
        ("data_ajuizamento",),
        "liquidação",
    ),
    "tributario": Perfil(
        "Tributário federal SELIC",
        "tributario",
        "SELIC do mês seguinte ao pagamento indevido até o mês anterior ao cálculo + 1% no mês do cálculo (Lei 9.250/95, art. 39, §4º).",
        {"aplicar_cortes_legais": True, "capitalizacao_selic": "simples", "selic_como": "juros", "arredondamento": "exato"},
        (),
        "atualização simples",
    ),
    "fazenda": Perfil(
        "Fazenda Pública EC 113",
        "fazenda",
        "Até 08/12/2021: IPCA-E + juros da poupança desde a citação; desde 09/12/2021: SELIC (EC 113/2021).",
        {
            "aplicar_cortes_legais": True,
            "indice_correcao_fazenda": "IPCA_E",
            "fazenda_regime_pos_ec136": "selic",
            "capitalizacao_selic": "simples",
            "selic_como": "juros",
            "multa_523": False,
            "arredondamento": "exato",
        },
        ("data_citacao",),
        "cumprimento",
    ),
    "personalizado": Perfil(
        "Personalizado",
        "civel",
        "Critérios do título: edite os períodos de cada parcela (cortes legais automáticos desligados).",
        {"aplicar_cortes_legais": False, "arredondamento": "exato"},
        (),
        "liquidação",
    ),
}


def aplicar_perfil(chave: str) -> dict:
    """Valores de formulário do perfil (área + parâmetros)."""
    perfil = PERFIS[chave]
    return {"area": perfil.area, "tipo_processo": perfil.tipo_processo, **perfil.valores}
