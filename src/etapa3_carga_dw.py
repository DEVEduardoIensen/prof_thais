from datetime import datetime
from psycopg2.extras import execute_values

from banco import DIRETORIO_BASE, obter_conexao, registrar_log_carga

NOMES_MESES = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

NOMES_DIAS_SEMANA = {
    0: "Domingo", 1: "Segunda-feira", 2: "Terça-feira", 3: "Quarta-feira",
    4: "Quinta-feira", 5: "Sexta-feira", 6: "Sábado",
}

def executar_script_sql(conexao, caminho_relativo: str):
    caminho_sql = DIRETORIO_BASE / caminho_relativo
    texto_sql = caminho_sql.read_text(encoding="utf-8")
    with conexao.cursor() as cursor:
        cursor.execute(texto_sql)
    conexao.commit()

def carregar_dim_tempo(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT data, descricao, tipo FROM campo_real_energia.feriado_nacional;")
        feriados = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        cur.execute("""
            SELECT generate_series(
                TIMESTAMP '2025-01-01 00:00:00',
                TIMESTAMP '2026-12-31 23:00:00',
                INTERVAL '1 hour'
            ) AS dh;
        """)
        horas = [r[0] for r in cur.fetchall()]

    linhas = []
    for dh in horas:
        sk_tempo = int(dh.strftime("%Y%m%d%H"))
        dt = dh.date()
        ano = dh.year
        mes = dh.month
        dia = dh.day
        hora = dh.hour
        semestre = 1 if mes <= 6 else 2
        trimestre = (mes - 1) // 3 + 1
        doy = int(dh.strftime("%j"))
        dow = (dh.weekday() + 1) % 7
        fim_semana = dow in (0, 6)

        if dt in feriados:
            flag_feriado = True
            nome_feriado, tipo_feriado = feriados[dt]
        else:
            flag_feriado = False
            nome_feriado, tipo_feriado = "Dia normal", "Não aplicável"

        dia_util = (not fim_semana) and (tipo_feriado != "Feriado")

        md = (mes, dia)
        if md >= (12, 21) or md < (3, 20):
            estacao = "Verão"
        elif md >= (3, 20) and md < (6, 21):
            estacao = "Outono"
        elif md >= (6, 21) and md < (9, 23):
            estacao = "Inverno"
        else:
            estacao = "Primavera"

        periodo_hidro = "Período Úmido (Dez-Abr)" if mes in (12, 1, 2, 3, 4) else "Período Seco (Mai-Nov)"

        if dia_util:
            if 18 <= hora <= 20:
                patamar = "Pesada"
            elif 0 <= hora <= 6:
                patamar = "Leve"
            else:
                patamar = "Média"
        else:
            if 0 <= hora <= 6 or hora >= 22:
                patamar = "Leve"
            else:
                patamar = "Média"

        linhas.append((
            sk_tempo,
            dh,
            dt,
            ano,
            semestre,
            trimestre,
            mes,
            NOMES_MESES[mes],
            f"{ano:04d}-{mes:02d}",
            dia,
            doy,
            hora,
            dow,
            NOMES_DIAS_SEMANA[dow],
            fim_semana,
            flag_feriado,
            nome_feriado,
            tipo_feriado,
            dia_util,
            estacao,
            periodo_hidro,
            patamar,
        ))

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO dw_campo_real.dim_tempo (
                sk_tempo, data_hora, data, ano, semestre, trimestre, mes, nome_mes, ano_mes,
                dia, dia_do_ano, hora, dia_semana_num, nome_dia_semana, flag_fim_de_semana,
                flag_feriado, nome_feriado, tipo_feriado, flag_dia_util, estacao_ano,
                periodo_hidrologico, patamar_carga
            ) VALUES %s
            ON CONFLICT (sk_tempo) DO NOTHING
            """,
            linhas,
            page_size=2000,
        )
    conn.commit()
    return len(linhas)

def carregar_dimensoes_cadastro(conn):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO dw_campo_real.dim_submercado (cod_submercado, nome_submercado, regioes)
            SELECT TRIM(cod_submercado), TRIM(nome), TRIM(regioes)
            FROM campo_real_energia.submercado
            ORDER BY cod_submercado;
        """)

        cur.execute("""
            INSERT INTO dw_campo_real.dim_fonte_energia (
                id_fonte_natural, cod_fonte, descricao_fonte,
                flag_renovavel, flag_intermitente, classificacao_despacho
            )
            SELECT
                id_fonte,
                TRIM(cod_fonte),
                TRIM(descricao),
                renovavel,
                intermitente,
                CASE
                    WHEN intermitente THEN 'Intermitente (Não Despachável)'
                    WHEN renovavel    THEN 'Renovável Controlável / Base'
                    ELSE 'Térmica Despachável (Flexível)'
                END
            FROM campo_real_energia.fonte_energia
            ORDER BY id_fonte;
        """)

        cur.execute("""
            INSERT INTO dw_campo_real.dim_usina (
                id_usina_natural, cod_usina, nome_usina, cod_fonte, descricao_fonte,
                flag_renovavel, flag_intermitente, cod_submercado, nome_submercado,
                uf, municipio, latitude, longitude, potencia_instalada_mw,
                data_inicio_operacao, situacao, data_inicio_vigencia, data_fim_vigencia,
                flag_registro_atual, versao_registro
            )
            SELECT
                u.id_usina,
                TRIM(u.cod_usina),
                TRIM(u.nome),
                TRIM(f.cod_fonte),
                TRIM(f.descricao),
                f.renovavel,
                f.intermitente,
                TRIM(s.cod_submercado),
                TRIM(s.nome),
                CASE
                    WHEN u.cod_usina = 'CRE-BIO-005' AND u.uf = 'PE' THEN 'SP'
                    ELSE u.uf
                END AS uf_limpa,
                TRIM(u.municipio),
                CASE
                    WHEN u.latitude > 0 THEN -ABS(u.latitude)
                    ELSE u.latitude
                END AS lat_limpa,
                u.longitude,
                u.potencia_mw,
                CASE
                    WHEN u.data_inicio_operacao > DATE '2025-12-31' THEN DATE '2019-02-01'
                    ELSE u.data_inicio_operacao
                END AS dt_inicio_limpa,
                TRIM(u.situacao),
                CASE
                    WHEN u.data_inicio_operacao > DATE '2025-12-31' THEN DATE '2019-02-01'
                    ELSE u.data_inicio_operacao
                END AS data_inicio_vigencia,
                NULL::DATE AS data_fim_vigencia,
                TRUE AS flag_registro_atual,
                1 AS versao_registro
            FROM campo_real_energia.usina u
            JOIN campo_real_energia.fonte_energia f ON f.id_fonte = u.id_fonte
            JOIN campo_real_energia.submercado s    ON s.cod_submercado = u.cod_submercado
            WHERE u.cod_usina ~ '^CRE-[A-Z]{3}-[0-9]{3}$'
            ORDER BY u.id_usina;
        """)

        cur.execute("""
            INSERT INTO dw_campo_real.dim_tipo_parada (tipo_parada, motivo_parada)
            SELECT DISTINCT TRIM(tipo), TRIM(motivo)
            FROM campo_real_energia.parada_usina
            WHERE fim >= inicio
            ORDER BY 1, 2;
        """)
    conn.commit()

