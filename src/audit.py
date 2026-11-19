# ==============================================================================
# AUDITORIA CONTÁBIL INDEPENDENTE DO SCEE E FATURAMENTO (Lei 14.300/2022)
# Versão: 0.1.1 (Modularizado para GitHub)
# ==============================================================================
import numpy as np
from .config import (
    snapshots, MASCARA, POSTOS_A, CFG_IND, CFG_COND, CFG_FAZ, ECON,
    CUSTO_DEGRADACAO_KWH, K_POSTO, FATOR_CONVERSAO_GRUPO_B,
    HOURS, DIAS, DIAS_PT, custo_semana
)
from .data_loader import carregar_perfis_semanais


def faturar_scee(imp_ind, exp_ind, imp_cond, inj_bruta, imp_faz, pct_fio_b, tem_fazenda):
    """
    Simulação contábil exata da concessionária (CEMIG-D) sob a Lei 14.300/2022:
      1. Prioridade Zero de autoconsumo da Fazenda Solar.
      2. Rateio do excedente líquido entre os participantes.
      3. Compensação intra-posto e cruzada da Indústria (Grupo A4).
      4. Compensação valorada por TE no Condomínio (Grupo B1).
    """
    E = lambda x, po: float(np.sum(x[MASCARA[po]]))
    s_ind, s_cond = CFG_IND["rateio_creditos"], CFG_COND["rateio_creditos"]

    # 1. Fazenda Solar (Prioridade Zero de autoconsumo)
    imp_f = {po: E(imp_faz, po) if tem_fazenda else 0.0 for po in POSTOS_A}
    inj_b = {po: E(inj_bruta, po) if tem_fazenda else 0.0 for po in POSTOS_A}
    comp_f = {po: min(imp_f[po], inj_b[po]) for po in POSTOS_A}
    fat_f = {po: imp_f[po] - comp_f[po] for po in POSTOS_A}
    inj_liq = {po: inj_b[po] - comp_f[po] for po in POSTOS_A}

    faz_scee = {
        "consumo_aux_kwh": imp_f,
        "inj_bruta_kwh": inj_b,
        "compensado_proprio_kwh": comp_f,
        "inj_liquida_rateio_kwh": inj_liq,
        "faturado_kwh": fat_f,
        "energia_rs": sum(CFG_FAZ["tarifa_energia"][po] * fat_f[po] for po in POSTOS_A),
        "fio_b_rs": sum(CFG_FAZ["fio_b_bruto"][po] * pct_fio_b * comp_f[po] for po in POSTOS_A),
    }

    # 2. Indústria Metalmecânica A4 Verde
    imp = {po: E(imp_ind, po) for po in POSTOS_A}
    cred = {po: s_ind * inj_liq[po] + E(exp_ind, po) for po in POSTOS_A}
    cs = {po: min(imp[po], cred[po]) for po in POSTOS_A}
    sobra = {po: cred[po] - cs[po] for po in POSTOS_A}
    falta = {po: imp[po] - cs[po] for po in POSTOS_A}
    cx = {"P": 0.0, "FP": 0.0}

    for de, para in (("FP", "P"), ("P", "FP")):
        if sobra[de] > 1e-9 and falta[para] > 1e-9:
            usar = min(sobra[de], falta[para] / K_POSTO[(de, para)])
            cx[de] = usar
            sobra[de] -= usar
            falta[para] -= usar * K_POSTO[(de, para)]

    fat = falta
    comp = {po: imp[po] - fat[po] for po in POSTOS_A}
    ind = {
        "consumo_kwh": imp, "creditos_kwh": cred, "comp_mesmo_posto_kwh": cs, "cruzado_kwh": cx,
        "compensado_kwh": comp, "faturado_kwh": fat, "sobra_kwh": sobra,
        "energia_rs": sum(CFG_IND["tarifa_energia"][po] * fat[po] for po in POSTOS_A),
        "fio_b_rs": sum(CFG_IND["fio_b_bruto"][po] * pct_fio_b * comp[po] for po in POSTOS_A),
        "sem_gd_rs": sum(CFG_IND["tarifa_energia"][po] * imp[po] for po in POSTOS_A),
    }

    # 3. Condomínio Residencial B1
    imp_c = float(imp_cond.sum())
    cred_c = s_cond * sum(FATOR_CONVERSAO_GRUPO_B[po] * inj_liq[po] for po in POSTOS_A)
    cs_c = min(imp_c, cred_c)
    cond = {
        "consumo_kwh": imp_c, "creditos_kwh": cred_c, "compensado_kwh": cs_c,
        "faturado_kwh": imp_c - cs_c, "sobra_kwh": cred_c - cs_c,
        "energia_rs": CFG_COND["tarifa_energia"]["U"] * (imp_c - cs_c),
        "fio_b_rs": CFG_COND["fio_b_bruto"]["U"] * pct_fio_b * cs_c,
        "sem_gd_rs": CFG_COND["tarifa_energia"]["U"] * imp_c,
    }

    return ind, cond, faz_scee


