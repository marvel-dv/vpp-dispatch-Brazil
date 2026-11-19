# ==============================================================================
# MODELAGEM MATEMÁTICA E DESPACHO ÓTIMO MILP (PyPSA / Linopy / HiGHS)
# Versão: 0.1.1 (Modularizado para GitHub)
# ==============================================================================
import logging
import warnings
import numpy as np
import pandas as pd
import pypsa
from linopy import Constraint
from .config import (
    snapshots, MASCARA, SNAPS, POSTOS_A, CFG_IND, CFG_COND, CFG_FAZ,
    CAPACIDADE_BESS_KWH, POTENCIA_BESS_KW, EFICIENCIA_CARGA, EFICIENCIA_DESCARGA,
    CUSTO_DEGRADACAO_KWH, SOC_RESERVA_KWH, K_POSTO, FATOR_CONVERSAO_GRUPO_B,
    PCT_FIO_B_ANO_BASE, LIMITE_PADRAO, HOURS, DIAS, custo_semana
)
from .data_loader import carregar_perfis_semanais

SOLVER_OPTS = dict(
    solver_name="highs",
    include_objective_constant=False,
    solver_options={"output_flag": False, "mip_rel_gap": 1e-7}
)
FECHAMENTO_DIARIO_PADRAO = False

# Configuração idêntica à versão original (vpp-semana-0.1.1.py):
# Silencia mensagens internas de INFO e avisos cosméticos de PyPSA e Linopy para manter o terminal limpo
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="pypsa")
warnings.filterwarnings("ignore", category=UserWarning, module="linopy")
warnings.filterwarnings("ignore", message=".*A value is trying to be set on a copy of a slice from a DataFrame.*")
warnings.filterwarnings("ignore", message=".*The specified objective does not contain an offset.*")
warnings.filterwarnings("ignore", message=".*conflicting across operands and silently dropped.*")
logging.getLogger().setLevel(logging.WARNING)
for _lg in ("pypsa", "linopy", "highspy"):
    logging.getLogger(_lg).setLevel(logging.ERROR)


def adicionar_scee(m, tem_fazenda, pct_fio_b, load_aux_fazenda, pv_pu):
    """
    Equacionamento MILP exato do Sistema de Compensação de Energia Elétrica (SCEE)
    conforme a Lei 14.300/2022 e a REN 1.000/2021 da ANEEL.
    """
    p = m["Link-p"]

    def soma(nome, posto):
        return p.sel(name=nome, snapshot=SNAPS[posto]).sum()

    imp = {po: soma("Import_Ind", po) for po in POSTOS_A}
    exp = {po: soma("Export_Ind", po) for po in POSTOS_A}

    custo = 0

    # Big-M ajustado rigorosamente (1.2x a maior geração/consumo do horizonte)
    M_BIG = 1.2 * max(
        float(CFG_IND["demanda_contratada_atual_kw"] * HOURS),
        float(CFG_FAZ["pv_kwp"] * pv_pu.sum()),
        float(sum(load_aux_fazenda))
    )

    # 1. Prioridade Zero na Fazenda Solar (autoconsumo da carga auxiliar antes do rateio remoto)
    inj_liq = {}
    if tem_fazenda:
        inj_bruta = {po: soma("Injecao_Faz", po) for po in POSTOS_A}
        imp_faz = {po: soma("Import_Faz", po) for po in POSTOS_A}

        for po in POSTOS_A:
            comp_f = m.add_variables(lower=0, name=f"scee_faz_comp_{po}")
            fat_f = m.add_variables(lower=0, name=f"scee_faz_fat_{po}")
            il_f = m.add_variables(lower=0, name=f"scee_faz_inj_liq_{po}")
            yf = m.add_variables(binary=True, name=f"scee_faz_y_{po}")

            m.add_constraints(comp_f + fat_f - imp_faz[po] == 0, name=f"scee_faz_bal_imp_{po}")
            m.add_constraints(comp_f + il_f - inj_bruta[po] == 0, name=f"scee_faz_bal_inj_{po}")
            m.add_constraints(il_f - M_BIG * yf <= 0, name=f"scee_faz_ord_a_{po}")
            m.add_constraints(fat_f + M_BIG * yf <= M_BIG, name=f"scee_faz_ord_b_{po}")
            inj_liq[po] = il_f

            fb_faz = CFG_FAZ["fio_b_bruto"][po] * pct_fio_b
            custo = custo + CFG_FAZ["tarifa_energia"][po] * fat_f + fb_faz * comp_f
    else:
        inj_liq = {po: 0.0 for po in POSTOS_A}

    s_ind = CFG_IND["rateio_creditos"] if tem_fazenda else 0.0
    s_cond = CFG_COND["rateio_creditos"] if tem_fazenda else 0.0

    # 2. Balanço SCEE da Indústria (A4 Verde)
    v = {}
    for po in POSTOS_A:
        for tag in ("cs", "cx", "sb", "fat"):
            v[f"{tag}_{po}"] = m.add_variables(lower=0, name=f"scee_ind_{tag}_{po}")
        v[f"y_{po}"] = m.add_variables(binary=True, name=f"scee_ind_y_{po}")

    for po, ou in (("P", "FP"), ("FP", "P")):
        cred = s_ind * inj_liq[po] + exp[po]
        m.add_constraints(v[f"cs_{po}"] + v[f"cx_{po}"] + v[f"sb_{po}"] - cred == 0,
                          name=f"scee_ind_balanco_credito_{po}")
        m.add_constraints(v[f"fat_{po}"] - imp[po] + v[f"cs_{po}"] + K_POSTO[(ou, po)] * v[f"cx_{ou}"] == 0,
                          name=f"scee_ind_faturado_{po}")
        m.add_constraints(v[f"cx_{po}"] - M_BIG * v[f"y_{po}"] <= 0, name=f"scee_ind_ordem_a_{po}")
        m.add_constraints(v[f"fat_{po}"] + M_BIG * v[f"y_{po}"] <= M_BIG, name=f"scee_ind_ordem_b_{po}")
        fb = CFG_IND["fio_b_bruto"][po] * pct_fio_b
        custo = custo + CFG_IND["tarifa_energia"][po] * v[f"fat_{po}"] \
            + fb * (v[f"cs_{po}"] + K_POSTO[(ou, po)] * v[f"cx_{ou}"])

    # 3. Balanço SCEE do Condomínio (B1 Monômio)
    imp_c = p.sel(name="Import_Cond").sum()
    cs_c = m.add_variables(lower=0, name="scee_cond_cs")
    sb_c = m.add_variables(lower=0, name="scee_cond_sb")
    fat_c = m.add_variables(lower=0, name="scee_cond_fat")
    cred_c = s_cond * sum(FATOR_CONVERSAO_GRUPO_B[po] * inj_liq[po] for po in POSTOS_A)
    m.add_constraints(cs_c + sb_c - cred_c == 0, name="scee_cond_balanco_credito")
    m.add_constraints(fat_c - imp_c + cs_c == 0, name="scee_cond_faturado")
    custo = custo + CFG_COND["tarifa_energia"]["U"] * fat_c \
        + CFG_COND["fio_b_bruto"]["U"] * pct_fio_b * cs_c

    m.objective = m.objective.expression + custo