def carregar_fato_pld(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO dw_campo_real.fato_pld_horario (sk_tempo, sk_submercado, pld_r_mwh)
            SELECT
                COALESCE(t.sk_tempo, -1) AS sk_tempo,
                COALESCE(s.sk_submercado, -1) AS sk_submercado,
                p.pld_r_mwh
            FROM campo_real_energia.pld_horario_tratado p
            LEFT JOIN dw_campo_real.dim_tempo t      ON t.data_hora = p.data_hora
            LEFT JOIN dw_campo_real.dim_submercado s ON s.cod_submercado = TRIM(p.cod_submercado)
            WHERE p.flag_valido = TRUE
            ON CONFLICT (sk_tempo, sk_submercado) DO UPDATE
            SET pld_r_mwh = EXCLUDED.pld_r_mwh;
        """)
        linhas = cur.rowcount
    conn.commit()
    return linhas

def carregar_fato_geracao(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            WITH usinas_validas AS (
                SELECT
                    du.sk_usina,
                    du.id_usina_natural AS id_usina,
                    du.cod_usina,
                    du.potencia_instalada_mw,
                    df.sk_fonte,
                    df.cod_fonte,
                    ds.sk_submercado,
                    ds.cod_submercado
                FROM dw_campo_real.dim_usina du
                JOIN dw_campo_real.dim_fonte_energia df ON df.cod_fonte = du.cod_fonte
                JOIN dw_campo_real.dim_submercado ds    ON ds.cod_submercado = du.cod_submercado
                WHERE du.sk_usina > 0 AND du.flag_registro_atual = TRUE
            ),
            grade_2025 AS (
                SELECT
                    dt.sk_tempo,
                    dt.data_hora,
                    dt.mes,
                    dt.hora,
                    uv.sk_usina,
                    uv.id_usina,
                    uv.cod_usina,
                    uv.potencia_instalada_mw,
                    uv.sk_fonte,
                    uv.cod_fonte,
                    uv.sk_submercado,
                    uv.cod_submercado
                FROM dw_campo_real.dim_tempo dt
                CROSS JOIN usinas_validas uv
                WHERE dt.ano = 2025
            ),
            medicoes_dedup AS (
                SELECT *
                FROM (
                    SELECT
                        m.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY m.id_usina, m.data_hora
                            ORDER BY CASE WHEN m.origem = 'SCADA' THEN 1 ELSE 2 END, m.id_medicao ASC
                        ) AS rn
                    FROM campo_real_energia.medicao_geracao m
                    WHERE m.id_usina IS NOT NULL
                      AND EXTRACT(YEAR FROM m.data_hora) = 2025
                ) d
                WHERE d.rn = 1
            ),
            medicoes_brutas_agregadas AS (
                SELECT
                    id_usina,
                    data_hora,
                    SUM(COALESCE(energia_mwh, 0)) AS energia_bruta_soma_mwh
                FROM campo_real_energia.medicao_geracao
                WHERE id_usina IS NOT NULL
                  AND EXTRACT(YEAR FROM data_hora) = 2025
                GROUP BY id_usina, data_hora
            ),
            paradas_hora AS (
                SELECT DISTINCT ON (p.id_usina, gs.dh)
                    p.id_usina,
                    gs.dh AS data_hora,
                    tp.sk_tipo_parada
                FROM campo_real_energia.parada_usina p
                JOIN dw_campo_real.dim_tipo_parada tp
                  ON tp.tipo_parada = TRIM(p.tipo) AND tp.motivo_parada = TRIM(p.motivo)
                CROSS JOIN LATERAL generate_series(p.inicio, p.fim, INTERVAL '1 hour') AS gs(dh)
                WHERE p.fim >= p.inicio
            ),
            medias_usina_mes_hora AS (
                SELECT
                    id_usina,
                    EXTRACT(MONTH FROM data_hora)::INT AS mes,
                    EXTRACT(HOUR FROM data_hora)::INT AS hora,
                    ROUND(AVG(energia_mwh), 3) AS media_mwh
                FROM medicoes_dedup
                WHERE energia_mwh IS NOT NULL
                  AND energia_mwh >= 0
                  AND energia_mwh <= 200
                GROUP BY id_usina, EXTRACT(MONTH FROM data_hora)::INT, EXTRACT(HOUR FROM data_hora)::INT
            ),
            vizinhos_eol003 AS (
                SELECT
                    ROUND(AVG(m.energia_mwh), 3) AS media_vizinhos
                FROM medicoes_dedup m
                JOIN usinas_validas u ON u.id_usina = m.id_usina
                WHERE u.cod_usina = 'CRE-EOL-003'
                  AND m.data_hora IN (TIMESTAMP '2025-07-03 02:00:00', TIMESTAMP '2025-07-03 04:00:00')
            )
            INSERT INTO dw_campo_real.fato_geracao_horaria (
                sk_tempo, sk_usina, sk_fonte, sk_submercado, sk_tipo_parada,
                energia_gerada_mwh, energia_bruta_origem_mwh,
                disponibilidade_mw, potencia_instalada_mw,
                pld_hora_r_mwh, receita_pld_brl, receita_bruta_origem_brl,
                flag_em_parada, flag_registro_tratado, tipo_tratamento_qualidade,
                atraso_recebimento_horas
            )
            SELECT
                g.sk_tempo,
                g.sk_usina,
                g.sk_fonte,
                g.sk_submercado,
                COALESCE(ph.sk_tipo_parada, -1) AS sk_tipo_parada,
                CASE
                    WHEN md.id_medicao IS NULL THEN 0.000
                    WHEN ph.sk_tipo_parada IS NOT NULL AND md.energia_mwh > 0 THEN 0.000
                    WHEN md.energia_mwh IS NULL THEN COALESCE(mm.media_mwh, 0.000)
                    WHEN md.energia_mwh < 0 THEN 0.000
                    WHEN g.cod_fonte = 'UFV' AND (g.hora >= 20 OR g.hora <= 4) AND md.energia_mwh > 0 THEN 0.000
                    WHEN md.energia_mwh > g.potencia_instalada_mw * 100 THEN ROUND(md.energia_mwh / 1000.0, 3)
                    WHEN md.energia_mwh > g.potencia_instalada_mw AND g.cod_usina = 'CRE-UHE-001' THEN ROUND(md.energia_mwh / 10.0, 3)
                    WHEN md.energia_mwh > g.potencia_instalada_mw AND g.cod_usina = 'CRE-EOL-003' THEN (SELECT media_vizinhos FROM vizinhos_eol003)
                    ELSE md.energia_mwh
                END AS energia_gerada_mwh,
                COALESCE(mb.energia_bruta_soma_mwh, 0.000) AS energia_bruta_origem_mwh,
                CASE
                    WHEN ph.sk_tipo_parada IS NOT NULL THEN 0.00
                    WHEN md.disponibilidade_mw IS NULL THEN g.potencia_instalada_mw
                    ELSE md.disponibilidade_mw
                END AS disponibilidade_mw,
                g.potencia_instalada_mw,
                COALESCE(pld.pld_r_mwh, 0.00) AS pld_hora_r_mwh,
                ROUND(
                    (CASE
                        WHEN md.id_medicao IS NULL THEN 0.000
                        WHEN ph.sk_tipo_parada IS NOT NULL AND md.energia_mwh > 0 THEN 0.000
                        WHEN md.energia_mwh IS NULL THEN COALESCE(mm.media_mwh, 0.000)
                        WHEN md.energia_mwh < 0 THEN 0.000
                        WHEN g.cod_fonte = 'UFV' AND (g.hora >= 20 OR g.hora <= 4) AND md.energia_mwh > 0 THEN 0.000
                        WHEN md.energia_mwh > g.potencia_instalada_mw * 100 THEN ROUND(md.energia_mwh / 1000.0, 3)
                        WHEN md.energia_mwh > g.potencia_instalada_mw AND g.cod_usina = 'CRE-UHE-001' THEN ROUND(md.energia_mwh / 10.0, 3)
                        WHEN md.energia_mwh > g.potencia_instalada_mw AND g.cod_usina = 'CRE-EOL-003' THEN (SELECT media_vizinhos FROM vizinhos_eol003)
                        ELSE md.energia_mwh
                    END) * COALESCE(pld.pld_r_mwh, 0.00),
                    2
                ) AS receita_pld_brl,
                ROUND(COALESCE(mb.energia_bruta_soma_mwh, 0.000) * COALESCE(pld.pld_r_mwh, 0.00), 2) AS receita_bruta_origem_brl,
                (ph.sk_tipo_parada IS NOT NULL) AS flag_em_parada,
                CASE
                    WHEN md.id_medicao IS NULL THEN TRUE
                    WHEN ph.sk_tipo_parada IS NOT NULL AND md.energia_mwh > 0 THEN TRUE
                    WHEN md.energia_mwh IS NULL THEN TRUE
                    WHEN md.energia_mwh < 0 THEN TRUE
                    WHEN g.cod_fonte = 'UFV' AND (g.hora >= 20 OR g.hora <= 4) AND md.energia_mwh > 0 THEN TRUE
                    WHEN md.energia_mwh > g.potencia_instalada_mw THEN TRUE
                    WHEN mb.energia_bruta_soma_mwh <> md.energia_mwh THEN TRUE
                    ELSE FALSE
                END AS flag_registro_tratado,
                CASE
                    WHEN md.id_medicao IS NULL THEN 'LACUNA_TELEMETRIA_ZERO'
                    WHEN ph.sk_tipo_parada IS NOT NULL AND md.energia_mwh > 0 THEN 'CORRIGIDO_GERACAO_EM_PARADA'
                    WHEN md.energia_mwh IS NULL THEN 'IMPUTADO_FALHA_SCADA'
                    WHEN md.energia_mwh < 0 THEN 'CORRIGIDO_GERACAO_NEGATIVA'
                    WHEN g.cod_fonte = 'UFV' AND (g.hora >= 20 OR g.hora <= 4) AND md.energia_mwh > 0 THEN 'CORRIGIDO_SOLAR_NOTURNO'
                    WHEN md.energia_mwh > g.potencia_instalada_mw * 100 THEN 'CORRIGIDO_UNIDADE_KWH_MWH'
                    WHEN md.energia_mwh > g.potencia_instalada_mw AND g.cod_usina = 'CRE-UHE-001' THEN 'CORRIGIDO_VIRGULA_DESLOCADA'
                    WHEN md.energia_mwh > g.potencia_instalada_mw AND g.cod_usina = 'CRE-EOL-003' THEN 'CORRIGIDO_OUTLIER_INTERPOLADO'
                    WHEN mb.energia_bruta_soma_mwh <> md.energia_mwh THEN 'DEDUPLICADO_REPROCESSAMENTO_OU_MANUAL'
                    WHEN g.cod_usina = 'CRE-BIO-005' AND g.data_hora BETWEEN TIMESTAMP '2025-09-01 00:00:00' AND TIMESTAMP '2025-09-03 23:00:00' THEN 'SINALIZADO_MEDIDOR_TRAVADO'
                    WHEN md.recebido_em < g.data_hora THEN 'SINALIZADO_RELOGIO_INVERTIDO'
                    WHEN md.recebido_em > g.data_hora + INTERVAL '24 hours' THEN 'SINALIZADO_ATRASO_INTEGRACAO'
                    ELSE 'ORIGINAL_VALIDO'
                END AS tipo_tratamento_qualidade,
                CASE
                    WHEN md.recebido_em IS NOT NULL THEN
                        ROUND(EXTRACT(EPOCH FROM (md.recebido_em - g.data_hora)) / 3600.0, 2)
                    ELSE NULL
                END AS atraso_recebimento_horas
            FROM grade_2025 g
            LEFT JOIN medicoes_dedup md           ON md.id_usina = g.id_usina AND md.data_hora = g.data_hora
            LEFT JOIN medicoes_brutas_agregadas mb ON mb.id_usina = g.id_usina AND mb.data_hora = g.data_hora
            LEFT JOIN paradas_hora ph             ON ph.id_usina = g.id_usina AND ph.data_hora = g.data_hora
            LEFT JOIN medias_usina_mes_hora mm    ON mm.id_usina = g.id_usina AND mm.mes = g.mes AND mm.hora = g.hora
            LEFT JOIN campo_real_energia.pld_horario_tratado pld
                   ON pld.data_hora = g.data_hora AND pld.cod_submercado = g.cod_submercado;
        """)
        linhas = cur.rowcount
    conn.commit()
    return linhas

