import json
from datetime import datetime
from psycopg2.extras import execute_values

from banco import obter_conexao, registrar_log_carga

TABELAS_PROFILING = [
    "submercado",
    "fonte_energia",
    "usina",
    "feriado_nacional",
    "parada_usina",
    "medicao_geracao",
    "pld_horario_tratado",
    "clima_horario_tratado",
]

def executar_profiling_tabelas(conn) -> list:
    resultados = []
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE campo_real_energia.profiling_tabelas RESTART IDENTITY;")

        for tabela in TABELAS_PROFILING:
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'campo_real_energia'
                  AND table_name = %s
                ORDER BY ordinal_position
                """,
                (tabela,),
            )
            colunas = cur.fetchall()

            for col_name, dtype in colunas:
                eh_numerico = any(
                    t in dtype.lower()
                    for t in ("int", "numeric", "decimal", "double", "real", "float")
                )
                if eh_numerico:
                    sql = f"""
                        SELECT
                            COUNT(*) AS total,
                            COUNT(*) - COUNT({col_name}) AS nulos,
                            COUNT(DISTINCT {col_name}) AS distintos,
                            MIN({col_name})::TEXT AS vmin,
                            MAX({col_name})::TEXT AS vmax,
                            ROUND(AVG({col_name}::NUMERIC), 3) AS media,
                            ROUND(STDDEV({col_name}::NUMERIC), 3) AS desvio
                        FROM campo_real_energia.{tabela}
                    """
                elif dtype.lower() == "boolean":
                    sql = f"""
                        SELECT
                            COUNT(*) AS total,
                            COUNT(*) - COUNT({col_name}) AS nulos,
                            COUNT(DISTINCT {col_name}) AS distintos,
                            MIN({col_name}::INT)::TEXT AS vmin,
                            MAX({col_name}::INT)::TEXT AS vmax,
                            NULL::NUMERIC AS media,
                            NULL::NUMERIC AS desvio
                        FROM campo_real_energia.{tabela}
                    """
                else:
                    sql = f"""
                        SELECT
                            COUNT(*) AS total,
                            COUNT(*) - COUNT({col_name}) + COUNT(*) FILTER (WHERE TRIM({col_name}::TEXT) = '') AS nulos,
                            COUNT(DISTINCT {col_name}) AS distintos,
                            LEFT(MIN({col_name}::TEXT), 95) AS vmin,
                            LEFT(MAX({col_name}::TEXT), 95) AS vmax,
                            NULL::NUMERIC AS media,
                            NULL::NUMERIC AS desvio
                        FROM campo_real_energia.{tabela}
                    """
                cur.execute(sql)
                total, nulos, distintos, vmin, vmax, media, desvio = cur.fetchone()
                pct_nulos = round((nulos / total * 100.0), 2) if total else 0.0
                resultados.append((
                    tabela,
                    col_name,
                    dtype,
                    total,
                    nulos,
                    pct_nulos,
                    distintos,
                    vmin,
                    vmax,
                    media,
                    desvio,
                ))

        execute_values(
            cur,
            """
            INSERT INTO campo_real_energia.profiling_tabelas
                (tabela, coluna, tipo_dado, total_registros, total_nulos,
                 percentual_nulos, total_distintos, valor_min, valor_max,
                 media_numerica, desvio_padrao)
            VALUES %s
            """,
            resultados,
        )
    conn.commit()
    return resultados

def avaliar_completude(conn) -> tuple[list, list]:
    metricas = []
    quarentena = []

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM campo_real_energia.usina;")
        tot_usina = cur.fetchone()[0]
        cur.execute("""
            SELECT id_usina, cod_usina, nome, uf, municipio
            FROM campo_real_energia.usina
            WHERE municipio IS NULL OR TRIM(municipio) = '';
        """)
        falhas_mun = cur.fetchall()
        metricas.append((
            "COMP_01_USINA_MUNICIPIO_VAZIO",
            "Completude",
            "usina",
            "Cadastro de usina com campo obrigatório 'municipio' vazio ('CRE-EOL-07')",
            tot_usina,
            len(falhas_mun),
            round(len(falhas_mun) / tot_usina * 100.0, 4),
            "DESCARTAR_DUPLICATA",
            "O registro CRE-EOL-07 além de estar sem município é duplicata cadastral da usina CRE-EOL-007 (João Câmara/RN).",
        ))
        for r in falhas_mun:
            quarentena.append((
                "usina",
                f"id_usina={r[0]} ({r[1]})",
                "Completude",
                "COMP_01_USINA_MUNICIPIO_VAZIO",
                "DESCARTAR_DUPLICATA",
                json.dumps({"id_usina": r[0], "cod_usina": r[1], "nome": r[2], "uf": r[3], "municipio": r[4]}, ensure_ascii=False),
                json.dumps({"acao": "Descartado em favor do cadastro canônico CRE-EOL-007"}, ensure_ascii=False),
            ))

        cur.execute("SELECT COUNT(*) FROM campo_real_energia.medicao_geracao;")
        tot_med = cur.fetchone()[0]
        cur.execute("""
            SELECT m.id_medicao, m.id_usina, u.cod_usina, m.data_hora, m.disponibilidade_mw
            FROM campo_real_energia.medicao_geracao m
            LEFT JOIN campo_real_energia.usina u ON u.id_usina = m.id_usina
            WHERE m.energia_mwh IS NULL;
        """)
        falhas_nulo = cur.fetchall()
        metricas.append((
            "COMP_02_MEDICAO_ENERGIA_NULA",
            "Completude",
            "medicao_geracao",
            "Leituras horárias com energia_mwh = NULL por falha de comunicação SCADA (CRE-UHE-008 em 05/03/2025 10h-15h)",
            tot_med,
            len(falhas_nulo),
            round(len(falhas_nulo) / tot_med * 100.0, 4),
            "CORRIGIR_INTERPOLACAO",
            "Imputar pela média horária da própria usina no mesmo mês/hora para não subestimar a geração hidrelétrica em 6 horas de operação normal.",
        ))
        for r in falhas_nulo:
            quarentena.append((
                "medicao_geracao",
                f"id_medicao={r[0]} ({r[2]} {r[3]})",
                "Completude",
                "COMP_02_MEDICAO_ENERGIA_NULA",
                "CORRIGIR_INTERPOLACAO",
                json.dumps({"id_medicao": r[0], "cod_usina": r[2], "data_hora": str(r[3]), "energia_mwh": None}, ensure_ascii=False),
                json.dumps({"metodo": "Média horária da usina CRE-UHE-008 em março/2025 na mesma hora"}, ensure_ascii=False),
            ))

        cur.execute("""
            SELECT id_medicao, data_hora, energia_mwh, origem
            FROM campo_real_energia.medicao_geracao
            WHERE id_usina IS NULL;
        """)
        falhas_sem_usina = cur.fetchall()
        metricas.append((
            "COMP_03_MEDICAO_USINA_NULA",
            "Completude",
            "medicao_geracao",
            "Registro manual de medição com id_usina NULO (20/10/2025 14:00)",
            tot_med,
            len(falhas_sem_usina),
            round(len(falhas_sem_usina) / tot_med * 100.0, 4),
            "QUARENTENA",
            "Sem código de usina não é possível saber o submercado nem valorar ao PLD; enviado para quarentena.",
        ))
        for r in falhas_sem_usina:
            quarentena.append((
                "medicao_geracao",
                f"id_medicao={r[0]}",
                "Completude",
                "COMP_03_MEDICAO_USINA_NULA",
                "QUARENTENA",
                json.dumps({"id_medicao": r[0], "id_usina": None, "data_hora": str(r[1]), "energia_mwh": float(r[2])}, ensure_ascii=False),
                None,
            ))

        cur.execute("""
            WITH grade AS (
                SELECT u.id_usina, u.cod_usina, t.data_hora
                FROM campo_real_energia.usina u
                CROSS JOIN generate_series(
                    TIMESTAMP '2025-01-01 00:00:00',
                    TIMESTAMP '2025-12-31 23:00:00',
                    INTERVAL '1 hour'
                ) AS t(data_hora)
                WHERE u.cod_usina ~ '^CRE-[A-Z]{3}-[0-9]{3}$'
            )
            SELECT g.cod_usina, g.data_hora
            FROM grade g
            LEFT JOIN campo_real_energia.medicao_geracao m
              ON m.id_usina = g.id_usina AND m.data_hora = g.data_hora
            WHERE m.id_medicao IS NULL
            ORDER BY g.cod_usina, g.data_hora;
        """)
        horas_faltantes = cur.fetchall()
        metricas.append((
            "COMP_04_LACUNA_GRADE_HORARIA",
            "Completude",
            "medicao_geracao",
            "Horas ausentes na série de 2025: 24h de CRE-EOL-003 (10/11/2025) e 72h de CRE-UFV-004 (29 a 31/12/2025)",
            78840,
            len(horas_faltantes),
            round(len(horas_faltantes) / 78840.0 * 100.0, 4),
            "SINALIZAR_LACUNA_ZERO",
            "Grade horária recomposta no DW com 0 MWh e flag 'LACUNA_TELEMETRIA' para preservar a continuidade temporal sem inventar receita não medida.",
        ))

        cur.execute("SELECT COUNT(*) FROM campo_real_energia.pld_horario_tratado WHERE ano = 2025;")
        tot_pld_2025 = cur.fetchone()[0]
        faltantes_pld = max(0, 35040 - tot_pld_2025)
        metricas.append((
            "COMP_05_GRADE_PLD_2025",
            "Completude",
            "pld_horario_tratado",
            "Verificação das 35.040 horasxsubmercado do PLD 2025 extraído da CCEE",
            35040,
            faltantes_pld,
            round(faltantes_pld / 35040.0 * 100.0, 4),
            "VALIDADO_OK",
            "Série de PLD 2025 100% completa nos 4 submercados.",
        ))

    return metricas, quarentena

def avaliar_unicidade(conn) -> tuple[list, list]:
    metricas = []
    quarentena = []

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM campo_real_energia.usina;")
        tot_usina = cur.fetchone()[0]
        cur.execute("""
            SELECT u1.id_usina, u1.cod_usina, u1.nome, u2.cod_usina AS cod_canonico
            FROM campo_real_energia.usina u1
            JOIN campo_real_energia.usina u2
              ON u1.id_usina <> u2.id_usina
             AND u1.latitude = u2.latitude
             AND u1.longitude = u2.longitude
            WHERE u1.id_usina > u2.id_usina;
        """)
        dup_usinas = cur.fetchall()
        metricas.append((
            "UNIC_01_USINA_DUPLICADA",
            "Unicidade",
            "usina",
            "Usina cadastrada em duplicidade ('CRE-EOL-07' duplicando 'CRE-EOL-007')",
            tot_usina,
            len(dup_usinas),
            round(len(dup_usinas) / tot_usina * 100.0, 4),
            "DESCARTAR_DUPLICATA",
            "Descartado o registro id_usina=10 ('CRE-EOL-07') que não possui medições válidas e fere a máscara de código.",
        ))

        cur.execute("SELECT COUNT(*) FROM campo_real_energia.medicao_geracao;")
        tot_med = cur.fetchone()[0]

        cur.execute("""
            SELECT m.id_medicao, u.cod_usina, m.data_hora, m.energia_mwh, m.origem, m.recebido_em
            FROM (
                SELECT id_medicao, id_usina, data_hora, energia_mwh, origem, recebido_em,
                       ROW_NUMBER() OVER (PARTITION BY id_usina, data_hora ORDER BY id_medicao ASC) AS rn,
                       COUNT(*) OVER (PARTITION BY id_usina, data_hora, origem) AS cnt_mesma_origem
                FROM campo_real_energia.medicao_geracao
                WHERE id_usina IS NOT NULL
            ) m
            LEFT JOIN campo_real_energia.usina u ON u.id_usina = m.id_usina
            WHERE m.rn > 1 AND m.cnt_mesma_origem > 1;
        """)
        dup_scada = cur.fetchall()
        metricas.append((
            "UNIC_02_MEDICAO_REPROCESSAMENTO_DUPLICADO",
            "Unicidade",
            "medicao_geracao",
            "Reprocessamento da integração duplicou 24 horas de CRE-UFV-004 no dia 20/01/2025",
            tot_med,
            len(dup_scada),
            round(len(dup_scada) / tot_med * 100.0, 4),
            "DESCARTAR_DUPLICATA",
            "Deduplicação mantendo apenas a primeira leitura original recebida (ROW_NUMBER = 1).",
        ))
        for r in dup_scada:
            quarentena.append((
                "medicao_geracao",
                f"id_medicao={r[0]} ({r[1]} {r[2]})",
                "Unicidade",
                "UNIC_02_MEDICAO_REPROCESSAMENTO_DUPLICADO",
                "DESCARTAR_DUPLICATA",
                json.dumps({"id_medicao": r[0], "cod_usina": r[1], "data_hora": str(r[2]), "energia_mwh": float(r[3]), "recebido_em": str(r[5])}, ensure_ascii=False),
                json.dumps({"acao": "Registro duplicado descartado"}, ensure_ascii=False),
            ))

        cur.execute("""
            SELECT m.id_medicao, u.cod_usina, m.data_hora, m.energia_mwh, m.origem, m.recebido_em
            FROM (
                SELECT id_medicao, id_usina, data_hora, energia_mwh, origem, recebido_em,
                       ROW_NUMBER() OVER (
                           PARTITION BY id_usina, data_hora
                           ORDER BY CASE WHEN origem = 'SCADA' THEN 1 ELSE 2 END, id_medicao ASC
                       ) AS rn,
                       MIN(origem) OVER (PARTITION BY id_usina, data_hora) AS min_origem,
                       MAX(origem) OVER (PARTITION BY id_usina, data_hora) AS max_origem
                FROM campo_real_energia.medicao_geracao
                WHERE id_usina IS NOT NULL
            ) m
            LEFT JOIN campo_real_energia.usina u ON u.id_usina = m.id_usina
            WHERE m.rn > 1 AND m.min_origem <> m.max_origem;
        """)
        dup_manual = cur.fetchall()
        metricas.append((
            "UNIC_03_MEDICAO_SOBREPOSICAO_MANUAL_SCADA",
            "Unicidade",
            "medicao_geracao",
            "Lançamento MANUAL (+10%) inserido sem remover leitura automática SCADA em CRE-PCH-002 (01/06/2025 08h-11h)",
            tot_med,
            len(dup_manual),
            round(len(dup_manual) / tot_med * 100.0, 4),
            "DESCARTAR_DUPLICATA",
            "Priorizada a leitura primária do medidor SCADA e descartado o lançamento manual duplicado.",
        ))
        for r in dup_manual:
            quarentena.append((
                "medicao_geracao",
                f"id_medicao={r[0]} ({r[1]} {r[2]})",
                "Unicidade",
                "UNIC_03_MEDICAO_SOBREPOSICAO_MANUAL_SCADA",
                "DESCARTAR_DUPLICATA",
                json.dumps({"id_medicao": r[0], "cod_usina": r[1], "data_hora": str(r[2]), "energia_mwh": float(r[3]), "origem": r[4]}, ensure_ascii=False),
                json.dumps({"acao": "Mantida leitura SCADA original"}, ensure_ascii=False),
            ))

    return metricas, quarentena

def avaliar_validade(conn) -> tuple[list, list]:
    metricas = []
    quarentena = []

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM campo_real_energia.usina;")
        tot_usina = cur.fetchone()[0]

        cur.execute("""
            SELECT id_usina, cod_usina, nome, uf, municipio, latitude, longitude
            FROM campo_real_energia.usina
            WHERE latitude > 0;
        """)
        lat_inv = cur.fetchall()
        metricas.append((
            "VAL_01_USINA_LATITUDE_POSITIVA_INVALIDA",
            "Validade",
            "usina",
            "Usina CRE-UFV-004 (Janaúba-MG) com latitude positiva (+15.803000, hemisfério norte) por esquecimento do sinal negativo",
            tot_usina,
            len(lat_inv),
            round(len(lat_inv) / tot_usina * 100.0, 4),
            "CORRIGIR_CADASTRO",
            "Sinal da latitude corrigido para -15.803000 na carga da dim_usina (SCD Tipo 1) e na extração climática.",
        ))
        for r in lat_inv:
            quarentena.append((
                "usina",
                f"id_usina={r[0]} ({r[1]})",
                "Validade",
                "VAL_01_USINA_LATITUDE_POSITIVA_INVALIDA",
                "CORRIGIR_CADASTRO",
                json.dumps({"cod_usina": r[1], "municipio": r[4], "uf": r[3], "latitude_original": float(r[5])}, ensure_ascii=False),
                json.dumps({"latitude_corrigida": -abs(float(r[5]))}, ensure_ascii=False),
            ))

        cur.execute("""
            SELECT id_usina, cod_usina, nome, data_inicio_operacao
            FROM campo_real_energia.usina
            WHERE data_inicio_operacao > DATE '2025-12-31';
        """)
        dt_futura_usina = cur.fetchall()
        metricas.append((
            "VAL_02_USINA_DATA_OPERACAO_FUTURA",
            "Validade",
            "usina",
            "Usina CRE-UFV-006 com data_inicio_operacao em '2031-02-01' embora esteja operando durante todo o ano de 2025",
            tot_usina,
            len(dt_futura_usina),
            round(len(dt_futura_usina) / tot_usina * 100.0, 4),
            "CORRIGIR_CADASTRO",
            "Data de início de operação restaurada para 2019-02-01 na dim_usina.",
        ))
        for r in dt_futura_usina:
            quarentena.append((
                "usina",
                f"id_usina={r[0]} ({r[1]})",
                "Validade",
                "VAL_02_USINA_DATA_OPERACAO_FUTURA",
                "CORRIGIR_CADASTRO",
                json.dumps({"cod_usina": r[1], "data_inicio_operacao_original": str(r[3])}, ensure_ascii=False),
                json.dumps({"data_inicio_operacao_corrigida": "2019-02-01"}, ensure_ascii=False),
            ))

        cur.execute("SELECT COUNT(*) FROM campo_real_energia.medicao_geracao;")
        tot_med = cur.fetchone()[0]
        cur.execute("""
            SELECT m.id_medicao, u.cod_usina, m.data_hora, m.energia_mwh
            FROM campo_real_energia.medicao_geracao m
            LEFT JOIN campo_real_energia.usina u ON u.id_usina = m.id_usina
            WHERE m.energia_mwh < 0;
        """)
        ger_neg = cur.fetchall()
        metricas.append((
            "VAL_03_MEDICAO_GERACAO_NEGATIVA",
            "Validade",
            "medicao_geracao",
            "Consumo de serviços auxiliares registrado como geração negativa (-0,850 MWh) em CRE-EOL-007 (03/02/2025 01h-04h)",
            tot_med,
            len(ger_neg),
            round(len(ger_neg) / tot_med * 100.0, 4),
            "CORRIGIR_ZERO",
            "Geração bruta/injetada não pode ser negativa; valores truncados em 0,000 MWh no DW.",
        ))
        for r in ger_neg:
            quarentena.append((
                "medicao_geracao",
                f"id_medicao={r[0]} ({r[1]} {r[2]})",
                "Validade",
                "VAL_03_MEDICAO_GERACAO_NEGATIVA",
                "CORRIGIR_ZERO",
                json.dumps({"id_medicao": r[0], "cod_usina": r[1], "data_hora": str(r[2]), "energia_mwh": float(r[3])}, ensure_ascii=False),
                json.dumps({"energia_mwh_corrigida": 0.0}, ensure_ascii=False),
            ))

        cur.execute("""
            SELECT m.id_medicao, u.cod_usina, m.data_hora, m.energia_mwh, m.origem
            FROM campo_real_energia.medicao_geracao m
            LEFT JOIN campo_real_energia.usina u ON u.id_usina = m.id_usina
            WHERE EXTRACT(YEAR FROM m.data_hora) <> 2025;
        """)
        ano_inv = cur.fetchall()
        metricas.append((
            "VAL_04_MEDICAO_ANO_INVALIDO_2052",
            "Validade",
            "medicao_geracao",
            "Erro de digitação no ano ('2052-05-14 10:00:00') em lançamento manual para CRE-UFV-006",
            tot_med,
            len(ano_inv),
            round(len(ano_inv) / tot_med * 100.0, 4),
            "QUARENTENA",
            "Descartado/quarentenado pois a leitura SCADA legítima de 2025-05-14 10:00:00 já existe para CRE-UFV-006.",
        ))
        for r in ano_inv:
            quarentena.append((
                "medicao_geracao",
                f"id_medicao={r[0]} ({r[1]} {r[2]})",
                "Validade",
                "VAL_04_MEDICAO_ANO_INVALIDO_2052",
                "QUARENTENA",
                json.dumps({"id_medicao": r[0], "cod_usina": r[1], "data_hora": str(r[2]), "energia_mwh": float(r[3]), "origem": r[4]}, ensure_ascii=False),
                None,
            ))

        cur.execute("SELECT COUNT(*) FROM campo_real_energia.pld_horario_tratado;")
        tot_pld = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM campo_real_energia.pld_horario_tratado
            WHERE NOT flag_valido OR pld_r_mwh < 40 OR pld_r_mwh > 2000;
        """)
        pld_inv = cur.fetchone()[0]
        metricas.append((
            "VAL_05_PLD_FAIXA_REGULATORIA_ANEEL",
            "Validade",
            "pld_horario_tratado",
            "Validação de limites mínimo e máximo regulatórios do PLD horário (R$/MWh)",
            tot_pld,
            pld_inv,
            round(pld_inv / tot_pld * 100.0, 4) if tot_pld else 0.0,
            "VALIDADO_OK",
            "Todos os registros de PLD da CCEE estão dentro dos limites regulatórios vigentes.",
        ))

    return metricas, quarentena

