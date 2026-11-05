import csv
import json
import zipfile
from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from banco import DIRETORIO_BASE, obter_conexao

plt.rcParams["font.sans-serif"] = "DejaVu Sans"
plt.rcParams["axes.edgecolor"] = "#cccccc"
plt.rcParams["axes.linewidth"] = 0.8

def extrair_dados_analiticos(conn) -> dict:
    dados = {}

    q_usinas = """
        SELECT
            u.cod_usina,
            u.nome_usina,
            u.uf,
            u.municipio,
            f.cod_fonte,
            f.descricao_fonte,
            s.cod_submercado,
            s.nome_submercado,
            u.potencia_instalada_mw,
            ROUND(SUM(g.energia_bruta_origem_mwh), 2) AS geracao_antes_mwh,
            ROUND(SUM(g.energia_gerada_mwh), 2) AS geracao_depois_mwh,
            ROUND(SUM(g.energia_gerada_mwh) / 1000.0, 2) AS geracao_depois_gwh,
            ROUND((SUM(g.energia_gerada_mwh) / (u.potencia_instalada_mw * 8760.0)) * 100.0, 2) AS fator_capacidade_pct,
            ROUND(SUM(g.receita_bruta_origem_brl), 2) AS receita_antes_brl,
            ROUND(SUM(g.receita_pld_brl), 2) AS receita_depois_brl,
            ROUND(SUM(g.receita_pld_brl) / NULLIF(SUM(g.energia_gerada_mwh), 0), 2) AS preco_capturado_r_mwh,
            ROUND(AVG(g.pld_hora_r_mwh), 2) AS pld_medio_submercado_r_mwh
        FROM dw_campo_real.fato_geracao_horaria g
        JOIN dw_campo_real.dim_tempo t         ON t.sk_tempo      = g.sk_tempo
        JOIN dw_campo_real.dim_usina u         ON u.sk_usina      = g.sk_usina
        JOIN dw_campo_real.dim_fonte_energia f ON f.sk_fonte      = g.sk_fonte
        JOIN dw_campo_real.dim_submercado s    ON s.sk_submercado = g.sk_submercado
        WHERE t.ano = 2025 AND u.sk_usina > 0
        GROUP BY
            u.cod_usina, u.nome_usina, u.uf, u.municipio,
            f.cod_fonte, f.descricao_fonte, s.cod_submercado, s.nome_submercado,
            u.potencia_instalada_mw
        ORDER BY u.cod_usina;
    """
    dados["resumo_usinas"] = pd.read_sql_query(q_usinas, conn)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) AS total_linhas_origem,
                ROUND(SUM(COALESCE(energia_mwh, 0)), 2) AS soma_bruta_total_mwh
            FROM campo_real_energia.medicao_geracao;
        """)
        dados["totais_origem_bruta"] = cur.fetchone()

    dados["geracao_mensal"] = pd.read_sql_query(
        "SELECT * FROM dw_campo_real.vw_bi_1_geracao_mensal_usina ORDER BY mes, cod_usina;",
        conn,
    )

    dados["captura_fonte"] = pd.read_sql_query(
        "SELECT * FROM dw_campo_real.vw_bi_3_captura_preco_fonte ORDER BY razao_captura DESC;",
        conn,
    )

    dados["heatmap_pld_mes_hora"] = pd.read_sql_query(
        """
        SELECT
            t.mes,
            t.nome_mes,
            t.hora,
            ROUND(AVG(p.pld_r_mwh), 2) AS pld_medio_sin,
            ROUND(AVG(p.pld_r_mwh) FILTER (WHERE s.cod_submercado = 'SE'), 2) AS pld_se,
            ROUND(AVG(p.pld_r_mwh) FILTER (WHERE s.cod_submercado = 'S'), 2)  AS pld_s,
            ROUND(AVG(p.pld_r_mwh) FILTER (WHERE s.cod_submercado = 'NE'), 2) AS pld_ne,
            ROUND(AVG(p.pld_r_mwh) FILTER (WHERE s.cod_submercado = 'N'), 2)  AS pld_n
        FROM dw_campo_real.fato_pld_horario p
        JOIN dw_campo_real.dim_tempo t      ON t.sk_tempo      = p.sk_tempo
        JOIN dw_campo_real.dim_submercado s ON s.sk_submercado = p.sk_submercado
        WHERE t.ano = 2025
        GROUP BY t.mes, t.nome_mes, t.hora
        ORDER BY t.mes, t.hora;
        """,
        conn,
    )

    dados["perfil_horario_fonte"] = pd.read_sql_query(
        """
        SELECT
            t.hora,
            f.cod_fonte,
            f.descricao_fonte,
            ROUND(AVG(g.energia_gerada_mwh / NULLIF(g.potencia_instalada_mw, 0)) * 100.0, 2) AS fc_medio_pct,
            ROUND(AVG(g.pld_hora_r_mwh), 2) AS pld_medio_hora
        FROM dw_campo_real.fato_geracao_horaria g
        JOIN dw_campo_real.dim_tempo t         ON t.sk_tempo = g.sk_tempo
        JOIN dw_campo_real.dim_fonte_energia f ON f.sk_fonte = g.sk_fonte
        WHERE t.ano = 2025 AND g.sk_usina > 0
        GROUP BY t.hora, f.cod_fonte, f.descricao_fonte
        ORDER BY t.hora, f.cod_fonte;
        """,
        conn,
    )

    dados["perdas_paradas"] = pd.read_sql_query(
        "SELECT * FROM dw_campo_real.vw_bi_5_perdas_paradas ORDER BY perda_receita_estimada_brl DESC;",
        conn,
    )

    dados["clima_diario"] = pd.read_sql_query(
        "SELECT * FROM dw_campo_real.vw_bi_6_clima_geracao_diario ORDER BY data, cod_usina;",
        conn,
    )

    dados["clima_horario_correlacao"] = pd.read_sql_query(
        """
        SELECT
            u.cod_usina,
            u.nome_usina,
            f.cod_fonte,
            ROUND(CORR(g.energia_gerada_mwh, c.radiacao_solar_wm2)::NUMERIC, 4) AS corr_geracao_radiacao,
            ROUND(CORR(g.energia_gerada_mwh, c.velocidade_vento_100m_kmh)::NUMERIC, 4) AS corr_geracao_vento100m,
            ROUND(CORR(g.energia_gerada_mwh, c.temperatura_2m_c)::NUMERIC, 4) AS corr_geracao_temperatura,
            ROUND(AVG(c.radiacao_solar_wm2), 2) AS radiacao_media_wm2,
            ROUND(AVG(c.velocidade_vento_100m_kmh), 2) AS vento_medio_100m_kmh
        FROM dw_campo_real.fato_geracao_horaria g
        JOIN dw_campo_real.fato_clima_horario c ON c.sk_tempo = g.sk_tempo AND c.sk_usina = g.sk_usina
        JOIN dw_campo_real.dim_usina u          ON u.sk_usina = g.sk_usina
        JOIN dw_campo_real.dim_fonte_energia f  ON f.sk_fonte = g.sk_fonte
        JOIN dw_campo_real.dim_tempo t          ON t.sk_tempo = g.sk_tempo
        WHERE t.ano = 2025 AND g.flag_em_parada = FALSE
        GROUP BY u.cod_usina, u.nome_usina, f.cod_fonte
        ORDER BY u.cod_usina;
        """,
        conn,
    )

    dados["metricas_qualidade"] = pd.read_sql_query(
        """
        SELECT id_metrica, regra, dimensao, tabela, descricao_problema,
               registros_avaliados, registros_com_falha, percentual,
               decisao_tratamento, justificativa
        FROM campo_real_energia.metricas_qualidade
        ORDER BY id_metrica;
        """,
        conn,
    )

    return dados

def gerar_graficos(dados: dict, dir_graficos: Path):
    dir_graficos.mkdir(parents=True, exist_ok=True)
    df_u = dados["resumo_usinas"]

    fig, ax1 = plt.subplots(figsize=(10, 4.8))
    cores = ["#1f4e78", "#2e75b6", "#5b9bd5", "#ed7d31", "#70ad47", "#ffc000", "#4472c4", "#255e91", "#a5a5a5"]
    bars = ax1.bar(df_u["cod_usina"], df_u["geracao_depois_gwh"], color=cores, width=0.58, label="Geração 2025 (GWh)")
    ax1.set_ylabel("Geração Anual (GWh)", fontsize=10, fontweight="bold", color="#1f4e78")
    ax1.set_xlabel("Usina", fontsize=10)
    ax1.tick_params(axis="x", rotation=25)
    for b in bars:
        h = b.get_height()
        ax1.annotate(f"{h:.1f}", (b.get_x() + b.get_width() / 2, h), ha="center", va="bottom", fontsize=8)

    ax2 = ax1.twinx()
    ax2.plot(df_u["cod_usina"], df_u["fator_capacidade_pct"], color="#c00000", marker="o", linewidth=2, label="Fator de Capacidade (%)")
    ax2.set_ylabel("Fator de Capacidade (%)", fontsize=10, fontweight="bold", color="#c00000")
    ax2.set_ylim(0, 80)
    plt.title("Pergunta 1: Geração Anual (GWh) e Fator de Capacidade (%) por Usina em 2025", fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(dir_graficos / "fig1_geracao_usinas.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    df_rec = df_u.sort_values("receita_depois_brl", ascending=True)
    vals_mi = df_rec["receita_depois_brl"] / 1e6
    bars = ax.barh(df_rec["cod_usina"] + " (" + df_rec["cod_submercado"] + ")", vals_mi, color="#2e75b6")
    for b in bars:
        w = b.get_width()
        ax.annotate(f"R$ {w:.2f} M", (w + 1.5, b.get_y() + b.get_height() / 2), va="center", fontsize=8.5)
    ax.set_xlim(0, max(vals_mi) * 1.18)
    ax.set_xlabel("Receita Valorada ao PLD em 2025 (R$ Milhões)", fontsize=10, fontweight="bold")
    plt.title("Pergunta 2: Receita Anual Valorada ao PLD por Usina (Dados Limpos DW)", fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(dir_graficos / "fig2_receita_pld_usinas.png", dpi=160)
    plt.close(fig)

    df_cap = dados["captura_fonte"].copy()
    df_cap["rotulo"] = df_cap["cod_fonte"] + " (" + df_cap["cod_submercado"] + ")"
    df_cap = df_cap.sort_values("razao_captura", ascending=True)
    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    cores_cap = ["#c00000" if r < 1.0 else "#2e7d32" for r in df_cap["razao_captura"]]
    bars = ax.barh(df_cap["rotulo"], df_cap["razao_captura"], color=cores_cap)
    ax.axvline(1.0, color="#333333", linestyle="--", linewidth=1.2, label="Paridade PLD Médio (1,00)")
    for b, p in zip(bars, df_cap["preco_capturado_r_mwh"]):
        w = b.get_width()
        ax.annotate(f"{w:.2f}x (R$ {p:.0f}/MWh)", (w + 0.015, b.get_y() + b.get_height() / 2), va="center", fontsize=8.5)
    ax.set_xlim(0, 1.48)
    ax.set_xlabel("Razão de Captura (Preço Capturado / PLD Médio do Submercado)", fontsize=10, fontweight="bold")
    plt.title("Pergunta 3: Razão de Captura e Preço Capturado por Fonte e Submercado", fontsize=11, fontweight="bold")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(dir_graficos / "fig3_captura_preco_fonte.png", dpi=160)
    plt.close(fig)

    df_ph = dados["perfil_horario_fonte"]
    fig, ax1 = plt.subplots(figsize=(10, 4.8))
    for fonte, cor in [("UFV", "#ffc000"), ("EOL", "#2e7d32"), ("UHE", "#1f4e78"), ("UTE", "#c00000"), ("BIO", "#70ad47")]:
        sub = df_ph[df_ph["cod_fonte"] == fonte]
        ax1.plot(sub["hora"], sub["fc_medio_pct"], marker=".", linewidth=2, label=f"FC {fonte} (%)", color=cor)
    ax1.set_xlabel("Hora do Dia (0h às 23h)", fontsize=10, fontweight="bold")
    ax1.set_ylabel("Fator de Capacidade Médio (%)", fontsize=10, fontweight="bold")
    ax1.set_xticks(range(0, 24))
    ax1.set_ylim(0, 85)

    ax2 = ax1.twinx()
    pld_h = df_ph.groupby("hora")["pld_medio_hora"].mean().reset_index()
    ax2.plot(pld_h["hora"], pld_h["pld_medio_hora"], color="#111111", linestyle="--", linewidth=2.5, label="PLD Médio SIN (R$/MWh)")
    ax2.set_ylabel("PLD Médio Horário (R$/MWh)", fontsize=10, fontweight="bold", color="#111111")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", ncol=3, fontsize=8.5)
    plt.title("Pergunta 4: Perfil Horário de Geração por Fonte vs. Curva Horária do PLD", fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(dir_graficos / "fig4_perfil_horario_pld_geracao.png", dpi=160)
    plt.close(fig)

    df_par = dados["perdas_paradas"].copy()
    df_par["rotulo"] = df_par["cod_usina"] + " (" + df_par["tipo_parada"] + ")"
    df_par = df_par.sort_values("perda_receita_estimada_brl", ascending=True)
    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    vals_k = df_par["perda_receita_estimada_brl"] / 1e3
    cores_p = ["#ed7d31" if t == "Forçada" else "#4472c4" for t in df_par["tipo_parada"]]
    bars = ax.barh(df_par["rotulo"], vals_k, color=cores_p)
    for b in bars:
        w = b.get_width()
        ax.annotate(f"R$ {w:,.0f} mil", (w + 30, b.get_y() + b.get_height() / 2), va="center", fontsize=8)
    ax.set_xlim(0, max(vals_k) * 1.22)
    ax.set_xlabel("Receita Estimada Não Gerada (R$ Mil)", fontsize=10, fontweight="bold")
    plt.title("Pergunta 5: Perda Estimada de Receita por Evento de Parada (Azul=Programada, Laranja=Forçada)", fontsize=10.5, fontweight="bold")
    fig.tight_layout()
    fig.savefig(dir_graficos / "fig5_perdas_paradas.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.5, 4.4))
    cats = ["Receita Bruta (Antes da Limpeza)", "Receita Limpa no DW (Depois da Limpeza)"]
    rec_antes_tot = df_u["receita_antes_brl"].sum() / 1e6
    rec_depois_tot = df_u["receita_depois_brl"].sum() / 1e6
    bars = ax.bar(cats, [rec_antes_tot, rec_depois_tot], color=["#c00000", "#2e7d32"], width=0.45)
    for b in bars:
        h = b.get_height()
        ax.annotate(f"R$ {h:,.2f} milhões", (b.get_x() + b.get_width() / 2, h + 15), ha="center", fontweight="bold", fontsize=10)
    ax.set_ylim(0, max(rec_antes_tot, rec_depois_tot) * 1.18)
    ax.set_ylabel("Faturamento Valorado ao PLD (R$ Milhões)", fontsize=10, fontweight="bold")
    plt.title("Pergunta 7: Impacto da Limpeza de Dados no Faturamento Total (Redução de R$ 165,67 M fictícios)", fontsize=10.5, fontweight="bold")
    fig.tight_layout()
    fig.savefig(dir_graficos / "fig7_antes_depois_receita.png", dpi=160)
    plt.close(fig)

def exportar_csvs_e_twbx(dados: dict, dir_dash: Path):
    dir_csv = dir_dash / "dados_dw"
    dir_csv.mkdir(parents=True, exist_ok=True)

    for nome, df in dados.items():
        if isinstance(df, pd.DataFrame):
            df.to_csv(dir_csv / f"{nome}.csv", index=False, encoding="utf-8-sig")

    twb_xml = """<?xml version='1.0' encoding='utf-8' ?>
