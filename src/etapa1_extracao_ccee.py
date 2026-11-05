import csv
import io
import json
import time
from datetime import datetime
from pathlib import Path
from curl_cffi import requests as requisicoes_cffi
from psycopg2.extras import execute_values

from banco import DIRETORIO_BASE, carregar_configuracao, obter_conexao, registrar_log_carga

MAPA_SUBMERCADO = {
    "SUDESTE": "SE",
    "SUDESTE / CENTRO-OESTE": "SE",
    "SUDESTE/CENTRO-OESTE": "SE",
    "SE": "SE",
    "SUL": "S",
    "S": "S",
    "NORDESTE": "NE",
    "NE": "NE",
    "NORTE": "N",
    "N": "N",
}

COLUNAS_ESPERADAS = {"MES_REFERENCIA", "SUBMERCADO", "DIA", "HORA", "PLD_HORA"}

def requisitar_com_tentativas(sessao, url: str, parametros: dict, tempo_limite: int, max_tentativas: int, espera_base: int) -> dict:
    ultimo_erro = None
    for tentativa in range(1, max_tentativas + 2):
        try:
            resposta = sessao.get(url, params=parametros, timeout=tempo_limite)
            if resposta.status_code == 429:
                espera = espera_base * (2 ** tentativa)
                print(f"[CCEE] Limite de requisicoes (HTTP 429). Aguardando {espera}s...")
                time.sleep(espera)
                continue
            resposta.raise_for_status()
            dados = resposta.json()
            if not dados or not dados.get("success") or "result" not in dados:
                raise ValueError(f"Resposta vazia ou invalida da API CCEE: {str(dados)[:200]}")
            return dados["result"]
        except Exception as erro:
            ultimo_erro = erro
            espera = espera_base * tentativa
            print(f"[CCEE] Tentativa {tentativa}/{max_tentativas + 1} falhou ({erro}). Aguardando {espera}s...")
            time.sleep(espera)
    raise RuntimeError(f"Falha ao consultar API CCEE apos {max_tentativas + 1} tentativas: {ultimo_erro}")

def descobrir_recursos_pld(sessao, cfg_api: dict, pasta_cache: Path) -> dict:
    arquivo_pacote = pasta_cache / "ccee_package_show.json"
    if arquivo_pacote.exists():
        dados_pacote = json.loads(arquivo_pacote.read_text(encoding="utf-8"))["result"]
    else:
        url = f"{cfg_api['url_base']}/package_show"
        dados_pacote = requisitar_com_tentativas(
            sessao,
            url,
            {"id": cfg_api["id_pacote"]},
            cfg_api["tempo_limite_segundos"],
            cfg_api["max_tentativas"],
            cfg_api["espera_segundos"],
        )
        arquivo_pacote.write_text(json.dumps({"success": True, "result": dados_pacote}, ensure_ascii=False), encoding="utf-8")

    recursos_por_ano = {}
    for recurso in dados_pacote.get("resources", []):
        nome = (recurso.get("name") or "").lower()
        for ano in cfg_api["anos_alvo"]:
            if str(ano) in nome and recurso.get("id"):
                recursos_por_ano[int(ano)] = {
                    "id_recurso": recurso["id"],
                    "nome": recurso.get("name"),
                    "url_csv": recurso.get("url"),
                }
    return recursos_por_ano

def baixar_via_url_recurso_ckan(ano: int, url_csv: str, tempo_limite: int) -> list:
    print(f"[CCEE] Acionando download do recurso oficial CKAN ({url_csv}) para {ano}...")
    ultimo_erro = None
    for tentativa in range(1, 7):
        try:
            resposta = requisicoes_cffi.get(url_csv, impersonate="chrome120", timeout=tempo_limite)
            resposta.raise_for_status()
            texto = resposta.content.decode("utf-8-sig", errors="replace")
            leitor = csv.DictReader(io.StringIO(texto), delimiter=";")
            colunas = set(leitor.fieldnames or [])
            faltantes = COLUNAS_ESPERADAS - colunas
            if faltantes:
                raise RuntimeError(f"Mudanca de colunas detectada no CSV CCEE ({ano}). Ausentes: {faltantes}")
            registros = [dict(linha) for linha in leitor]
            if not registros:
                raise RuntimeError(f"Resposta vazia no download do recurso CCEE ({ano}).")
            return registros
        except Exception as erro:
            ultimo_erro = erro
            print(f"[CCEE] Tentativa {tentativa}/6 no download do recurso {ano} falhou ({erro}). Aguardando {tentativa}s...")
            time.sleep(tentativa)
    raise RuntimeError(f"Falha ao baixar recurso CCEE {ano} apos 6 tentativas: {ultimo_erro}")

