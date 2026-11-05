SET search_path TO dw_campo_real;

CREATE OR REPLACE VIEW vw_bi_1_geracao_mensal_usina AS
SELECT
    t.ano,
    t.mes,
    t.nome_mes,
    t.ano_mes,
    u.cod_usina,
    u.nome_usina,
    u.uf,
    u.municipio,
    f.cod_fonte,
    f.descricao_fonte,
    f.flag_renovavel,
    f.flag_intermitente,
    s.cod_submercado,
    s.nome_submercado,
    u.potencia_instalada_mw,
    COUNT(*) AS horas_periodo,
    ROUND(SUM(g.energia_gerada_mwh), 2) AS geracao_mwh,
    ROUND(SUM(g.energia_gerada_mwh) / 1000.0, 3) AS geracao_gwh,
    ROUND(
        (SUM(g.energia_gerada_mwh) / NULLIF(SUM(g.potencia_instalada_mw), 0)) * 100.0,
        2
    ) AS fator_capacidade_pct,
    ROUND(AVG(g.disponibilidade_mw), 2) AS disponibilidade_media_mw
FROM fato_geracao_horaria g
JOIN dim_tempo t         ON t.sk_tempo      = g.sk_tempo
JOIN dim_usina u         ON u.sk_usina      = g.sk_usina
JOIN dim_fonte_energia f ON f.sk_fonte      = g.sk_fonte
JOIN dim_submercado s    ON s.sk_submercado = g.sk_submercado
WHERE g.sk_usina > 0 AND t.ano = 2025
GROUP BY
    t.ano, t.mes, t.nome_mes, t.ano_mes,
    u.cod_usina, u.nome_usina, u.uf, u.municipio,
    f.cod_fonte, f.descricao_fonte, f.flag_renovavel, f.flag_intermitente,
    s.cod_submercado, s.nome_submercado, u.potencia_instalada_mw;

CREATE OR REPLACE VIEW vw_bi_2_receita_pld AS
SELECT
    t.ano,
    t.mes,
    t.nome_mes,
    t.ano_mes,
    u.cod_usina,
    u.nome_usina,
    f.cod_fonte,
    f.descricao_fonte,
    s.cod_submercado,
    s.nome_submercado,
    ROUND(SUM(g.energia_gerada_mwh), 2) AS geracao_limpa_mwh,
    ROUND(SUM(g.receita_pld_brl), 2) AS receita_limpa_brl,
    ROUND(SUM(g.energia_bruta_origem_mwh), 2) AS geracao_bruta_mwh,
    ROUND(SUM(g.receita_bruta_origem_brl), 2) AS receita_bruta_brl,
    ROUND(AVG(g.pld_hora_r_mwh), 2) AS pld_medio_simples_r_mwh,
    ROUND(
        SUM(g.receita_pld_brl) / NULLIF(SUM(g.energia_gerada_mwh), 0),
        2
    ) AS preco_capturado_r_mwh
FROM fato_geracao_horaria g
JOIN dim_tempo t         ON t.sk_tempo      = g.sk_tempo
JOIN dim_usina u         ON u.sk_usina      = g.sk_usina
JOIN dim_fonte_energia f ON f.sk_fonte      = g.sk_fonte
JOIN dim_submercado s    ON s.sk_submercado = g.sk_submercado
WHERE g.sk_usina > 0 AND t.ano = 2025
GROUP BY
    t.ano, t.mes, t.nome_mes, t.ano_mes,
    u.cod_usina, u.nome_usina,
    f.cod_fonte, f.descricao_fonte,
    s.cod_submercado, s.nome_submercado;

CREATE OR REPLACE VIEW vw_bi_3_captura_preco_fonte AS
WITH pld_submercado AS (
    SELECT
        p.sk_submercado,
        AVG(p.pld_r_mwh) AS pld_medio_submercado_ano
    FROM fato_pld_horario p
    JOIN dim_tempo t ON t.sk_tempo = p.sk_tempo
    WHERE t.ano = 2025
    GROUP BY p.sk_submercado
)
SELECT
    f.cod_fonte,
    f.descricao_fonte,
    f.flag_renovavel,
    f.flag_intermitente,
    s.cod_submercado,
    s.nome_submercado,
    ROUND(SUM(g.energia_gerada_mwh), 2) AS geracao_total_mwh,
    ROUND(SUM(g.receita_pld_brl), 2) AS receita_total_brl,
    ROUND(
        SUM(g.receita_pld_brl) / NULLIF(SUM(g.energia_gerada_mwh), 0),
        2
    ) AS preco_capturado_r_mwh,
    ROUND(ps.pld_medio_submercado_ano, 2) AS pld_medio_submercado_r_mwh,
    ROUND(
        (SUM(g.receita_pld_brl) / NULLIF(SUM(g.energia_gerada_mwh), 0))
        / NULLIF(ps.pld_medio_submercado_ano, 0),
        4
    ) AS razao_captura,
    ROUND(
        ((SUM(g.receita_pld_brl) / NULLIF(SUM(g.energia_gerada_mwh), 0))
        / NULLIF(ps.pld_medio_submercado_ano, 0) - 1.0) * 100.0,
        2
    ) AS premio_desconto_captura_pct