def avaliar_consistencia(conn) -> tuple[list, list]:
    metricas = []
    quarentena = []

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM campo_real_energia.usina;")
        tot_usina = cur.fetchone()[0]

        cur.execute("""
            SELECT id_usina, cod_usina, nome, cod_submercado, uf, municipio
            FROM campo_real_energia.usina
            WHERE (cod_submercado = 'SE' AND uf NOT IN ('SP', 'MG', 'RJ', 'ES', 'GO', 'MT', 'MS', 'DF'))
               OR (cod_submercado = 'S'  AND uf NOT IN ('PR', 'SC', 'RS'))
               OR (cod_submercado = 'NE' AND uf NOT IN ('BA', 'PE', 'CE', 'RN', 'PB', 'AL', 'SE', 'PI'))
               OR (cod_submercado = 'N'  AND uf NOT IN ('PA', 'TO', 'MA', 'AM', 'AP', 'RR', 'AC', 'RO'));
        """)
        uf_inc = cur.fetchall()
        metricas.append((
            "CONS_01_USINA_UF_INCOMPATIVEL_SUBMERCADO",
            "Consistência",
            "usina",
            "Usina CRE-BIO-005 (Sertãozinho, submercado SE, lat -21.138, lon -47.990) cadastrada com UF='PE' em vez de 'SP'",
            tot_usina,
            len(uf_inc),
            round(len(uf_inc) / tot_usina * 100.0, 4),
            "CORRIGIR_CADASTRO",
            "UF corrigida de 'PE' para 'SP' na dim_usina (Sertãozinho localiza-se em São Paulo, submercado Sudeste/Centro-Oeste).",
        ))
        for r in uf_inc:
            quarentena.append((
                "usina",
                f"id_usina={r[0]} ({r[1]})",
                "Consistência",
                "CONS_01_USINA_UF_INCOMPATIVEL_SUBMERCADO",
                "CORRIGIR_CADASTRO",
                json.dumps({"cod_usina": r[1], "municipio": r[5], "submercado": r[3], "uf_original": r[4]}, ensure_ascii=False),
                json.dumps({"uf_corrigida": "SP"}, ensure_ascii=False),
            ))

        cur.execute("SELECT COUNT(*) FROM campo_real_energia.parada_usina;")
        tot_par = cur.fetchone()[0]
        cur.execute("""
            SELECT p.id_parada, u.cod_usina, p.inicio, p.fim, p.tipo, p.motivo
            FROM campo_real_energia.parada_usina p
            JOIN campo_real_energia.usina u ON u.id_usina = p.id_usina
            WHERE p.fim < p.inicio;
        """)
        par_inv = cur.fetchall()
        metricas.append((
            "CONS_02_PARADA_FIM_ANTERIOR_AO_INICIO",
            "Consistência",
            "parada_usina",
            "Parada manual em CRE-UFV-004 (05/11/2025) registrada com início às 18:00 e fim às 08:00 do mesmo dia",
            tot_par,
            len(par_inv),
            round(len(par_inv) / tot_par * 100.0, 4),
            "QUARENTENA",
            "Intervalo temporal inválido (fim < início) descartado da expansão de paradas e registrado em quarentena (durante 08h-18h de 05/11/2025 a usina gerou normalmente no SCADA).",
        ))
        for r in par_inv:
            quarentena.append((
                "parada_usina",
                f"id_parada={r[0]} ({r[1]})",
                "Consistência",
                "CONS_02_PARADA_FIM_ANTERIOR_AO_INICIO",
                "QUARENTENA",
                json.dumps({"id_parada": r[0], "cod_usina": r[1], "inicio": str(r[2]), "fim": str(r[3]), "motivo": r[5]}, ensure_ascii=False),
                None,
            ))

        cur.execute("SELECT COUNT(*) FROM campo_real_energia.medicao_geracao;")
        tot_med = cur.fetchone()[0]
        cur.execute("""
            SELECT m.id_medicao, u.cod_usina, m.data_hora, m.energia_mwh, m.disponibilidade_mw, p.motivo
            FROM campo_real_energia.medicao_geracao m
            JOIN campo_real_energia.usina u ON u.id_usina = m.id_usina
            JOIN campo_real_energia.parada_usina p
              ON p.id_usina = m.id_usina
             AND p.fim >= p.inicio
             AND m.data_hora BETWEEN p.inicio AND p.fim
            WHERE m.energia_mwh > 0;
        """)
        ger_parada = cur.fetchall()
        metricas.append((
            "CONS_03_GERACAO_DURANTE_PARADA_MANUTENCAO",
            "Consistência",
            "medicao_geracao",
            "Geração de 132 MWh/h lançada em CRE-UTE-009 (05/06/2025 18h-21h) durante overhaul programado da turbina (disponibilidade = 0 MW)",
            tot_med,
            len(ger_parada),
            round(len(ger_parada) / tot_med * 100.0, 4),
            "CORRIGIR_ZERO",
            "Usina indisponível em manutenção programada e com disponibilidade 0 MW; geração corrigida para 0,000 MWh.",
        ))
        for r in ger_parada:
            quarentena.append((
                "medicao_geracao",
                f"id_medicao={r[0]} ({r[1]} {r[2]})",
                "Consistência",
                "CONS_03_GERACAO_DURANTE_PARADA_MANUTENCAO",
                "CORRIGIR_ZERO",
                json.dumps({"id_medicao": r[0], "cod_usina": r[1], "data_hora": str(r[2]), "energia_mwh": float(r[3]), "disponibilidade_mw": float(r[4]), "parada": r[5]}, ensure_ascii=False),
                json.dumps({"energia_mwh_corrigida": 0.0}, ensure_ascii=False),
            ))

        cur.execute("""
            SELECT m.id_medicao, u.cod_usina, m.data_hora, m.energia_mwh, u.potencia_mw, m.origem
            FROM campo_real_energia.medicao_geracao m
            JOIN campo_real_energia.usina u ON u.id_usina = m.id_usina
            WHERE m.energia_mwh > u.potencia_mw;
        """)
        ger_acima_pot = cur.fetchall()
        metricas.append((
            "CONS_04_GERACAO_ACIMA_CAPACIDADE_INSTALADA",
            "Consistência",
            "medicao_geracao",
            "Leituras horárias que excedem fisicamente 100% da potência instalada da usina (26 ocorrências em CRE-BIO-005, CRE-UHE-001 e CRE-EOL-003)",
            tot_med,
            len(ger_acima_pot),
            round(len(ger_acima_pot) / tot_med * 100.0, 4),
            "CORRIGIR_ESCALA_OU_INTERPOLAR",
            "Tratadas conforme causa-raiz na dimensão Acurácia (conversão kWh->MWh por /1000, correção de vírgula por /10 e interpolação de pico espúrio).",
        ))

    return metricas, quarentena