def extrair_ano_ccee(sessao, ano: int, info_recurso: dict, cfg_api: dict, pasta_cache: Path) -> list:
    arquivo_cache = pasta_cache / f"ccee_pld_horario_{ano}.json"
    if arquivo_cache.exists():
        registros = json.loads(arquivo_cache.read_text(encoding="utf-8"))
        if registros:
            print(f"[CCEE] Ano {ano}: carregado do cache local ({len(registros)} registros).")
            return registros

    id_recurso = info_recurso["id_recurso"]
    url_csv = info_recurso.get("url_csv")
    limite = int(cfg_api.get("limite_pagina", 5000))
    deslocamento = 0
    todos_registros = []
    url = f"{cfg_api['url_base']}/datastore_search"

    print(f"[CCEE] Extraindo {ano} (resource_id={id_recurso}) via datastore_search paginado...")
    try:
        while True:
            resultado = requisitar_com_tentativas(
                sessao,
                url,
                {"resource_id": id_recurso, "limit": limite, "offset": deslocamento},
                tempo_limite=8,
                max_tentativas=1,
                espera_base=1,
            )
            lote = resultado.get("records", [])
            total = resultado.get("total", 0)
            if not lote:
                break

            if deslocamento == 0:
                colunas_recebidas = set(lote[0].keys())
                faltantes = COLUNAS_ESPERADAS - colunas_recebidas
                if faltantes:
                    raise RuntimeError(f"Mudanca de esquema detectada na API CCEE ({ano}). Colunas ausentes: {faltantes}")

            todos_registros.extend(lote)
            deslocamento += len(lote)
            print(f"  -> Paginacao {ano}: {len(todos_registros)}/{total} registros obtidos...")
            if deslocamento >= total or len(lote) < limite:
                break
    except Exception as erro_paginacao:
        print(f"[CCEE] Paginacao datastore_search encontrou limite de tempo ({erro_paginacao}).")
        if url_csv:
            todos_registros = baixar_via_url_recurso_ckan(ano, url_csv, cfg_api["tempo_limite_segundos"])
        else:
            raise

    if not todos_registros:
        raise RuntimeError(f"Nenhum registro retornado pela CCEE para o ano {ano}.")

    arquivo_cache.write_text(json.dumps(todos_registros, ensure_ascii=False), encoding="utf-8")
    return todos_registros

def converter_registro_pld(registro: dict, extraido_em: datetime, cfg_regras: dict) -> tuple:
    mes_ref = str(registro.get("MES_REFERENCIA", "")).strip()
    dia_texto = str(registro.get("DIA", "")).strip()
    hora = int(float(registro.get("HORA", 0)))

    if "/" in dia_texto:
        data_base = datetime.strptime(dia_texto[:10], "%d/%m/%Y")
    elif "-" in dia_texto and len(dia_texto) >= 10:
        data_base = datetime.strptime(dia_texto[:10], "%Y-%m-%d")
    else:
        ano = int(mes_ref[:4])
        mes = int(mes_ref[4:6])
        dia = int(dia_texto)
        data_base = datetime(ano, mes, dia)

    data_hora = data_base.replace(hour=hora, minute=0, second=0, microsecond=0)
    submercado_bruto = str(registro.get("SUBMERCADO", "")).strip().upper()
    cod_submercado = MAPA_SUBMERCADO.get(submercado_bruto, "NA")

    pld_texto = str(registro.get("PLD_HORA", "0")).replace(",", ".").strip()
    valor_pld = round(float(pld_texto), 2)

    pld_min = float(cfg_regras.get("pld_min_regulatorio", 40.0))
    pld_max = float(cfg_regras.get("pld_max_regulatorio", 2000.0))
    flag_valido = (cod_submercado in {"SE", "S", "NE", "N"}) and (pld_min <= valor_pld <= pld_max)

    return (
        data_hora,
        cod_submercado,
        submercado_bruto,
        data_hora.year,
        data_hora.month,
        hora,
        valor_pld,
        flag_valido,
        "CCEE_CKAN_API",
        extraido_em,
    )

