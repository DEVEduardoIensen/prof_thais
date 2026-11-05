import json
import time
from datetime import datetime
from pathlib import Path
import requests as requisicoes_http
from psycopg2.extras import execute_values

from banco import DIRETORIO_BASE, carregar_configuracao, obter_conexao, registrar_log_carga

COLUNAS_HORARIAS_ESPERADAS = {
    "time",
    "shortwave_radiation",
    "wind_speed_10m",
    "wind_speed_100m",
    "temperature_2m",
    "precipitation",
}

def obter_usinas_canonicas(conexao) -> list:
    with conexao.cursor() as cursor:
        cursor.execute("""
            SELECT cod_usina, nome, uf, municipio, latitude, longitude
            FROM campo_real_energia.usina
            WHERE cod_usina ~ '^CRE-[A-Z]{3}-[0-9]{3}$'
            ORDER BY cod_usina
        """)
        linhas = cursor.fetchall()

    usinas = []
    for cod_usina, nome, uf, municipio, lat, lon in linhas:
        lat_num = float(lat)
        lon_num = float(lon)
        if lat_num > 0 and uf in {"MG", "SP", "PR", "SC", "RS", "BA", "RN", "TO", "MA"}:
            lat_num = -abs(lat_num)
        usinas.append({
            "cod_usina": cod_usina,
            "nome": nome.strip(),
            "uf": uf,
            "municipio": municipio.strip(),
            "lat_original": float(lat),
            "lon_original": float(lon),
            "lat_consulta": round(lat_num, 6),
            "lon_consulta": round(lon_num, 6),
        })
    return usinas

def consultar_open_meteo(usina: dict, cfg_meteo: dict, pasta_cache: Path) -> dict:
    cod = usina["cod_usina"]
    arquivo_cache = pasta_cache / f"open_meteo_{cod}_2025.json"
    if arquivo_cache.exists():
        dados = json.loads(arquivo_cache.read_text(encoding="utf-8"))
        if "hourly" in dados and dados["hourly"].get("time"):
            print(f"[Open-Meteo] {cod}: carregado do cache local ({len(dados['hourly']['time'])} horas).")
            return dados

    parametros = {
        "latitude": usina["lat_consulta"],
        "longitude": usina["lon_consulta"],
        "start_date": cfg_meteo["data_inicio"],
        "end_date": cfg_meteo["data_fim"],
        "hourly": ",".join(cfg_meteo["variaveis_horarias"]),
        "timezone": cfg_meteo["fuso_horario"],
    }

    max_tentativas = int(cfg_meteo.get("max_tentativas", 4))
    espera_base = int(cfg_meteo.get("espera_segundos", 2))
    tempo_limite = int(cfg_meteo.get("tempo_limite_segundos", 30))
    ultimo_erro = None

    for tentativa in range(1, max_tentativas + 2):
        try:
            resposta = requisicoes_http.get(cfg_meteo["url_base"], params=parametros, timeout=tempo_limite)
            if resposta.status_code == 429:
                espera = espera_base * (2 ** tentativa)
                print(f"[Open-Meteo] Limite de requisicoes (429) para {cod}. Aguardando {espera}s...")
                time.sleep(espera)
                continue
            resposta.raise_for_status()
            dados = resposta.json()
            if "hourly" not in dados or not dados["hourly"].get("time"):
                raise ValueError(f"Resposta vazia da Open-Meteo para {cod}")
            faltantes = COLUNAS_HORARIAS_ESPERADAS - set(dados["hourly"].keys())
            if faltantes:
                raise RuntimeError(f"Mudanca de colunas na Open-Meteo ({cod}). Ausentes: {faltantes}")
            arquivo_cache.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
            time.sleep(0.4)
            return dados
        except Exception as erro:
            ultimo_erro = erro
            espera = espera_base * tentativa
            print(f"[Open-Meteo] Tentativa {tentativa}/{max_tentativas + 1} para {cod} falhou ({erro}). Aguardando {espera}s...")
            time.sleep(espera)

    raise RuntimeError(f"Falha ao extrair clima de {cod} apos {max_tentativas + 1} tentativas: {ultimo_erro}")