def carregar_fato_paradas(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            WITH fc_referencia AS (
                SELECT
                    g.sk_usina,
                    t.mes,
                    t.hora,
                    AVG(g.energia_gerada_mwh / NULLIF(g.potencia_instalada_mw, 0)) AS fc_medio_mes_hora
                FROM dw_campo_real.fato_geracao_horaria g
                JOIN dw_campo_real.dim_tempo t ON t.sk_tempo = g.sk_tempo
                WHERE g.flag_em_parada = FALSE
                GROUP BY g.sk_usina, t.mes, t.hora
            ),
            fc_sazonal_usina AS (
                SELECT
                    g.sk_usina,
                    CASE WHEN t.mes BETWEEN 4 AND 11 THEN 'SAFRA_SECO' ELSE 'ENTRESSAFRA_UMIDO' END AS periodo_sazonal,
                    t.hora,
                    AVG(g.energia_gerada_mwh / NULLIF(g.potencia_instalada_mw, 0)) AS fc_medio_sazonal_hora
                FROM dw_campo_real.fato_geracao_horaria g
                JOIN dw_campo_real.dim_tempo t ON t.sk_tempo = g.sk_tempo
                WHERE g.flag_em_parada = FALSE
                GROUP BY g.sk_usina, CASE WHEN t.mes BETWEEN 4 AND 11 THEN 'SAFRA_SECO' ELSE 'ENTRESSAFRA_UMIDO' END, t.hora
            )
            INSERT INTO dw_campo_real.fato_parada_usina (
                sk_tempo, sk_usina, sk_fonte, sk_submercado, sk_tipo_parada,
                id_parada_natural, horas_indisponiveis, potencia_indisponivel_mw,
                fator_capacidade_esperado, energia_nao_gerada_estimada_mwh,
                pld_hora_r_mwh, perda_receita_estimada_brl
            )
            SELECT
                dt.sk_tempo,
                du.sk_usina,
                df.sk_fonte,
                ds.sk_submercado,
                tp.sk_tipo_parada,
                p.id_parada,
                1.00 AS horas_indisponiveis,
                du.potencia_instalada_mw,
                ROUND(COALESCE(fcr.fc_medio_mes_hora, fcs.fc_medio_sazonal_hora, 0.50), 4) AS fc_esperado,
                ROUND(
                    du.potencia_instalada_mw * COALESCE(fcr.fc_medio_mes_hora, fcs.fc_medio_sazonal_hora, 0.50),
                    3
                ) AS energia_nao_gerada_mwh,
                COALESCE(pld.pld_r_mwh, 0.00) AS pld_hora_r_mwh,
                ROUND(
                    du.potencia_instalada_mw
                    * COALESCE(fcr.fc_medio_mes_hora, fcs.fc_medio_sazonal_hora, 0.50)
                    * COALESCE(pld.pld_r_mwh, 0.00),
                    2
                ) AS perda_receita_brl
            FROM campo_real_energia.parada_usina p
            JOIN dw_campo_real.dim_usina du
              ON du.id_usina_natural = p.id_usina AND du.flag_registro_atual = TRUE
            JOIN dw_campo_real.dim_fonte_energia df ON df.cod_fonte = du.cod_fonte
            JOIN dw_campo_real.dim_submercado ds    ON ds.cod_submercado = du.cod_submercado
            JOIN dw_campo_real.dim_tipo_parada tp
              ON tp.tipo_parada = TRIM(p.tipo) AND tp.motivo_parada = TRIM(p.motivo)
            CROSS JOIN LATERAL generate_series(p.inicio, p.fim, INTERVAL '1 hour') AS gs(dh)
            JOIN dw_campo_real.dim_tempo dt ON dt.data_hora = gs.dh
            LEFT JOIN fc_referencia fcr
              ON fcr.sk_usina = du.sk_usina AND fcr.mes = dt.mes AND fcr.hora = dt.hora
            LEFT JOIN fc_sazonal_usina fcs
              ON fcs.sk_usina = du.sk_usina
             AND fcs.periodo_sazonal = (CASE WHEN dt.mes BETWEEN 4 AND 11 THEN 'SAFRA_SECO' ELSE 'ENTRESSAFRA_UMIDO' END)
             AND fcs.hora = dt.hora
            LEFT JOIN campo_real_energia.pld_horario_tratado pld
              ON pld.data_hora = dt.data_hora AND pld.cod_submercado = du.cod_submercado
            WHERE p.fim >= p.inicio;
        """)
        linhas = cur.rowcount
    conn.commit()
    return linhas

def carregar_fato_clima(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO dw_campo_real.fato_clima_horario (
                sk_tempo, sk_usina, sk_submercado,
                radiacao_solar_wm2, velocidade_vento_10m_kmh,
                velocidade_vento_100m_kmh, temperatura_2m_c, precipitacao_mm
            )
            SELECT
                dt.sk_tempo,
                du.sk_usina,
                ds.sk_submercado,
                c.radiacao_wm2,
                c.vento_10m_kmh,
                c.vento_100m_kmh,
                c.temperatura_c,
                c.precipitacao_mm
            FROM campo_real_energia.clima_horario_tratado c
            JOIN dw_campo_real.dim_tempo dt      ON dt.data_hora = c.data_hora
            JOIN dw_campo_real.dim_usina du      ON du.cod_usina = c.cod_usina AND du.flag_registro_atual = TRUE
            JOIN dw_campo_real.dim_submercado ds ON ds.cod_submercado = du.cod_submercado;
        """)
        linhas = cur.rowcount
    conn.commit()
    return linhas