def fabricar_restricoes(cen, pct_fio_b, limite, fechamento_diario, load_aux_fazenda, pv_pu):
    def extra(n, sns):
        m = n.model
        p_link = m["Link-p"]

        # Lastro físico de injeção da indústria
        m.add_constraints(
            p_link.sel(name="Export_Ind") - m["Generator-p"].sel(name="Solar_Industria") <= 0,
            name="exp_ind_lastro_renovavel"
        )

        if cen["fazenda"]:
            vals_aux = pd.Series(load_aux_fazenda, index=sns)
            m.add_constraints(p_link.sel(name="Import_Faz") <= vals_aux,
                              name="imp_faz_limite_carga_aux")
            if limite is not None:
                idx_fin = np.where(np.isfinite(limite))[0]
                if len(idx_fin) > 0:
                    snaps_lim = sns[idx_fin]
                    vals_lim = pd.Series(limite[idx_fin], index=snaps_lim)
                    m.add_constraints(p_link.sel(name="Injecao_Faz", snapshot=snaps_lim) <= vals_lim,
                                      name="inversao_fluxo_fazenda")

        if cen["bess"]:
            soc = m["StorageUnit-state_of_charge"]
            m.add_constraints(soc >= SOC_RESERVA_KWH, name="bess_soc_min")
            if fechamento_diario:
                for d in range(DIAS):
                    m.add_constraints(soc.sel(snapshot=sns[24 * d + 23]) == SOC_RESERVA_KWH,
                                      name=f"bess_soc_fim_dia_{d}")
            else:
                m.add_constraints(soc.sel(snapshot=sns[-1]) == SOC_RESERVA_KWH, name="bess_soc_final_semana")

        adicionar_scee(m, cen["fazenda"], pct_fio_b, load_aux_fazenda, pv_pu)
    return extra