def executar_extracao_clima():
    cfg = carregar_configuracao()
    cfg_meteo = cfg["apis"]["open_meteo"]
    pasta_cache = DIRETORIO_BASE / cfg["caminhos"]["pasta_cache_bruto"]
    pasta_cache.mkdir(parents=True, exist_ok=True)

    conexao = obter_conexao()
    inicio_global = datetime.now()
    total_lidas = 0
    total_gravadas = 0

    try:
        usinas = obter_usinas_canonicas(conexao)
        with conexao.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE campo_real_energia.raw_clima_horario RESTART IDENTITY;")
            cursor.execute("TRUNCATE TABLE campo_real_energia.clima_horario_tratado;")
        conexao.commit()

        for u in usinas:
            inicio_usina = datetime.now()
            cod = u["cod_usina"]
            dados = consultar_open_meteo(u, cfg_meteo, pasta_cache)
            extraido_em = datetime.now()

            bloco_horario = dados["hourly"]
            tempos = bloco_horario["time"]
            radiacao = bloco_horario["shortwave_radiation"]
            vento10 = bloco_horario["wind_speed_10m"]
            vento100 = bloco_horario["wind_speed_100m"]
            temperatura = bloco_horario["temperature_2m"]
            precipitacao = bloco_horario["precipitation"]

            linhas_brutas = []
            linhas_tratadas = []

            for indice, texto_hora in enumerate(tempos):
                val_rad = radiacao[indice]
                val_v10 = vento10[indice]
                val_v100 = vento100[indice]
                val_temp = temperatura[indice]
                val_prec = precipitacao[indice]

                linhas_brutas.append((
                    cod,
                    u["lat_consulta"],
                    u["lon_consulta"],
                    str(texto_hora),
                    None if val_rad is None else str(val_rad),
                    None if val_v10 is None else str(val_v10),
                    None if val_v100 is None else str(val_v100),
                    None if val_temp is None else str(val_temp),
                    None if val_prec is None else str(val_prec),
                    "OPEN_METEO_ARCHIVE_API",
                    extraido_em,
                ))

                data_hora = datetime.strptime(texto_hora, "%Y-%m-%dT%H:%M")
                linhas_tratadas.append((
                    cod,
                    data_hora,
                    u["lat_consulta"],
                    u["lon_consulta"],
                    0.0 if val_rad is None else max(0.0, round(float(val_rad), 2)),
                    0.0 if val_v10 is None else max(0.0, round(float(val_v10), 2)),
                    0.0 if val_v100 is None else max(0.0, round(float(val_v100), 2)),
                    None if val_temp is None else round(float(val_temp), 2),
                    0.0 if val_prec is None else max(0.0, round(float(val_prec), 2)),
                    "OPEN_METEO_ARCHIVE_API",
                    extraido_em,
                ))

            with conexao.cursor() as cursor:
                execute_values(
                    cursor,
                    """
                    INSERT INTO campo_real_energia.raw_clima_horario
                        (cod_usina, latitude_consulta, longitude_consulta, data_hora_iso,
                         shortwave_radiation_raw, wind_speed_10m_raw, wind_speed_100m_raw,
                         temperature_2m_raw, precipitation_raw, origem, extraido_em)
                    VALUES %s
                    """,
                    linhas_brutas,
                    page_size=2000,
                )
                execute_values(
                    cursor,
                    """
                    INSERT INTO campo_real_energia.clima_horario_tratado
                        (cod_usina, data_hora, latitude_real, longitude_real,
                         radiacao_wm2, vento_10m_kmh, vento_100m_kmh,
                         temperatura_c, precipitacao_mm, origem, extraido_em)
                    VALUES %s
                    ON CONFLICT (cod_usina, data_hora) DO UPDATE
                    SET radiacao_wm2 = EXCLUDED.radiacao_wm2,
                        vento_10m_kmh = EXCLUDED.vento_10m_kmh,
                        vento_100m_kmh = EXCLUDED.vento_100m_kmh,
                        temperatura_c = EXCLUDED.temperatura_c,
                        precipitacao_mm = EXCLUDED.precipitacao_mm,
                        tratado_em = CURRENT_TIMESTAMP
                    """,
                    linhas_tratadas,
                    page_size=2000,
                )
            conexao.commit()

            total_lidas += len(linhas_brutas)
            total_gravadas += len(linhas_tratadas)
            registrar_log_carga(
                conexao=conexao,
                tabela="raw_clima_horario / clima_horario_tratado",
                arquivo=f"open_meteo_{cod}_2025.json",
                linhas_lidas=len(linhas_brutas),
                linhas_gravadas=len(linhas_tratadas),
                status="OK",
                observacao=f"Clima horario 2025 extraido para {cod} ({u['municipio']}-{u['uf']})",
                inicio_execucao=inicio_usina,
                fim_execucao=datetime.now(),
            )
            print(f"[Open-Meteo] {cod} gravado: {len(linhas_tratadas)} horas.")

        print(f"[Open-Meteo] Extracao concluida: {total_gravadas} registros horarios para {len(usinas)} usinas.")

    except Exception as erro:
        conexao.rollback()
        registrar_log_carga(
            conexao=conexao,
            tabela="raw_clima_horario",
            arquivo="open_meteo_2025",
            linhas_lidas=total_lidas,
            linhas_gravadas=total_gravadas,
            status="ERRO",
            observacao="Falha na extracao climatica Open-Meteo",
            inicio_execucao=inicio_global,
            fim_execucao=datetime.now(),
            erro=str(erro),
        )
        raise
    finally:
        conexao.close()

if __name__ == "__main__":
    executar_extracao_clima()