FROM fato_geracao_horaria g
JOIN dim_tempo t         ON t.sk_tempo      = g.sk_tempo
JOIN dim_fonte_energia f ON f.sk_fonte      = g.sk_fonte
JOIN dim_submercado s    ON s.sk_submercado = g.sk_submercado
JOIN pld_submercado ps   ON ps.sk_submercado = g.sk_submercado
WHERE g.sk_usina > 0 AND t.ano = 2025
GROUP BY
    f.cod_fonte, f.descricao_fonte, f.flag_renovavel, f.flag_intermitente,
    s.cod_submercado, s.nome_submercado, ps.pld_medio_submercado_ano;

CREATE OR REPLACE VIEW vw_bi_4_perfil_horario_mes AS
SELECT
    t.mes,
    t.nome_mes,
    t.hora,
    t.patamar_carga,
    f.cod_fonte,
    f.descricao_fonte,
    s.cod_submercado,
    ROUND(AVG(g.pld_hora_r_mwh), 2) AS pld_medio_r_mwh,
    ROUND(AVG(g.energia_gerada_mwh), 3) AS geracao_media_horaria_mwh,
    ROUND(SUM(g.energia_gerada_mwh), 2) AS geracao_total_mwh,
    ROUND(SUM(g.receita_pld_brl), 2) AS receita_total_brl,
    ROUND(AVG(g.energia_gerada_mwh / NULLIF(g.potencia_instalada_mw, 0)) * 100.0, 2) AS fator_capacidade_medio_pct
FROM fato_geracao_horaria g
JOIN dim_tempo t         ON t.sk_tempo      = g.sk_tempo
JOIN dim_fonte_energia f ON f.sk_fonte      = g.sk_fonte
JOIN dim_submercado s    ON s.sk_submercado = g.sk_submercado
WHERE g.sk_usina > 0 AND t.ano = 2025
GROUP BY
    t.mes, t.nome_mes, t.hora, t.patamar_carga,
    f.cod_fonte, f.descricao_fonte, s.cod_submercado;

CREATE OR REPLACE VIEW vw_bi_5_perdas_paradas AS
SELECT
    u.cod_usina,
    u.nome_usina,
    f.cod_fonte,
    f.descricao_fonte,
    s.cod_submercado,
    tp.tipo_parada,
    tp.motivo_parada,
    p.id_parada_natural,
    MIN(t.data_hora) AS inicio_parada,
    MAX(t.data_hora) AS fim_parada,
    COUNT(*) AS horas_indisponiveis,
    ROUND(SUM(p.energia_nao_gerada_estimada_mwh), 2) AS energia_nao_gerada_mwh,
    ROUND(AVG(p.pld_hora_r_mwh), 2) AS pld_medio_durante_parada_r_mwh,
    ROUND(SUM(p.perda_receita_estimada_brl), 2) AS perda_receita_estimada_brl
FROM fato_parada_usina p
JOIN dim_tempo t        ON t.sk_tempo       = p.sk_tempo
JOIN dim_usina u        ON u.sk_usina       = p.sk_usina
JOIN dim_fonte_energia f ON f.sk_fonte      = p.sk_fonte
JOIN dim_submercado s   ON s.sk_submercado  = p.sk_submercado
JOIN dim_tipo_parada tp ON tp.sk_tipo_parada = p.sk_tipo_parada
GROUP BY
    u.cod_usina, u.nome_usina, f.cod_fonte, f.descricao_fonte,
    s.cod_submercado, tp.tipo_parada, tp.motivo_parada, p.id_parada_natural;

CREATE OR REPLACE VIEW vw_bi_6_clima_geracao_diario AS
SELECT
    t.data,
    t.ano,
    t.mes,
    t.estacao_ano,
    u.cod_usina,
    u.nome_usina,
    f.cod_fonte,
    f.descricao_fonte,
    s.cod_submercado,
    ROUND(SUM(g.energia_gerada_mwh), 2) AS geracao_diaria_mwh,
    ROUND(AVG(g.energia_gerada_mwh / NULLIF(g.potencia_instalada_mw, 0)) * 100.0, 2) AS fator_capacidade_diario_pct,
    ROUND(AVG(g.pld_hora_r_mwh), 2) AS pld_medio_diario_r_mwh,
    ROUND(AVG(c.radiacao_solar_wm2), 2) AS radiacao_media_wm2,
    ROUND(MAX(c.radiacao_solar_wm2), 2) AS radiacao_max_wm2,
    ROUND(AVG(c.velocidade_vento_100m_kmh), 2) AS vento_medio_100m_kmh,
    ROUND(AVG(c.velocidade_vento_10m_kmh), 2) AS vento_medio_10m_kmh,
    ROUND(AVG(c.temperatura_2m_c), 2) AS temperatura_media_c,
    ROUND(SUM(c.precipitacao_mm), 2) AS precipitacao_total_mm
FROM fato_geracao_horaria g
JOIN fato_clima_horario c ON c.sk_tempo = g.sk_tempo AND c.sk_usina = g.sk_usina
JOIN dim_tempo t          ON t.sk_tempo = g.sk_tempo
JOIN dim_usina u          ON u.sk_usina = g.sk_usina
JOIN dim_fonte_energia f  ON f.sk_fonte = g.sk_fonte
JOIN dim_submercado s     ON s.sk_submercado = g.sk_submercado
WHERE g.sk_usina > 0 AND t.ano = 2025
GROUP BY
    t.data, t.ano, t.mes, t.estacao_ano,
    u.cod_usina, u.nome_usina, f.cod_fonte, f.descricao_fonte, s.cod_submercado;