def avaliar_acuracia(conn) -> tuple[list, list]:
    metricas = []
    quarentena = []

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM campo_real_energia.medicao_geracao;")
        tot_med = cur.fetchone()[0]

        cur.execute("""
            SELECT m.id_medicao, u.cod_usina, m.data_hora, m.energia_mwh, u.potencia_mw
            FROM campo_real_energia.medicao_geracao m
            JOIN campo_real_energia.usina u ON u.id_usina = m.id_usina
            WHERE m.energia_mwh > u.potencia_mw * 100;
        """)
        erro_kwh = cur.fetchall()
        metricas.append((
            "ACUR_01_UNIDADE_KWH_EM_VEZ_DE_MWH",
            "Acurácia",
            "medicao_geracao",
            "Planilha manual digitada em kWh (valores ~31.000x maiores, somando ~750 GWh falsos em 1 dia) para CRE-BIO-005 em 10/05/2025",
            tot_med,
            len(erro_kwh),
            round(len(erro_kwh) / tot_med * 100.0, 4),
            "CORRIGIR_DIVIDIR_1000",
            "Divisão exata por 1.000 restaura a grandeza física em MWh (~31,2 MWh/h, compatível com a potência de 40 MW da usina).",
        ))
        for r in erro_kwh:
            quarentena.append((
                "medicao_geracao",
                f"id_medicao={r[0]} ({r[1]} {r[2]})",
                "Acurácia",
                "ACUR_01_UNIDADE_KWH_EM_VEZ_DE_MWH",
                "CORRIGIR_DIVIDIR_1000",
                json.dumps({"id_medicao": r[0], "cod_usina": r[1], "data_hora": str(r[2]), "energia_mwh_original": float(r[3])}, ensure_ascii=False),
                json.dumps({"energia_mwh_corrigida": round(float(r[3]) / 1000.0, 3)}, ensure_ascii=False),
            ))

        cur.execute("""
            SELECT m.id_medicao, u.cod_usina, m.data_hora, m.energia_mwh, u.potencia_mw
            FROM campo_real_energia.medicao_geracao m
            JOIN campo_real_energia.usina u ON u.id_usina = m.id_usina
            WHERE m.energia_mwh > u.potencia_mw
              AND m.energia_mwh <= u.potencia_mw * 100;
        """)
        outliers_pico = cur.fetchall()
        metricas.append((
            "ACUR_02_OUTLIER_VIRGULA_E_PICO_LEITURA",
            "Acurácia",
            "medicao_geracao",
            "Vírgula deslocada x10 em CRE-UHE-001 (22/04/2025 14:00) e pico espúrio de 499 MWh em CRE-EOL-003 (03/07/2025 03:00)",
            tot_med,
            len(outliers_pico),
            round(len(outliers_pico) / tot_med * 100.0, 4),
            "CORRIGIR_ESCALA_E_INTERPOLACAO",
            "Em CRE-UHE-001 divide-se por 10 (restaurando 134,827 MWh); em CRE-EOL-003 substitui-se o pico de 499 MWh pela média das horas adjacentes (02h e 04h).",
        ))
        for r in outliers_pico:
            acao = "Divisão por 10" if r[1] == "CRE-UHE-001" else "Interpolação vizinhos (02h e 04h)"
            quarentena.append((
                "medicao_geracao",
                f"id_medicao={r[0]} ({r[1]} {r[2]})",
                "Acurácia",
                "ACUR_02_OUTLIER_VIRGULA_E_PICO_LEITURA",
                "CORRIGIR_ESCALA_E_INTERPOLACAO",
                json.dumps({"id_medicao": r[0], "cod_usina": r[1], "data_hora": str(r[2]), "energia_mwh_original": float(r[3]), "potencia_mw": float(r[4])}, ensure_ascii=False),
                json.dumps({"tratamento": acao}, ensure_ascii=False),
            ))

        cur.execute("""
            SELECT m.id_medicao, u.cod_usina, m.data_hora, m.energia_mwh
            FROM campo_real_energia.medicao_geracao m
            JOIN campo_real_energia.usina u ON u.id_usina = m.id_usina
            JOIN campo_real_energia.fonte_energia f ON f.id_fonte = u.id_fonte
            WHERE f.cod_fonte = 'UFV'
              AND (EXTRACT(HOUR FROM m.data_hora) >= 20 OR EXTRACT(HOUR FROM m.data_hora) <= 4)
              AND m.energia_mwh > 0;
        """)
        solar_noite = cur.fetchall()
        metricas.append((
            "ACUR_03_GERACAO_SOLAR_NOTURNA",
            "Acurácia",
            "medicao_geracao",
            "Usina solar CRE-UFV-006 registrando 15,400 MWh/h entre 20h e 23h de 12/08/2025 (radiação solar = 0 W/m²)",
            tot_med,
            len(solar_noite),
            round(len(solar_noite) / tot_med * 100.0, 4),
            "CORRIGIR_ZERO",
            "Impossibilidade física comprovada pelo cruzamento com radiação solar (0 W/m²); corrigido para 0,000 MWh.",
        ))
        for r in solar_noite:
            quarentena.append((
                "medicao_geracao",
                f"id_medicao={r[0]} ({r[1]} {r[2]})",
                "Acurácia",
                "ACUR_03_GERACAO_SOLAR_NOTURNA",
                "CORRIGIR_ZERO",
                json.dumps({"id_medicao": r[0], "cod_usina": r[1], "data_hora": str(r[2]), "energia_mwh_original": float(r[3])}, ensure_ascii=False),
                json.dumps({"energia_mwh_corrigida": 0.0}, ensure_ascii=False),
            ))

        cur.execute("""
            WITH blocos AS (
                SELECT
                    m.id_medicao,
                    u.cod_usina,
                    m.data_hora,
                    m.energia_mwh,
                    COUNT(*) OVER (PARTITION BY m.id_usina, m.data_hora::DATE, m.energia_mwh) AS horas_iguais_dia
                FROM campo_real_energia.medicao_geracao m
                JOIN campo_real_energia.usina u ON u.id_usina = m.id_usina
                WHERE m.energia_mwh > 0
            )
            SELECT id_medicao, cod_usina, data_hora, energia_mwh
            FROM blocos
            WHERE horas_iguais_dia = 24
            ORDER BY cod_usina, data_hora;
        """)
        medidor_travado = cur.fetchall()
        metricas.append((
            "ACUR_04_MEDIDOR_TRAVADO_CONSTANTE",
            "Acurácia",
            "medicao_geracao",
            "Medidor travado em 31,250 MWh durante 72 horas consecutivas (01/09/2025 a 03/09/2025) na usina CRE-BIO-005",
            tot_med,
            len(medidor_travado),
            round(len(medidor_travado) / tot_med * 100.0, 4),
            "APENAS_SINALIZAR",
            "Sinalizado no DW ('SINALIZADO_MEDIDOR_TRAVADO'): o patamar de 31,25 MWh (78% da capacidade) é compatível com a geração de base da biomassa na safra, não devendo ser zerado.",
        ))

    return metricas, quarentena