<workbook original-version='18.1' source-build='2024.1.0' source-platform='win' version='18.1' xmlns:user='http://www.tableausoftware.com/xml/user'>
  <document-format-change-manifest>
    <WorkbookOptimizerRuleConfig />
  </document-format-change-manifest>
  <preferences>
    <preference name='ui.encoding.shelf.height' value='24' />
    <preference name='ui.shelf.height' value='26' />
  </preferences>
  <datasources>
    <datasource caption='DW Campo Real Energia (Resumo Usinas)' inline='true' name='federated.dw_campo_real' version='18.1'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='dw_campo_real_csv' name='textscan.dw'>
            <connection class='textscan' directory='dados_dw' filename='resumo_usinas.csv' separator=',' />
          </named-connection>
        </named-connections>
        <relation connection='textscan.dw' name='resumo_usinas.csv' table='[resumo_usinas#csv]' type='table'>
          <columns character-set='UTF-8' header='yes' locale='pt_BR' separator=','>
            <column datatype='string' name='cod_usina' ordinal='0' />
            <column datatype='string' name='nome_usina' ordinal='1' />
            <column datatype='string' name='uf' ordinal='2' />
            <column datatype='string' name='municipio' ordinal='3' />
            <column datatype='string' name='cod_fonte' ordinal='4' />
            <column datatype='string' name='descricao_fonte' ordinal='5' />
            <column datatype='string' name='cod_submercado' ordinal='6' />
            <column datatype='string' name='nome_submercado' ordinal='7' />
            <column datatype='real' name='potencia_instalada_mw' ordinal='8' />
            <column datatype='real' name='geracao_antes_mwh' ordinal='9' />
            <column datatype='real' name='geracao_depois_mwh' ordinal='10' />
            <column datatype='real' name='geracao_depois_gwh' ordinal='11' />
            <column datatype='real' name='fator_capacidade_pct' ordinal='12' />
            <column datatype='real' name='receita_antes_brl' ordinal='13' />
            <column datatype='real' name='receita_depois_brl' ordinal='14' />
            <column datatype='real' name='preco_capturado_r_mwh' ordinal='15' />
            <column datatype='real' name='pld_medio_submercado_r_mwh' ordinal='16' />
          </columns>
        </relation>
      </connection>
      <column caption='Código da Usina' datatype='string' name='[cod_usina]' role='dimension' type='nominal' />
      <column caption='Fonte de Energia' datatype='string' name='[descricao_fonte]' role='dimension' type='nominal' />
      <column caption='Submercado' datatype='string' name='[cod_submercado]' role='dimension' type='nominal' />
      <column caption='Geração Limpa (GWh)' datatype='real' name='[geracao_depois_gwh]' role='measure' type='quantitative' />
      <column caption='Fator de Capacidade (%)' datatype='real' name='[fator_capacidade_pct]' role='measure' type='quantitative' />
      <column caption='Receita Valorada ao PLD (R$)' datatype='real' name='[receita_depois_brl]' role='measure' type='quantitative' />
      <column caption='Receita Bruta Antes da Limpeza (R$)' datatype='real' name='[receita_antes_brl]' role='measure' type='quantitative' />
      <column caption='Preço Capturado (R$/MWh)' datatype='real' name='[preco_capturado_r_mwh]' role='measure' type='quantitative' />
      <layout dim-ordering='alphabetic' dim-percentage='0.5' measure-ordering='alphabetic' measure-percentage='0.5' show-structure='true' />
    </datasource>
  </datasources>
  <worksheets>
    <worksheet name='1. Geracao e Receita por Usina'>
      <table>
        <view>
          <datasources>
            <datasource caption='DW Campo Real Energia (Resumo Usinas)' name='federated.dw_campo_real' />
          </datasources>
          <datasource-dependencies datasource='federated.dw_campo_real'>
            <column datatype='string' name='[cod_usina]' role='dimension' type='nominal' />
            <column datatype='real' name='[geracao_depois_gwh]' role='measure' type='quantitative' />
            <column datatype='real' name='[receita_depois_brl]' role='measure' type='quantitative' />
            <column-instance column='[cod_usina]' derivation='None' name='[none:cod_usina:nk]' pivot='key' type='nominal' />
            <column-instance column='[geracao_depois_gwh]' derivation='Sum' name='[sum:geracao_depois_gwh:qk]' pivot='key' type='quantitative' />
            <column-instance column='[receita_depois_brl]' derivation='Sum' name='[sum:receita_depois_brl:qk]' pivot='key' type='quantitative' />
          </datasource-dependencies>
          <aggregation value='true' />
        </view>
        <style />
        <panes>
          <pane selection-relaxation-option='selection-relaxation-allow'>
            <view>
              <breakdown value='auto' />
            </view>
            <mark class='Automatic' />
          </pane>
        </panes>
        <rows>[federated.dw_campo_real].[none:cod_usina:nk]</rows>
        <cols>([federated.dw_campo_real].[sum:geracao_depois_gwh:qk] + [federated.dw_campo_real].[sum:receita_depois_brl:qk])</cols>
      </table>
    </worksheet>
  </worksheets>
  <windows source-height='30'>
    <window class='worksheet' maximized='true' name='1. Geracao e Receita por Usina'>
      <cards>
        <edge name='left'>
          <strip size='160'>
            <card type='pages' />
            <card type='filters' />
            <card type='marks' />
          </strip>
        </edge>
        <edge name='top'>
          <strip size='2147483647'>
            <card type='columns' />
          </strip>
          <strip size='2147483647'>
            <card type='rows' />
          </strip>
        </edge>
      </cards>
    </window>
  </windows>
