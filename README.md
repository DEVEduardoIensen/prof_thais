# Data Warehouse — Campo Real Energia

**Aluno:** Eduardo Iensen Prestes  
**Disciplina / Trabalho Prático:** Data Warehouse da Campo Real Energia (Centro Universitário Campo Real)

---

## 1. Sobre o Projeto

Solução de Engenharia de Dados, Qualidade de Dados, Modelagem Dimensional (*Star Schema*) e Business Intelligence para a **Campo Real Energia** (geradora com 9 usinas distribuídas nos 4 submercados do SIN: `SE`, `S`, `NE`, `N`).

O projeto responde à pergunta central:
> *"Quanto a nossa geração vale, onde e quando ganhamos ou perdemos dinheiro, e podemos confiar nos números que nos mostram?"*

---

## 2. Estrutura do Repositório

```text
├── config/
│   └── config.yaml
├── .env.example
├── requirements.txt
├── sql/
│   ├── 00_origem_campo_real_energia.sql
│   ├── 01_ddl_staging_e_qualidade.sql
│   ├── 02_ddl_dw_star_schema.sql
│   └── 03_views_analiticas_bi.sql
├── src/
│   ├── banco.py
│   ├── etapa1_extracao_ccee.py
│   ├── etapa1_extracao_clima.py
│   ├── etapa2_qualidade_dados.py
│   ├── etapa3_carga_dw.py
│   ├── etapa4_indicadores_e_relatorios.py
│   └── executar_pipeline.py
├── dados/
│   └── cache_bruto/
├── painel/
│   ├── Painel_Campo_Real_Energia.twbx
│   ├── Painel_Campo_Real_Energia.twb
│   ├── PAINEL_CAMPO_REAL_ENERGIA.html
│   └── dados_dw/
└── docs/
    ├── DOCUMENTO_MODELAGEM.md
    ├── RELATORIO_QUALIDADE.md
    └── graficos/
```

---

## 3. Requisitos de Ambiente

- **Banco de Dados:** PostgreSQL 13+
- **Python:** Python 3.10+
- **Bibliotecas Python:** listadas em `requirements.txt` (`psycopg2-binary`, `curl_cffi`, `requests`, `PyYAML`, `pandas`, `matplotlib`).

---

## 4. Passo a Passo para Reproduzir do Zero

### Passo 1 — Instalar dependências Python
```powershell
pip install -r requirements.txt
```

### Passo 2 — Configurar variáveis de conexão (opcional)
O arquivo `config/config.yaml` utiliza por padrão `localhost:5432`, banco `postgres`, usuário `postgres`. Caso seu ambiente utilize outra porta ou senha, copie `.env.example` para `.env` e ajuste:
```powershell
Copy-Item .env.example .env
```

### Passo 3 — Criar e popular o banco operacional de origem
```powershell
psql -h localhost -p 5432 -U postgres -d postgres -f sql/00_origem_campo_real_energia.sql
```

### Passo 4 — Executar o Pipeline Completo (Etapas 1, 2, 3 e 4)
```powershell
python src/executar_pipeline.py
```
Ou execute cada etapa individualmente na ordem abaixo:
```powershell
psql -h localhost -p 5432 -U postgres -d postgres -f sql/01_ddl_staging_e_qualidade.sql
python src/etapa1_extracao_ccee.py
python src/etapa1_extracao_clima.py
python src/etapa2_qualidade_dados.py
python src/etapa3_carga_dw.py
python src/etapa4_indicadores_e_relatorios.py
```

### Passo 5 — Abrir o Painel de BI
- **Navegador Web:** abra `painel/PAINEL_CAMPO_REAL_ENERGIA.html`.
- **Tableau Desktop / Reader:** abra `painel/Painel_Campo_Real_Energia.twbx`.
- **Power BI / DBeaver:** conecte ao PostgreSQL (`localhost:5432`, banco `postgres`, schema `dw_campo_real`) e consulte as tabelas fato/dimensão ou as views `vw_bi_1_*` a `vw_bi_6_*`.

---

## 5. Fontes de Dados Externas e Licenças (CC-BY 4.0)

1. **Câmara de Comercialização de Energia Elétrica (CCEE) — Portal de Dados Abertos:**
   - Conjunto de dados: `pld_horario` (PLD Horário 2025 e 2026 por submercado).
   - Endpoint CKAN: `https://dadosabertos.ccee.org.br/api/3/action/datastore_search`
   - Licença: **Creative Commons Attribution 4.0 International (CC-BY 4.0)** — Crédito atribuído à CCEE / GEPME. Dados brutos preservados em `raw_pld_horario` e padronizados para cruzamento com o calendário horário do DW em `pld_horario_tratado` e `dw_campo_real.fato_pld_horario`.
2. **Open-Meteo — Historical Weather API:**
   - Endpoint: `https://archive-api.open-meteo.com/v1/archive` (variáveis horárias: `shortwave_radiation`, `wind_speed_10m`, `wind_speed_100m`, `temperature_2m`, `precipitation` em `America/Sao_Paulo` para as coordenadas das 9 usinas em 2025).
   - Licença: **Creative Commons Attribution 4.0 International (CC-BY 4.0)** — Dados meteorológicos históricos reanalisados (ERA5 / Copernicus) fornecidos por Open-Meteo.com.
