from banco import obter_conexao
from etapa1_extracao_ccee import executar_extracao_ccee
from etapa1_extracao_clima import executar_extracao_clima
from etapa2_qualidade_dados import executar_avaliacao_qualidade
from etapa3_carga_dw import executar_script_sql, executar_carga_dw
from etapa4_indicadores_e_relatorios import executar_etapa4

def executar_tudo():
    print("=" * 72)
    print(" INICIANDO PIPELINE COMPLETO - DATA WAREHOUSE CAMPO REAL ENERGIA")
    print("=" * 72)

    conexao = obter_conexao()
    try:
        executar_script_sql(conexao, "sql/01_ddl_staging_e_qualidade.sql")
    finally:
        conexao.close()

    executar_extracao_ccee()
    executar_extracao_clima()
    executar_avaliacao_qualidade()
    executar_carga_dw()
    executar_etapa4()

    print("=" * 72)
    print(" PIPELINE CONCLUIDO COM SUCESSO!")
    print("=" * 72)

if __name__ == "__main__":
    executar_tudo()
