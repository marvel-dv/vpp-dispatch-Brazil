# ==============================================================================
# ANÁLISE DE VIABILIDADE ECONÔMICO-FINANCEIRA (VPL, TIR, PAYBACK) - 10 E 25 ANOS
# Versão: 0.1.1 (Modularizado para GitHub)
# ==============================================================================
from .config import (
    ECON, ANO_BASE, PCT_FIO_B_POS_2028, TRANSICAO_FIO_B,
    CFG_FAZ, CAPACIDADE_BESS_KWH, CENARIOS
)

def pct_fio_b_ano(ano):
    return TRANSICAO_FIO_B.get(ano, PCT_FIO_B_POS_2028)

def vpl(fluxos, taxa):
    return sum(f / (1 + taxa) ** t for t, f in enumerate(fluxos))

def tir(fluxos):
    lo, hi = -0.99, 2.0
    if vpl(fluxos, lo) * vpl(fluxos, hi) > 0:
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if vpl(fluxos, lo) * vpl(fluxos, mid) <= 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)

def payback(fluxos, taxa=0.0):
    assert fluxos[0] < 0, f"Payback requer investimento inicial negativo no ano 0 (recebeu {fluxos[0]})."
    acum = 0.0
    for t, f in enumerate(fluxos):
        fd = f / (1 + taxa) ** t
        if t > 0 and acum < 0 <= acum + fd:
            return t - 1 + (-acum / fd)
        acum += fd
    return None

def calcular_investimentos(resolver_fn):
    """
    Calcula a viabilidade econômica plurianual considerando a curva progressiva do Fio B
    (Art. 27 da Lei 14.300/2022) e custos reais de O&M.
    """
    anos = list(range(ANO_BASE, ANO_BASE + ECON["vida_fazenda_anos"]))
    pcts = sorted({pct_fio_b_ano(a) for a in anos})

    # Pré-computa o faturamento semanal para cada percentual de Fio B relevante
    FAT_PCT = {}
    for pct in pcts:
        FAT_PCT[pct] = {c: resolver_fn(c, pct)["fatura_total"] for c in CENARIOS}

    def analisar(cen_base, cen_novo, capex, om_mensal, vida):
        om_anual = om_mensal * 12.0
        economias = [
            (FAT_PCT[pct_fio_b_ano(a)][cen_base] - FAT_PCT[pct_fio_b_ano(a)][cen_novo]) * ECON["semanas_ano"]
            for a in anos[:vida]
        ]
        fluxos = [-capex] + [e - om_anual for e in economias]
        r = ECON["wacc_real"]
        return {
            "capex": capex,
            "vpl": vpl(fluxos, r),
            "tir": tir(fluxos),
            "payback": payback(fluxos),
            "fluxos": fluxos,
            "vida": vida,
        }

    capex_faz = CFG_FAZ["pv_kwp"] * 1000 * ECON["capex_fazenda_rs_wp"]
    capex_bess = CAPACIDADE_BESS_KWH * ECON["capex_bess_rs_kwh"]

    return {
        "Fazenda (C1->C2)": analisar("C1", "C2", capex_faz, ECON["om_fazenda_mensal_rs"], ECON["vida_fazenda_anos"]),
        "VPP s/EMS (C1.5->C3)": analisar("C1.5", "C3", capex_faz, ECON["om_fazenda_mensal_rs"], ECON["vida_fazenda_anos"]),
        "BESS EMS (C1->C1.5)": analisar("C1", "C1.5", capex_bess, ECON["om_bess_mensal_rs"], ECON["vida_bess_anos"]),
        "BESS Ind. (C2->C3)": analisar("C2", "C3", capex_bess, ECON["om_bess_mensal_rs"], ECON["vida_bess_anos"]),
        "BESS Faz. (C2->C4)": analisar("C2", "C4", capex_bess, ECON["om_bess_mensal_rs"], ECON["vida_bess_anos"]),
    }