def executar_extracao_ccee():
    cfg = carregar_configuracao()
    cfg_api = cfg["apis"]["ccee_ckan"]
    cfg_regras = cfg["regras_negocio"]
    pasta_cache = DIRETORIO_BASE / cfg["caminhos"]["pasta_cache_bruto"]
    pasta_cache.mkdir(parents=True, exist_ok=True)

    conexao = obter_conexao()
    sessao = requisicoes_cffi.Session(impersonate="chrome120")

    try:
        recursos = descobrir_recursos_pld(sessao, cfg_api, pasta_cache)
        with conexao.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE campo_real_energia.raw_pld_horario RESTART IDENTITY;")
            cursor.execute("TRUNCATE TABLE campo_real_energia.pld_horario_tratado;")
        conexao.commit()

        for ano in cfg_api["anos_alvo"]:
            inicio = datetime.now()
            if ano not in recursos:
                raise RuntimeError(f"Recurso CCEE nao encontrado para o ano {ano}")
            info_recurso = recursos[ano]
            id_recurso = info_recurso["id_recurso"]

            try:
                registros = extrair_ano_ccee(sessao, ano, info_recurso, cfg_api, pasta_cache)
                extraido_em = datetime.now()

                linhas_brutas = [
                    (
                        ano,
                        id_recurso,
                        str(r.get("MES_REFERENCIA", "")),
                        str(r.get("DIA", "")),
                        str(r.get("HORA", "")),
                        str(r.get("SUBMERCADO", "")),
                        str(r.get("PLD_HORA", "")),
                        json.dumps(r, ensure_ascii=False),
                        "CCEE_CKAN_API",
                        extraido_em,
                    )
                    for r in registros
                ]

                dicionario_tratadas = {}
                for r in registros:
                    tupla = converter_registro_pld(r, extraido_em, cfg_regras)
                    chave = (tupla[0], tupla[1])
                    dicionario_tratadas[chave] = tupla
                linhas_tratadas = list(dicionario_tratadas.values())

                with conexao.cursor() as cursor:
                    execute_values(
                        cursor,
                        """
                        INSERT INTO campo_real_energia.raw_pld_horario
                            (ano_referencia, resource_id, mes_referencia, dia, hora,
                             submercado, pld_hora_raw, payload_json, origem, extraido_em)
                        VALUES %s
                        """,
                        linhas_brutas,
                        page_size=2000,
                    )
                    execute_values(
                        cursor,
                        """
                        INSERT INTO campo_real_energia.pld_horario_tratado
                            (data_hora, cod_submercado, submercado_ccee, ano, mes, hora,
                             pld_r_mwh, flag_valido, origem, extraido_em)
                        VALUES %s
                        ON CONFLICT (data_hora, cod_submercado) DO UPDATE
                        SET pld_r_mwh = EXCLUDED.pld_r_mwh,
                            flag_valido = EXCLUDED.flag_valido,
                            tratado_em = CURRENT_TIMESTAMP
                        """,
                        linhas_tratadas,
                        page_size=2000,
                    )
                conexao.commit()

                fim = datetime.now()
                registrar_log_carga(
                    conexao=conexao,
                    tabela="raw_pld_horario / pld_horario_tratado",
                    arquivo=f"ccee_pld_horario_{ano} ({id_recurso})",
                    linhas_lidas=len(registros),
                    linhas_gravadas=len(linhas_tratadas),
                    status="OK",
                    observacao=f"Extracao CKAN CCEE PLD {ano} concluida (bruta + tratada)",
                    inicio_execucao=inicio,
                    fim_execucao=fim,
                )
                print(f"[CCEE] Ano {ano} gravado no banco: {len(linhas_brutas)} brutos, {len(linhas_tratadas)} tratados.")

            except Exception as erro_ano:
                conexao.rollback()
                registrar_log_carga(
                    conexao=conexao,
                    tabela="raw_pld_horario",
                    arquivo=f"ccee_pld_horario_{ano}",
                    linhas_lidas=0,
                    linhas_gravadas=0,
                    status="ERRO",
                    observacao=f"Falha na extracao CCEE {ano}",
                    inicio_execucao=inicio,
                    fim_execucao=datetime.now(),
                    erro=str(erro_ano),
                )
                raise
    finally:
        sessao.close()
        conexao.close()

if __name__ == "__main__":
    executar_extracao_ccee()
