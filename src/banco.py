import os
from pathlib import Path
from datetime import datetime
import yaml
import psycopg2

DIRETORIO_BASE = Path(__file__).resolve().parent.parent
CAMINHO_CONFIG = DIRETORIO_BASE / "config" / "config.yaml"
CAMINHO_ENV = DIRETORIO_BASE / ".env"

def carregar_arquivo_env():
    if CAMINHO_ENV.exists():
        for linha in CAMINHO_ENV.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            os.environ.setdefault(chave.strip(), valor.strip())

def carregar_configuracao() -> dict:
    carregar_arquivo_env()
    with open(CAMINHO_CONFIG, "r", encoding="utf-8") as arquivo:
        return yaml.safe_load(arquivo)

def obter_conexao():
    cfg_banco = carregar_configuracao()["banco_dados"]
    host = os.environ.get(cfg_banco["variavel_host"], cfg_banco["host_padrao"])
    porta = int(os.environ.get(cfg_banco["variavel_porta"], cfg_banco["porta_padrao"]))
    nome_banco = os.environ.get(cfg_banco["variavel_nome"], cfg_banco["nome_padrao"])
    usuario = os.environ.get(cfg_banco["variavel_usuario"], cfg_banco["usuario_padrao"])
    senha = os.environ.get(cfg_banco["variavel_senha"], cfg_banco["senha_padrao"])
    return psycopg2.connect(
        host=host,
        port=porta,
        dbname=nome_banco,
        user=usuario,
        password=senha,
    )

def garantir_colunas_log_carga(conexao):
    with conexao.cursor() as cursor:
        cursor.execute("""
            ALTER TABLE campo_real_energia.log_carga
                ADD COLUMN IF NOT EXISTS inicio_execucao TIMESTAMP,
                ADD COLUMN IF NOT EXISTS fim_execucao TIMESTAMP,
                ADD COLUMN IF NOT EXISTS linhas_lidas INTEGER,
                ADD COLUMN IF NOT EXISTS linhas_gravadas INTEGER,
                ADD COLUMN IF NOT EXISTS erro TEXT;
        """)
    conexao.commit()

def registrar_log_carga(
    conexao,
    tabela: str,
    arquivo: str,
    linhas_lidas: int,
    linhas_gravadas: int,
    status: str,
    observacao: str = "",
    inicio_execucao: datetime = None,
    fim_execucao: datetime = None,
    erro: str = None,
):
    garantir_colunas_log_carga(conexao)
    inicio_execucao = inicio_execucao or datetime.now()
    fim_execucao = fim_execucao or datetime.now()
    with conexao.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO campo_real_energia.log_carga
                (tabela, arquivo, linhas, status, observacao, registrado_em,
                 inicio_execucao, fim_execucao, linhas_lidas, linhas_gravadas, erro)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tabela[:40],
                (arquivo or "")[:100],
                linhas_gravadas,
                status[:20],
                (observacao or "")[:255],
                fim_execucao,
                inicio_execucao,
                fim_execucao,
                linhas_lidas,
                linhas_gravadas,
                erro,
            ),
        )
    conexao.commit()