def executar_carga_dw():
    conexao = obter_conexao()
    inicio = datetime.now()
    try:
        print("[DW] Executando DDL do Star Schema (02_ddl_dw_star_schema.sql)...")
        executar_script_sql(conexao, "sql/02_ddl_dw_star_schema.sql")

        print("[DW] Populando dim_tempo...")
        n_tempo = carregar_dim_tempo(conexao)
        print(f"  -> dim_tempo: {n_tempo} horas carregadas (+ membro desconhecido -1).")

        print("[DW] Populando dimensões de cadastro (dim_submercado, dim_fonte_energia, dim_usina, dim_tipo_parada)...")
        carregar_dimensoes_cadastro(conexao)

        print("[DW] Populando fato_pld_horario...")
        n_pld = carregar_fato_pld(conexao)
        print(f"  -> fato_pld_horario: {n_pld} registros.")

        print("[DW] Populando fato_geracao_horaria (aplicando regras de limpeza no ETL)...")
        n_ger = carregar_fato_geracao(conexao)
        print(f"  -> fato_geracao_horaria: {n_ger} registros.")

        print("[DW] Populando fato_parada_usina...")
        n_par = carregar_fato_paradas(conexao)
        print(f"  -> fato_parada_usina: {n_par} horas em parada.")

        print("[DW] Populando fato_clima_horario...")
        n_cli = carregar_fato_clima(conexao)
        print(f"  -> fato_clima_horario: {n_cli} registros.")

        print("[DW] Criando views analíticas para BI (03_views_analiticas_bi.sql)...")
        executar_script_sql(conexao, "sql/03_views_analiticas_bi.sql")

        total_gravadas = n_tempo + n_pld + n_ger + n_par + n_cli
        registrar_log_carga(
            conexao=conexao,
            tabela="dw_campo_real (Star Schema)",
            arquivo="etapa3_carga_dw.py",
            linhas_lidas=total_gravadas,
            linhas_gravadas=total_gravadas,
            status="OK",
            observacao=f"Star Schema carregado: geracao={n_ger}, pld={n_pld}, paradas={n_par}, clima={n_cli}",
            inicio_execucao=inicio,
            fim_execucao=datetime.now(),
        )
        print("[DW] Carga completa do Data Warehouse concluída com sucesso!")
    finally:
        conexao.close()

if __name__ == "__main__":
    executar_carga_dw()