def auditar_resultado(n, cen, pct_fio_b, usar_dados_reais=None, caminho_dados_reais=None):
    """
    Audita independentemente as faturas e valida o valor da função objetivo do PyPSA.
    """
    lt = n.links_t.p0
    zeros = np.zeros(HOURS)
    imp_ind = lt["Import_Ind"].values
    exp_ind = lt["Export_Ind"].values
    imp_cond = lt["Import_Cond"].values
    inj = lt["Injecao_Faz"].values if cen["fazenda"] else zeros
    imp_faz = lt["Import_Faz"].values if cen["fazenda"] else zeros
    s_ind, s_cond = CFG_IND["rateio_creditos"], CFG_COND["rateio_creditos"]

    ind, cond, faz_scee = faturar_scee(imp_ind, exp_ind, imp_cond, inj, imp_faz, pct_fio_b, cen["fazenda"])

    dem_kw = float(n.links.p_nom_opt["Import_Ind"])
    dem_rs = dem_kw * custo_semana(CFG_IND["tarifa_demanda_rs_kw_mes"])
    musdg_kw = float(n.links.p_nom_opt["Injecao_Faz"]) if cen["fazenda"] else 0.0
    tusdg_rs = musdg_kw * custo_semana(CFG_FAZ["tarifa_tusdg_rs_kw_mes"])
    cusd_faz_kw = float(n.links.p_nom_opt["Import_Faz"]) if cen["fazenda"] else 0.0
    cusd_faz_rs = cusd_faz_kw * custo_semana(CFG_FAZ["tarifa_cusd_consumo_rs_kw_mes"]) if cen["fazenda"] else 0.0

    # Custos de O&M parametrizados (convertidos para R$/semana)
    om_faz_rs = custo_semana(CFG_FAZ["om_mensal_rs"]) if cen["fazenda"] else 0.0
    om_bess_rs = custo_semana(ECON["om_bess_mensal_rs"]) if cen["bess"] else 0.0
    om_total_rs = om_faz_rs + om_bess_rs

    p_bess = n.storage_units_t.p.iloc[:, 0].values if cen["bess"] else zeros
    soc = n.storage_units_t.state_of_charge.iloc[:, 0].values if cen["bess"] else zeros
    deg_rs = float(np.clip(p_bess, 0, None).sum() * CUSTO_DEGRADACAO_KWH)

    # Geração solar e curtailment
    _, _, _, pv_pu, _ = carregar_perfis_semanais(usar_dados_reais, caminho_dados_reais)
    pv_faz_disp = CFG_FAZ["pv_kwp"] * pv_pu if cen["fazenda"] else zeros
    pv_faz_ger = n.generators_t.p["Solar_Fazenda"].values if cen["fazenda"] else zeros
    pv_ind_ger = n.generators_t.p["Solar_Industria"].values
    corte_faz_h = pv_faz_disp - pv_faz_ger
    corte_ind_h = CFG_IND["pv_local_kwp"] * pv_pu - pv_ind_ger
    corte_kwh = float(corte_faz_h.sum() + corte_ind_h.sum())

    fat_ind = ind["energia_rs"] + ind["fio_b_rs"] + dem_rs
    fat_cond = cond["energia_rs"] + cond["fio_b_rs"]
    fat_faz_cemig = faz_scee["energia_rs"] + faz_scee["fio_b_rs"] + tusdg_rs + cusd_faz_rs
    fatura_cemig_total = fat_ind + fat_cond + fat_faz_cemig
    custo_total_operacional = fatura_cemig_total + om_total_rs

    # Verificação cruzada com o solver PyPSA
    erro_auditoria = abs((fatura_cemig_total + deg_rs) - float(n.objective))
    assert erro_auditoria < 1e-2, (
        f"Divergência MILP vs auditoria ({cen['nome']}): R$ {erro_auditoria:.4f} "
        f"(PyPSA={float(n.objective):.2f}, Auditoria={fatura_cemig_total + deg_rs:.2f})"
    )

    # Alocação dos custos por participante
    om_bess_ind = om_bess_rs if cen["bess"] == "Industria" else (s_ind * om_bess_rs if cen["bess"] == "Fazenda" else 0.0)
    om_bess_cond = s_cond * om_bess_rs if cen["bess"] == "Fazenda" else 0.0
    custo_faz_total = fat_faz_cemig + om_faz_rs

    efetivo_ind = fat_ind + s_ind * custo_faz_total + om_bess_ind
    efetivo_cond = fat_cond + s_cond * custo_faz_total + om_bess_cond

    return {
        "fatura_total": fatura_cemig_total,
        "custo_total_operacional": custo_total_operacional,
        "dem_ind_kw": dem_kw,
        "dem_ind_rs": dem_rs,
        "cusd_faz_kw": cusd_faz_kw,
        "cusd_faz_rs": cusd_faz_rs,
        "musdg_kw": musdg_kw,
        "tusdg_rs": tusdg_rs,
        "om_faz_rs": om_faz_rs,
        "om_bess_rs": om_bess_rs,
        "bess_desc_kwh": float(np.clip(p_bess, 0, None).sum()),
        "deg_rs": deg_rs,
        "inj_bruta_kwh": float(inj.sum()),
        "corte_kwh": corte_kwh,
        "efetivo_ind": efetivo_ind,
        "efetivo_cond": efetivo_cond,
        "fat_ind": fat_ind,
        "fat_cond": fat_cond,
        "fat_faz_cemig": fat_faz_cemig,
        "ind": ind,
        "cond": cond,
        "fazenda": faz_scee,
        "series": {
            "imp_ind": imp_ind,
            "exp_ind": exp_ind,
            "imp_cond": imp_cond,
            "inj": inj,
            "imp_faz": imp_faz,
            "p_bess": p_bess,
            "soc": soc,
            "pv_ind_ger": pv_ind_ger,
            "corte_faz_h": corte_faz_h,
        }
    }