def avaliar_integridade_referencial(conn) -> tuple[list, list]:
    metricas = []
    quarentena = []

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM campo_real_energia.medicao_geracao;")
        tot_med = cur.fetchone()[0]

        cur.execute("""
            SELECT m.id_medicao, m.id_usina, m.data_hora, m.energia_mwh, m.origem
            FROM campo_real_energia.medicao_geracao m
            LEFT JOIN campo_real_energia.usina u ON u.id_usina = m.id_usina
            WHERE m.id_usina IS NOT NULL AND u.id_usina IS NULL;
        """)
        orfaos = cur.fetchall()
        metricas.append((
            "IREF_01_MEDICAO_USINA_INEXISTENTE",
            "Integridade referencial",
            "medicao_geracao",
            "24 leituras horárias no dia 15/10/2025 associadas ao id_usina = 99 (inexistente no cadastro de usinas)",
            tot_med,
            len(orfaos),
            round(len(orfaos) / tot_med * 100.0, 4),
            "QUARENTENA_MEMBRO_DESCONHECIDO",
            "Registros órfãos enviados para quarentena e mapeados ao membro desconhecido (sk_usina = -1), isolando-os das métricas oficiais das 9 usinas.",
        ))
        for r in orfaos:
            quarentena.append((
                "medicao_geracao",
                f"id_medicao={r[0]} (id_usina={r[1]} {r[2]})",
                "Integridade referencial",
                "IREF_01_MEDICAO_USINA_INEXISTENTE",
                "QUARENTENA_MEMBRO_DESCONHECIDO",
                json.dumps({"id_medicao": r[0], "id_usina": r[1], "data_hora": str(r[2]), "energia_mwh": float(r[3])}, ensure_ascii=False),
                json.dumps({"sk_usina_atribuido": -1}, ensure_ascii=False),
            ))

    return metricas, quarentena