def construir_e_otimizar(cen, pct_fio_b=PCT_FIO_B_ANO_BASE, limite=LIMITE_PADRAO,
                         fechamento_diario=FECHAMENTO_DIARIO_PADRAO,
                         usar_dados_reais=None, caminho_dados_reais=None):
    load_industria, load_condominio, _, pv_pu, load_aux_fazenda = carregar_perfis_semanais(
        usar_dados_reais=usar_dados_reais, caminho_csv=caminho_dados_reais
    )

    n = pypsa.Network()
    n.set_snapshots(snapshots)
    n.add("Carrier", "AC")

    n.add("Bus", "Rede_CEMIG")
    n.add("Generator", "Slack_CEMIG", bus="Rede_CEMIG", p_nom=1e4, p_min_pu=-1.0, marginal_cost=0.0)

    # Indústria (A4 Verde)
    n.add("Bus", "Bus_Industria")
    n.add("Load", "Carga_Industria", bus="Bus_Industria", p_set=load_industria)
    n.add("Generator", "Solar_Industria", bus="Bus_Industria", p_nom=CFG_IND["pv_local_kwp"],
          p_max_pu=pv_pu, marginal_cost=0.0)

    if CFG_IND["otimizar_demanda"]:
        d_min, d_max = CFG_IND["demanda_minima_kw"], np.inf
    else:
        d_min = d_max = CFG_IND["demanda_contratada_atual_kw"]

    n.add("Link", "Import_Ind", bus0="Rede_CEMIG", bus1="Bus_Industria", marginal_cost=0.0,
          p_min_pu=0.0, p_nom_extendable=True, p_nom_min=d_min, p_nom_max=d_max,
          capital_cost=custo_semana(CFG_IND["tarifa_demanda_rs_kw_mes"]))

    n.add("Link", "Export_Ind", bus0="Bus_Industria", bus1="Rede_CEMIG", p_nom=1e3, p_min_pu=0.0, marginal_cost=0.0)

    # Condomínio (B1 Monômio)
    n.add("Bus", "Bus_Condominio")
    n.add("Load", "Carga_Condominio", bus="Bus_Condominio", p_set=load_condominio)
    n.add("Link", "Import_Cond", bus0="Rede_CEMIG", bus1="Bus_Condominio", p_nom=1e3, p_min_pu=0.0, marginal_cost=0.0)

    # Fazenda Solar (A4 Compartilhada)
    if cen["fazenda"]:
        n.add("Bus", "Bus_Fazenda")
        n.add("Load", "Load_Aux_Fazenda", bus="Bus_Fazenda", p_set=load_aux_fazenda)
        n.add("Generator", "Solar_Fazenda", bus="Bus_Fazenda", p_nom=CFG_FAZ["pv_kwp"],
              p_max_pu=pv_pu, marginal_cost=0.0)
        n.add("Link", "Injecao_Faz", bus0="Bus_Fazenda", bus1="Rede_CEMIG", marginal_cost=0.0,
              p_min_pu=0.0, p_nom_extendable=True, p_nom_min=0.0, p_nom_max=CFG_FAZ["inversor_kw_ac"],
              capital_cost=custo_semana(CFG_FAZ["tarifa_tusdg_rs_kw_mes"]))
        n.add("Link", "Import_Faz", bus0="Rede_CEMIG", bus1="Bus_Fazenda", marginal_cost=0.0,
              p_min_pu=0.0, p_nom_extendable=True, p_nom_min=CFG_FAZ["cusd_minimo_kw"], p_nom_max=np.inf,
              capital_cost=custo_semana(CFG_FAZ["tarifa_cusd_consumo_rs_kw_mes"]))

    if cen["bess"]:
        n.add("StorageUnit", f"BESS_{cen['bess']}", bus=f"Bus_{cen['bess']}",
              p_nom=POTENCIA_BESS_KW, max_hours=CAPACIDADE_BESS_KWH / POTENCIA_BESS_KW,
              efficiency_store=EFICIENCIA_CARGA, efficiency_dispatch=EFICIENCIA_DESCARGA,
              cyclic_state_of_charge=False, state_of_charge_initial=SOC_RESERVA_KWH,
              marginal_cost=CUSTO_DEGRADACAO_KWH)

    extra_fn = fabricar_restricoes(cen, pct_fio_b, limite, fechamento_diario, load_aux_fazenda, pv_pu)
    status, condicao = n.optimize(extra_functionality=extra_fn, **SOLVER_OPTS)
    if condicao != "optimal":
        raise RuntimeError(f"Cenário {cen['nome']} não convergiu: {status}/{condicao}")

    # Validação do gerador slack: confirma que não há fluxo espúrio na rede CEMIG
    slack_p = n.generators_t.p["Slack_CEMIG"].values
    lt = n.links_t.p0
    imp_ind = lt["Import_Ind"].values
    exp_ind = lt["Export_Ind"].values
    imp_cond = lt["Import_Cond"].values
    inj_faz = lt["Injecao_Faz"].values if cen["fazenda"] else np.zeros(HOURS)
    imp_faz = lt["Import_Faz"].values if cen["fazenda"] else np.zeros(HOURS)
    balanco_rede = (imp_ind + imp_cond + imp_faz) - (exp_ind + inj_faz)
    erro_slack = float(np.max(np.abs(slack_p - balanco_rede)))
    assert erro_slack < 1e-3, f"Fluxo espúrio no Slack_CEMIG ({cen['nome']}): {erro_slack:.4f} kW"

    return n