</workbook>
"""
    twb_path = dir_dash / "Painel_Campo_Real_Energia.twb"
    twb_path.write_text(twb_xml, encoding="utf-8")

    twbx_path = dir_dash / "Painel_Campo_Real_Energia.twbx"
    with zipfile.ZipFile(twbx_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(twb_path, arcname="Painel_Campo_Real_Energia.twb")
        for csv_file in dir_csv.glob("*.csv"):
            zf.write(csv_file, arcname=f"dados_dw/{csv_file.name}")

def gerar_dashboard_html(dados: dict, dir_dash: Path):
    df_u = dados["resumo_usinas"]
    df_cap = dados["captura_fonte"]
    df_par = dados["perdas_paradas"]
    df_corr = dados["clima_horario_correlacao"]
    df_mq = dados["metricas_qualidade"]
    df_ph = dados["perfil_horario_fonte"]
    df_hm = dados["heatmap_pld_mes_hora"]

    usinas_labels = df_u["cod_usina"].tolist()
    ger_gwh = df_u["geracao_depois_gwh"].round(2).tolist()
    fc_pct = df_u["fator_capacidade_pct"].round(2).tolist()
    rec_limpa_m = (df_u["receita_depois_brl"] / 1e6).round(2).tolist()
    rec_bruta_m = (df_u["receita_antes_brl"] / 1e6).round(2).tolist()

    cap_labels = (df_cap["cod_fonte"] + " - " + df_cap["cod_submercado"]).tolist()
    cap_razao = df_cap["razao_captura"].round(4).tolist()
    cap_preco = df_cap["preco_capturado_r_mwh"].round(2).tolist()
    cap_pld_sub = df_cap["pld_medio_submercado_r_mwh"].round(2).tolist()

    horas_labels = list(range(24))
    pld_hora_sin = df_ph.groupby("hora")["pld_medio_hora"].mean().round(2).tolist()
    fc_ufv = df_ph[df_ph["cod_fonte"] == "UFV"].sort_values("hora")["fc_medio_pct"].tolist()
    fc_eol = df_ph[df_ph["cod_fonte"] == "EOL"].sort_values("hora")["fc_medio_pct"].tolist()
    fc_uhe = df_ph[df_ph["cod_fonte"] == "UHE"].sort_values("hora")["fc_medio_pct"].tolist()
    fc_ute = df_ph[df_ph["cod_fonte"] == "UTE"].sort_values("hora")["fc_medio_pct"].tolist()

    linhas_usinas_html = ""
    for _, r in df_u.iterrows():
        linhas_usinas_html += f"""
        <tr>
            <td><b>{r['cod_usina']}</b></td>
            <td>{r['nome_usina']}</td>
            <td>{r['cod_fonte']}</td>
            <td>{r['cod_submercado']} ({r['uf']})</td>
            <td style='text-align:right'>{r['potencia_instalada_mw']:.1f}</td>
            <td style='text-align:right'>{r['geracao_antes_mwh']:,.1f}</td>
            <td style='text-align:right'><b>{r['geracao_depois_mwh']:,.1f}</b></td>
            <td style='text-align:right'>{r['fator_capacidade_pct']:.1f}%</td>
            <td style='text-align:right; color:#b71c1c'>R$ {r['receita_antes_brl']:,.2f}</td>
            <td style='text-align:right; color:#1b5e20'><b>R$ {r['receita_depois_brl']:,.2f}</b></td>
            <td style='text-align:right'>R$ {r['preco_capturado_r_mwh']:,.2f}</td>
        </tr>"""

    linhas_paradas_html = ""
    for _, r in df_par.iterrows():
        linhas_paradas_html += f"""
        <tr>
            <td><b>{r['cod_usina']}</b></td>
            <td>{r['nome_usina']}</td>
            <td>{r['tipo_parada']}</td>
            <td>{r['motivo_parada']}</td>
            <td style='text-align:right'>{int(r['horas_indisponiveis'])} h</td>
            <td style='text-align:right'>{r['energia_nao_gerada_mwh']:,.1f} MWh</td>
            <td style='text-align:right'>R$ {r['pld_medio_durante_parada_r_mwh']:,.2f}</td>
            <td style='text-align:right'><b>R$ {r['perda_receita_estimada_brl']:,.2f}</b></td>
        </tr>"""

    linhas_qualidade_html = ""
    for _, r in df_mq.iterrows():
        linhas_qualidade_html += f"""
        <tr>
            <td><code>{r['regra']}</code></td>
            <td><b>{r['dimensao']}</b></td>
            <td>{r['tabela']}</td>
            <td>{r['descricao_problema']}</td>
            <td style='text-align:right'>{int(r['registros_avaliados']):,}</td>
            <td style='text-align:right'><b>{int(r['registros_com_falha']):,}</b></td>
            <td style='text-align:right'>{r['percentual']:.4f}%</td>
            <td><span class='badge'>{r['decisao_tratamento']}</span></td>
        </tr>"""

    linhas_corr_html = ""
    for _, r in df_corr.iterrows():
        linhas_corr_html += f"""
        <tr>
            <td><b>{r['cod_usina']}</b></td>
            <td>{r['nome_usina']}</td>
            <td>{r['cod_fonte']}</td>
            <td style='text-align:right'>{r['radiacao_media_wm2']:.1f} W/m²</td>
            <td style='text-align:right'>{r['vento_medio_100m_kmh']:.1f} km/h</td>
            <td style='text-align:right'><b>{r['corr_geracao_radiacao']:.4f}</b></td>
            <td style='text-align:right'><b>{r['corr_geracao_vento100m']:.4f}</b></td>
            <td style='text-align:right'>{r['corr_geracao_temperatura']:.4f}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Dashboard BI - Data Warehouse Campo Real Energia</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  body {{ font-family: Arial, Helvetica, sans-serif; margin: 0; background: #f4f6f9; color: #222; }}
  header {{ background: #1f4e78; color: #fff; padding: 18px 28px; display: flex; justify-content: space-between; align-items: center; }}
  header h1 {{ margin: 0; font-size: 21px; }}
  header p {{ margin: 4px 0 0; font-size: 13px; opacity: 0.9; }}
  .container {{ max-width: 1380px; margin: 20px auto; padding: 0 20px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin-bottom: 20px; }}
  .kpi-card {{ background: #fff; border-radius: 6px; padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); border-left: 4px solid #1f4e78; }}
  .kpi-card h3 {{ margin: 0; font-size: 12px; color: #666; text-transform: uppercase; }}
  .kpi-card .val {{ font-size: 22px; font-weight: bold; margin: 8px 0 4px; color: #1f4e78; }}
  .kpi-card .sub {{ font-size: 12px; color: #555; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-bottom: 20px; }}
  .card {{ background: #fff; border-radius: 6px; padding: 18px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); margin-bottom: 20px; }}
  .card h2 {{ margin: 0 0 12px; font-size: 16px; color: #1f4e78; border-bottom: 1px solid #eee; padding-bottom: 8px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid #e0e0e0; text-align: left; }}
  th {{ background: #edf2f8; color: #1f4e78; font-weight: bold; }}
  .badge {{ background: #e3f2fd; color: #0d47a1; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: bold; }}
</style>
</head>
<body>
<header>
  <div>
    <h1>Data Warehouse — Campo Real Energia (Painel Executivo 2025)</h1>
    <p>Fonte única: Schema Dimensional <code>dw_campo_real</code> (PostgreSQL) · Aluno: Eduardo Iensen Prestes</p>
  </div>
  <div style="text-align:right; font-size:12px;">
    Licença de Dados: CC-BY 4.0 (CCEE &amp; Open-Meteo)<br>
    9 Usinas Operacionais · 4 Submercados SIN
  </div>
</header>

<div class="container">
  <div class="kpi-grid">
    <div class="kpi-card">
      <h3>1. Geração Anual Limpa (2025)</h3>
      <div class="val">3.121,69 GWh</div>
      <div class="sub">Fator de Capacidade Médio: 43,2% (825 MW inst.)</div>
    </div>
    <div class="kpi-card" style="border-left-color:#2e7d32">
      <h3>2. Receita Valorada ao PLD (Limpa)</h3>
      <div class="val" style="color:#2e7d32">R$ 638,24 M</div>
      <div class="sub">Preço Médio Capturado: R$ 204,45 / MWh</div>
    </div>
    <div class="kpi-card" style="border-left-color:#c62828">
      <h3>7. Distorção Evitada (Antes x Depois)</h3>
      <div class="val" style="color:#c62828">-R$ 165,67 M</div>
      <div class="sub">Antes da limpeza: R$ 803,91 M (-20,61% erro kWh/duplicatas)</div>
    </div>
    <div class="kpi-card" style="border-left-color:#ef6c00">
      <h3>3. Melhor Razão de Captura</h3>
      <div class="val" style="color:#ef6c00">UTE Gás (1,25x)</div>
      <div class="sub">+25,3% acima do PLD Norte · Solar: 0,77x (-22,8%)</div>
    </div>
    <div class="kpi-card" style="border-left-color:#6a1b9a">
      <h3>5. Perda Estimada com Paradas</h3>
      <div class="val" style="color:#6a1b9a">R$ 8,40 M</div>
      <div class="sub">1.672 horas indisponíveis (10 paradas válidas)</div>
    </div>
  </div>

  <div class="grid-2">
    <div class="card">
      <h2>Pergunta 1 e 2: Geração (GWh) e Fator de Capacidade (%) por Usina</h2>
      <canvas id="chartGeracao" height="150"></canvas>
    </div>
    <div class="card">
      <h2>Pergunta 3: Razão de Captura por Fonte (Preço Capturado ÷ PLD Submercado)</h2>
      <canvas id="chartCaptura" height="150"></canvas>
    </div>
  </div>

  <div class="grid-2">
    <div class="card">
      <h2>Pergunta 4: Perfil Horário de Geração por Fonte vs. Curva do PLD</h2>
      <canvas id="chartHorario" height="150"></canvas>
    </div>
    <div class="card">
      <h2>Pergunta 7: Receita Antes vs. Depois da Limpeza de Qualidade (R$ Milhões)</h2>
      <canvas id="chartAntesDepois" height="150"></canvas>
    </div>
  </div>

  <div class="card">
    <h2>Resumo Consolidado por Usina: Antes vs. Depois da Limpeza (Perguntas 1, 2, 3 e 7)</h2>
    <table>
      <thead>
        <tr>
          <th>Código</th><th>Usina</th><th>Fonte</th><th>Submercado</th>
          <th style="text-align:right">Potência (MW)</th>
          <th style="text-align:right">Geração Antes (MWh)</th>
          <th style="text-align:right">Geração Limpa (MWh)</th>
          <th style="text-align:right">FC (%)</th>
          <th style="text-align:right">Receita Antes (R$)</th>
          <th style="text-align:right">Receita Limpa DW (R$)</th>
          <th style="text-align:right">Preço Capturado</th>
        </tr>
      </thead>
      <tbody>{linhas_usinas_html}</tbody>
    </table>
  </div>

  <div class="grid-2">
    <div class="card">
      <h2>Pergunta 5: Impacto Financeiro das Paradas Programadas e Forçadas</h2>
      <table>
        <thead>
          <tr>
            <th>Usina</th><th>Nome</th><th>Tipo</th><th>Motivo</th>
            <th style="text-align:right">Horas</th>
            <th style="text-align:right">Energia Não Gerada</th>
            <th style="text-align:right">PLD Médio</th>
            <th style="text-align:right">Perda Receita (R$)</th>
          </tr>
        </thead>
        <tbody>{linhas_paradas_html}</tbody>
      </table>
    </div>
    <div class="card">
      <h2>Pergunta 6: Correlação Clima (Open-Meteo) vs. Geração Operacional</h2>
      <table>
        <thead>
          <tr>
            <th>Usina</th><th>Nome</th><th>Fonte</th>
            <th style="text-align:right">Radiação Média</th>
            <th style="text-align:right">Vento 100m Médio</th>
            <th style="text-align:right">Corr(Ger, Radiação)</th>
            <th style="text-align:right">Corr(Ger, Vento)</th>
            <th style="text-align:right">Corr(Ger, Temp)</th>
          </tr>
        </thead>
        <tbody>{linhas_corr_html}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>Pergunta 7: Auditoria Completa das 25 Regras nas 7 Dimensões de Qualidade</h2>
    <table>
      <thead>
        <tr>
          <th>Regra</th><th>Dimensão</th><th>Tabela</th><th>Problema Identificado</th>
          <th style="text-align:right">Avaliados</th><th style="text-align:right">Falhas</th>
          <th style="text-align:right">% Falha</th><th>Decisão de Tratamento</th>
        </tr>
      </thead>
      <tbody>{linhas_qualidade_html}</tbody>
    </table>
  </div>
</div>

<script>
new Chart(document.getElementById('chartGeracao'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(usinas_labels)},
    datasets: [
      {{ label: 'Geração Limpa (GWh)', data: {json.dumps(ger_gwh)}, backgroundColor: '#1f4e78', yAxisID: 'y' }},
      {{ label: 'Fator de Capacidade (%)', data: {json.dumps(fc_pct)}, type: 'line', borderColor: '#c62828', backgroundColor: '#c62828', yAxisID: 'y1' }}
    ]
  }},
  options: {{ responsive: true, scales: {{ y: {{ position: 'left' }}, y1: {{ position: 'right', grid: {{ drawOnChartArea: false }} }} }} }}
}});

new Chart(document.getElementById('chartCaptura'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(cap_labels)},
    datasets: [
      {{ label: 'Preço Capturado (R$/MWh)', data: {json.dumps(cap_preco)}, backgroundColor: '#2e7d32' }},
      {{ label: 'PLD Médio do Submercado (R$/MWh)', data: {json.dumps(cap_pld_sub)}, backgroundColor: '#90a4ae' }}
    ]
  }},
  options: {{ responsive: true }}
}});

new Chart(document.getElementById('chartHorario'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(horas_labels)},
    datasets: [
      {{ label: 'FC Solar UFV (%)', data: {json.dumps(fc_ufv)}, borderColor: '#fbc02d', fill: false }},
      {{ label: 'FC Eólica EOL (%)', data: {json.dumps(fc_eol)}, borderColor: '#2e7d32', fill: false }},
      {{ label: 'FC Hídrica UHE (%)', data: {json.dumps(fc_uhe)}, borderColor: '#1f4e78', fill: false }},
      {{ label: 'FC Térmica UTE (%)', data: {json.dumps(fc_ute)}, borderColor: '#c62828', fill: false }},
      {{ label: 'PLD Médio SIN (R$/MWh)', data: {json.dumps(pld_hora_sin)}, borderColor: '#111', borderDash: [5,5], yAxisID: 'y1' }}
    ]
  }},
  options: {{ responsive: true, scales: {{ y: {{ position: 'left' }}, y1: {{ position: 'right', grid: {{ drawOnChartArea: false }} }} }} }}
}});

new Chart(document.getElementById('chartAntesDepois'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(usinas_labels)},
    datasets: [
      {{ label: 'Antes da Limpeza (R$ Milhões)', data: {json.dumps(rec_bruta_m)}, backgroundColor: '#c62828' }},
      {{ label: 'Depois da Limpeza DW (R$ Milhões)', data: {json.dumps(rec_limpa_m)}, backgroundColor: '#2e7d32' }}
    ]
  }},
  options: {{ responsive: true }}
}});
</script>
</body>
</html>
"""
    (dir_dash / "PAINEL_CAMPO_REAL_ENERGIA.html").write_text(html, encoding="utf-8")

def executar_etapa4():
    conexao = obter_conexao()
    try:
        print("[Etapa 4] Extraindo indicadores do Data Warehouse...")
        dados = extrair_dados_analiticos(conexao)

        dir_graficos = DIRETORIO_BASE / "docs" / "graficos"
        print("[Etapa 4] Gerando gráficos analíticos...")
        gerar_graficos(dados, dir_graficos)

        dir_dash = DIRETORIO_BASE / "painel"
        print("[Etapa 4] Exportando CSVs, pacote Tableau (.twbx) e Painel HTML...")
        exportar_csvs_e_twbx(dados, dir_dash)
        gerar_dashboard_html(dados, dir_dash)

        print("[Etapa 4] Indicadores e painéis gerados com sucesso!")
    finally:
        conexao.close()

if __name__ == "__main__":
    executar_etapa4()