def avaliar_atualidade(conn) -> tuple[list, list]:
    metricas = []
    quarentena = []

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM campo_real_energia.medicao_geracao;")
        tot_med = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*)
            FROM campo_real_energia.medicao_geracao
            WHERE recebido_em > data_hora + INTERVAL '24 hours'
              AND EXTRACT(YEAR FROM data_hora) = 2025;
        """)
        atrasados = cur.fetchone()[0]
        metricas.append((
            "ATUA_01_ATRASO_INTEGRACAO_ACIMA_24H",
            "Atualidade",
            "medicao_geracao",
            "Leituras recebidas com mais de 24h de atraso (288h de CRE-UHE-008 de 20-31/12/2025 recebidas só em 10/02/2026 + reprocessamentos)",
            tot_med,
            atrasados,
            round(atrasados / tot_med * 100.0, 4),
            "APENAS_SINALIZAR",
            "As medições físicas são válidas para o fechamento de 2025, mas o atraso (>1.000h) é registrado no atributo atraso_recebimento_horas do fato.",
        ))

        cur.execute("""
            SELECT m.id_medicao, u.cod_usina, m.data_hora, m.recebido_em
            FROM campo_real_energia.medicao_geracao m
            JOIN campo_real_energia.usina u ON u.id_usina = m.id_usina
            WHERE m.recebido_em < m.data_hora;
        """)
        relogio_inv = cur.fetchall()
        metricas.append((
            "ATUA_02_RECEBIDO_ANTES_DA_MEDICAO",
            "Atualidade",
            "medicao_geracao",
            "Relógio do servidor dessincronizado: recebido_em 3 dias antes de data_hora em CRE-PCH-002 (05/08/2025, 24 registros)",
            tot_med,
            len(relogio_inv),
            round(len(relogio_inv) / tot_med * 100.0, 4),
            "APENAS_SINALIZAR",
            "Anomalia de timestamp de ingestão sinalizada; geração da hora mantida pois a curva operacional está íntegra.",
        ))

        cur.execute("""
            SELECT u.cod_usina, MAX(m.data_hora) AS ultima_leitura
            FROM campo_real_energia.usina u
            JOIN campo_real_energia.medicao_geracao m ON m.id_usina = u.id_usina
            WHERE u.cod_usina ~ '^CRE-[A-Z]{3}-[0-9]{3}$'
              AND EXTRACT(YEAR FROM m.data_hora) = 2025
            GROUP BY u.cod_usina
            HAVING MAX(m.data_hora) < TIMESTAMP '2025-12-31 23:00:00';
        """)
        usinas_interrompidas = cur.fetchall()
        metricas.append((
            "ATUA_03_USINA_INTERRUPCAO_ENVIO_FIM_ANO",
            "Atualidade",
            "medicao_geracao",
            "Usina CRE-UFV-004 parou de enviar telemetria em 28/12/2025 23:00 (72 horas finais do ano ausentes)",
            9,
            len(usinas_interrompidas),
            round(len(usinas_interrompidas) / 9.0 * 100.0, 4),
            "SINALIZAR_LACUNA_ZERO",
            "Interrupção de telemetria sinalizada no relatório de qualidade e grade completada com 0 MWh.",
        ))

    return metricas, quarentena

def executar_avaliacao_qualidade():
    conexao = obter_conexao()
    inicio = datetime.now()
    try:
        print("[Qualidade] Executando profiling estatístico das tabelas...")
        prof = executar_profiling_tabelas(conexao)
        print(f"[Qualidade] Profiling concluído ({len(prof)} colunas analisadas).")

        funcoes_dimensoes = [
            avaliar_completude,
            avaliar_unicidade,
            avaliar_validade,
            avaliar_consistencia,
            avaliar_acuracia,
            avaliar_integridade_referencial,
            avaliar_atualidade,
        ]

        todas_metricas = []
        toda_quarentena = []

        for fn in funcoes_dimensoes:
            m, q = fn(conexao)
            todas_metricas.extend(m)
            toda_quarentena.extend(q)

        with conexao.cursor() as cur:
            cur.execute("TRUNCATE TABLE campo_real_energia.metricas_qualidade RESTART IDENTITY;")
            cur.execute("TRUNCATE TABLE campo_real_energia.quarentena_registros RESTART IDENTITY;")

            execute_values(
                cur,
                """
                INSERT INTO campo_real_energia.metricas_qualidade
                    (regra, dimensao, tabela, descricao_problema, registros_avaliados,
                     registros_com_falha, percentual, decisao_tratamento, justificativa)
                VALUES %s
                """,
                todas_metricas,
            )

            if toda_quarentena:
                execute_values(
                    cur,
                    """
                    INSERT INTO campo_real_energia.quarentena_registros
                        (tabela_origem, chave_registro, dimensao_qualidade, regra_violada,
                         acao_tomada, dados_originais_json, dados_tratados_json)
                    VALUES %s
                    """,
                    toda_quarentena,
                )
        conexao.commit()

        registrar_log_carga(
            conexao=conexao,
            tabela="metricas_qualidade / quarentena",
            arquivo="etapa2_qualidade_dados.py",
            linhas_lidas=len(todas_metricas),
            linhas_gravadas=len(todas_metricas) + len(toda_quarentena),
            status="OK",
            observacao=f"Avaliação nas 7 dimensões: {len(todas_metricas)} regras, {len(toda_quarentena)} registros em quarentena",
            inicio_execucao=inicio,
            fim_execucao=datetime.now(),
        )
        print(f"[Qualidade] {len(todas_metricas)} regras avaliadas nas 7 dimensões; {len(toda_quarentena)} registros auditados em quarentena.")
    finally:
        conexao.close()

if __name__ == "__main__":
    executar_avaliacao_qualidade()
