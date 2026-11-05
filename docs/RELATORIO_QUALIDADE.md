# Relatório de Qualidade de Dados — Campo Real Energia (Etapa 2)

**Aluno:** Eduardo Iensen Prestes  
**Tabelas de Auditoria no Banco:** `campo_real_energia.profiling_tabelas`, `campo_real_energia.metricas_qualidade` e `campo_real_energia.quarentena_registros`

---

## 1. Princípio de Governança Adotado

Conforme exigido na especificação do trabalho, **nenhuma tabela operacional de origem foi alterada para "consertar" dados**. Todas as tabelas de origem (`usina`, `parada_usina`, `medicao_geracao`, etc.) permanecem no estado original em `campo_real_energia`, e todas as correções, deduplicações, imputações e filtros de quarentena ocorrem exclusivamente no pipeline ETL a caminho do Star Schema (`dw_campo_real`).

---

## 2. Síntese do Profiling das Tabelas (`profiling_tabelas`)

O módulo `executar_profiling_tabelas()` analisou todas as **59 colunas** distribuídas nas 8 tabelas operacionais e tratadas:

| Tabela | Total de Linhas | Colunas | Principais Achados no Profiling |
| :--- | ---: | ---: | :--- |
| `submercado` | 4 | 3 | 100% completa; chaves naturais `SE`, `S`, `NE`, `N`. |
| `fonte_energia` | 6 | 5 | 100% completa; 6 fontes (`UHE`, `PCH`, `UFV`, `EOL`, `BIO`, `UTE`). |
| `usina` | 10 | 12 | Deveria conter 9 usinas; possui 1 duplicata (`CRE-EOL-07`) com `municipio` vazio (`''`), `latitude` máxima positiva (`+15.803`) e `data_inicio_operacao` máxima em `2031-02-01`. |
| `feriado_nacional` | 13 | 3 | 13 feriados e pontos facultativos de 2025 sem nulos. |
| `parada_usina` | 11 | 6 | 10 paradas válidas e 1 registro inconsistente onde `fim < inicio` (`id_parada = 11`). |
| `medicao_geracao` | 78.798 | 7 | 6 nulos em `energia_mwh`, 1 nulo em `id_usina`, `id_usina` máximo = `99` (órfão), `energia_mwh` mínima = `-0.850` e máxima = `35.125,000` (erro de unidade kWh), `data_hora` máxima em `2052-05-14`. |
| `pld_horario_tratado` | 64.800 | 11 | 35.040 registros em 2025 (4 submercados $\times$ 8.760h) + 29.760 registros em 2026; PLD em 2025 variando dentro dos limites regulatórios da ANEEL. |
| `clima_horario_tratado` | 78.840 | 11 | 9 usinas $\times$ 8.760h = 78.840 registros completos sem nulos. |

---

## 3. Problemas Encontrados por Dimensão de Qualidade (`metricas_qualidade`)

Foram implementadas **7 funções reutilizáveis** em `src/etapa2_qualidade_dados.py` (`avaliar_completude`, `avaliar_unicidade`, `avaliar_validade`, `avaliar_consistencia`, `avaliar_acuracia`, `avaliar_integridade_referencial`, `avaliar_atualidade`), totalizando **25 regras verificadas** e **103 registros críticos arquivados em `quarentena_registros`**:

| Dimensão | Código da Regra | Tabela | Problema Encontrado | Avaliados | Com Falha | % Falha | Decisão de Tratamento | Justificativa Técnica e de Negócio |
| :--- | :--- | :--- | :--- | ---: | ---: | ---: | :--- | :--- |
| **1. Completude** | `COMP_01_USINA_MUNICIPIO_VAZIO` | `usina` | Usina `CRE-EOL-07` com campo obrigatório `municipio` em branco (`''`) | 10 | 1 | 10,0000% | **Descartar / Quarentena** | Além de incompleto, `CRE-EOL-07` é duplicata cadastral da usina `CRE-EOL-007` (João Câmara-RN). |
| **1. Completude** | `COMP_02_MEDICAO_ENERGIA_NULA` | `medicao_geracao` | 6 horas com `energia_mwh = NULL` em `CRE-UHE-008` (`05/03/2025` das 10h às 15h) | 78.798 | 6 | 0,0076% | **Corrigir (Imputação)** | Falha de comunicação SCADA durante operação normal; imputada a média horária da própria usina no mês de março na mesma hora (~88,77 MWh/h). |
| **1. Completude** | `COMP_03_MEDICAO_USINA_NULA` | `medicao_geracao` | 1 registro manual em `20/10/2025 14:00` com `id_usina = NULL` (33,100 MWh) | 78.798 | 1 | 0,0013% | **Quarentena** | Impossível atribuir usina ou submercado para valorar ao PLD; isolado em quarentena. |
| **1. Completude** | `COMP_04_LACUNA_GRADE_HORARIA` | `medicao_geracao` | 96 horas ausentes na grade de 2025: 24h em `CRE-EOL-003` (`10/11/2025`) e 72h em `CRE-UFV-004` (`29 a 31/12/2025`) | 78.840 | 96 | 0,1218% | **Sinalizar e Completar Grade (0 MWh)** | Recompõe as 78.840 horas no DW com `0 MWh` e flag `'LACUNA_TELEMETRIA_ZERO'`, preservando a continuidade horária sem inflar faturamento sem lastro de medição. |
| **1. Completude** | `COMP_05_GRADE_PLD_2025` | `pld_horario_tratado` | Verificação das 35.040 horas $\times$ submercado de PLD em 2025 | 35.040 | 0 | 0,0000% | **Validado OK** | Série histórica CCEE 2025 100% completa nos 4 submercados. |
| **2. Unicidade** | `UNIC_01_USINA_DUPLICADA` | `usina` | Registro `id_usina = 10` (`CRE-EOL-07`, `'Eol Ventos do Potiguar '`) duplicando `CRE-EOL-007` | 10 | 1 | 10,0000% | **Descartar Duplicata** | Mantido apenas o registro canônico `CRE-EOL-007` na `dim_usina`. |
| **2. Unicidade** | `UNIC_02_MEDICAO_REPROCESSAMENTO_DUPLICADO` | `medicao_geracao` | Reprocessamento duplicou todas as 24 horas de `CRE-UFV-004` no dia `20/01/2025` (`recebido_em + 1 dia`) | 78.798 | 24 | 0,0305% | **Descartar Duplicata** | Deduplicação por `(id_usina, data_hora)` mantendo a leitura original (`ROW_NUMBER = 1`). |
| **2. Unicidade** | `UNIC_03_MEDICAO_SOBREPOSICAO_MANUAL_SCADA` | `medicao_geracao` | Lançamento `MANUAL` (+10%) sobreposto à leitura `SCADA` em `CRE-PCH-002` (`01/06/2025` das 08h às 11h) | 78.798 | 4 | 0,0051% | **Descartar Duplicata Manual** | Priorizada a telemetria primária `SCADA` e descartadas as 4 linhas manuais duplicadas. |
| **3. Validade** | `VAL_01_USINA_LATITUDE_POSITIVA_INVALIDA` | `usina` | `CRE-UFV-004` (Janaúba-MG) com `latitude = +15.803000` (hemisfério norte) | 10 | 1 | 10,0000% | **Corrigir (SCD Tipo 1)** | Corrigido o sinal para `-15.803000` na `dim_usina` e na consulta à API Open-Meteo. |
| **3. Validade** | `VAL_02_USINA_DATA_OPERACAO_FUTURA` | `usina` | `CRE-UFV-006` com `data_inicio_operacao = '2031-02-01'` | 10 | 1 | 10,0000% | **Corrigir (SCD Tipo 1)** | Data de início de operação corrigida para `'2019-02-01'` na `dim_usina`. |
| **3. Validade** | `VAL_03_MEDICAO_GERACAO_NEGATIVA` | `medicao_geracao` | 4 horas com `energia_mwh = -0.850` em `CRE-EOL-007` (`03/02/2025` das 01h às 04h) | 78.798 | 4 | 0,0051% | **Corrigir para 0,000 MWh** | Consumo de serviços auxiliares não deve abater nem gerar receita negativa de geração bruta; ajustado para `0,000 MWh`. |
| **3. Validade** | `VAL_04_MEDICAO_ANO_INVALIDO_2052` | `medicao_geracao` | Registro manual com data `'2052-05-14 10:00:00'` em `CRE-UFV-006` | 78.798 | 1 | 0,0013% | **Descartar / Quarentena** | A hora `'2025-05-14 10:00:00'` já possui medição `SCADA` normal para `CRE-UFV-006`; registro `2052` enviado à quarentena. |
| **3. Validade** | `VAL_05_PLD_FAIXA_REGULATORIA_ANEEL` | `pld_horario_tratado` | Verificação dos limites mínimo e máximo regulatórios do PLD | 64.800 | 0 | 0,0000% | **Validado OK** | 100% dos valores dentro do intervalo regulatório ANEEL. |
| **4. Consistência** | `CONS_01_USINA_UF_INCOMPATIVEL_SUBMERCADO` | `usina` | `CRE-BIO-005` (Sertãozinho, submercado `SE`, lat `-21.138`) com `uf = 'PE'` | 10 | 1 | 10,0000% | **Corrigir (SCD Tipo 1)** | Sertãozinho situa-se em São Paulo (`uf = 'SP'`); corrigido na `dim_usina`. |
| **4. Consistência** | `CONS_02_PARADA_FIM_ANTERIOR_AO_INICIO` | `parada_usina` | Parada em `CRE-UFV-004` (`05/11/2025`) com `inicio = 18:00` e `fim = 08:00` (`fim < inicio`) | 11 | 1 | 9,0909% | **Quarentena** | Registro inconsistente descartado da `fato_parada_usina` (no dia `05/11/2025` a usina operou normalmente no SCADA). |
| **4. Consistência** | `CONS_03_GERACAO_DURANTE_PARADA_MANUTENCAO` | `medicao_geracao` | 4 horas com `132,000 MWh` em `CRE-UTE-009` (`05/06/2025` 18h–21h) durante overhaul programado (`02 a 15/06`) e disponibilidade = 0 MW | 78.798 | 4 | 0,0051% | **Corrigir para 0,000 MWh** | Usina desligada para revisão geral de turbina; geração espúria zerada no DW. |
| **4. Consistência** | `CONS_04_GERACAO_ACIMA_CAPACIDADE_INSTALADA` | `medicao_geracao` | 26 leituras horárias onde `energia_mwh > potencia_mw` | 78.798 | 26 | 0,0330% | **Corrigir Escala / Interpolar** | Tratadas individualmente nas regras `ACUR_01` (24h em kWh) e `ACUR_02` (2h de pico/vírgula). |
| **5. Acurácia** | `ACUR_01_UNIDADE_KWH_EM_VEZ_DE_MWH` | `medicao_geracao` | 24 horas do dia `10/05/2025` em `CRE-BIO-005` digitadas manualmente em **kWh** ($\times 1.000$, ~31.000 MWh/h em usina de 40 MW) | 78.798 | 24 | 0,0305% | **Corrigir ($\div 1.000$)** | Dividir por 1.000 converte de kWh para MWh, eliminando **741,80 GWh** e **R\$ 165,33 milhões** de faturamento fictício! |
| **5. Acurácia** | `ACUR_02_OUTLIER_VIRGULA_E_PICO_LEITURA` | `medicao_geracao` | Vírgula deslocada ($\times 10$) em `CRE-UHE-001` (`22/04/2025 14:00`: 1.286,75 MWh) e pico absurdo em `CRE-EOL-003` (`03/07/2025 03:00`: 499,00 MWh) | 78.798 | 2 | 0,0025% | **Corrigir ($\div 10$ e Interpolação)** | Em `CRE-UHE-001` divide-se por 10 (`128,675 MWh`); em `CRE-EOL-003` interpola-se a média das horas 02h e 04h (`24,268 MWh`). |
| **5. Acurácia** | `ACUR_03_GERACAO_SOLAR_NOTURNA` | `medicao_geracao` | 4 horas noturnas (`20h às 23h` de `12/08/2025`) com `15,400 MWh` na solar `CRE-UFV-006` (radiação = 0 W/m²) | 78.798 | 4 | 0,0051% | **Corrigir para 0,000 MWh** | Geração fotovoltaica impossível à noite; zerada no DW. |
| **5. Acurácia** | `ACUR_04_MEDIDOR_TRAVADO_CONSTANTE` | `medicao_geracao` | 72 horas consecutivas (`01 a 03/09/2025`) travadas exatamente em `31,250 MWh` na `CRE-BIO-005` | 78.798 | 72 | 0,0914% | **Apenas Sinalizar** | Como `31,250 MWh` (FC = 78,1%) é fisicamente normal para biomassa na safra, o valor é mantido e sinalizado com `'SINALIZADO_MEDIDOR_TRAVADO'`. |
| **6. Integridade Referencial** | `IREF_01_MEDICAO_USINA_INEXISTENTE` | `medicao_geracao` | 24 leituras no dia `15/10/2025` com `id_usina = 99` (inexistente em `usina`) | 78.798 | 24 | 0,0305% | **Quarentena / Membro `-1`** | Enviadas para `quarentena_registros` (811,72 MWh de equipamento não cadastrado excluídos do faturamento das 9 usinas). |
| **7. Atualidade** | `ATUA_01_ATRASO_INTEGRACAO_ACIMA_24H` | `medicao_geracao` | 288 horas de `CRE-UHE-008` (`20 a 31/12/2025`) recebidas só em `10/02/2026` (+28 reprocessadas = 316) | 78.798 | 316 | 0,4010% | **Apenas Sinalizar** | Medição física válida para o ano de 2025; atraso calculado em `atraso_recebimento_horas` (~1.000h). |
| **7. Atualidade** | `ATUA_02_RECEBIDO_ANTES_DA_MEDICAO` | `medicao_geracao` | 24 horas de `CRE-PCH-002` (`05/08/2025`) com `recebido_em = data_hora - 3 dias` (+1 registro `2052`) | 78.798 | 25 | 0,0317% | **Apenas Sinalizar** | Desvio de relógio do servidor de integração sinalizado sem descartar a geração medida. |
| **7. Atualidade** | `ATUA_03_USINA_INTERRUPCAO_ENVIO_FIM_ANO` | `medicao_geracao` | `CRE-UFV-004` cessou envio de dados em `28/12/2025 23:00` (3 dias finais ausentes) | 9 | 1 | 11,1111% | **Sinalizar e Completar Grade (0 MWh)** | Sinalizada perda de telemetria nas 72 horas finais de dezembro/2025. |

---

## 4. Comparativo "Antes vs. Depois" da Limpeza nos Indicadores de Negócio

A tabela abaixo demonstra o impacto exato da limpeza de dados na geração anual (MWh) e na receita valorada ao PLD (R\$) de cada usina em 2025:

| Código | Usina | Fonte | Sub | Geração Antes (MWh) | Geração Depois (MWh) | Diferença (MWh) | Receita Antes (R\$) | Receita Depois (R\$) | Diferença na Receita (R\$) | Causa Principal da Diferença |
| :--- | :--- | :---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| `CRE-UHE-001` | UHE Campo Real Iguaçu | UHE | S | 973.833,15 | 972.675,07 | -1.158,08 | 218.649.557,94 | 218.549.847,68 | -99.710,26 | Correção de vírgula deslocada ($\times 10$) em `22/04 14:00` |
| `CRE-PCH-002` | PCH Rio das Araucárias | PCH | S | 130.846,39 | 130.789,90 | -56,49 | 28.881.614,81 | 28.878.304,84 | -3.309,97 | Remoção de 4 lançamentos manuais duplicados (+10%) em `01/06` |
| `CRE-EOL-003` | EOL Coxilha Real | EOL | S | 135.880,60 | 135.405,87 | -474,73 | 31.992.576,08 | 31.932.835,55 | -59.740,53 | Correção do pico espúrio de `499 MWh` em `03/07 03:00` |
| `CRE-UFV-004` | UFV Campo Real Minas | UFV | SE | 112.655,13 | 112.304,77 | -350,36 | 19.646.449,44 | 19.625.918,41 | -20.531,03 | Deduplicação das 24h reprocessadas em `20/01/2025` |
| `CRE-BIO-005` | UTE Bagaço Campo Real | BIO | SE | 927.792,19 | 185.989,74 | **-741.802,45** | 210.063.260,35 | 44.738.199,48 | **-165.325.060,87** | Conversão de kWh para MWh ($\div 1.000$) nas 24h de `10/05/2025` |
| `CRE-UFV-006` | UFV Sertão do Sol | UFV | NE | 168.812,80 | 168.751,20 | -61,60 | 23.084.089,56 | 23.063.621,42 | -20.468,14 | Zeramento de 4h de geração solar noturna em `12/08` (+ descarte ano `2052`) |
| `CRE-EOL-007` | EOL Ventos do Potiguar | EOL | NE | 493.793,06 | 493.796,46 | +3,40 | 97.971.739,21 | 97.971.938,45 | +199,24 | Zeramento de 4h de geração negativa (`-0,850 MWh`) em `03/02` |
| `CRE-UHE-008` | UHE Tocantins Real | UHE | N | 540.550,02 | 541.092,62 | +542,60 | 86.170.297,58 | 86.202.094,46 | +31.796,88 | Imputação das 6h `NULL` em `05/03/2025` (falha de telemetria) |
| `CRE-UTE-009` | UTE Gás Norte | UTE | N | 381.407,90 | 380.879,90 | -528,00 | 87.452.290,55 | 87.280.563,83 | -171.726,72 | Zeramento de 4h (`132 MWh/h`) lançadas durante overhaul em `05/06` |
| **Subtotal (9 Usinas)** | — | — | — | **3.865.571,24** | **3.121.685,53** | **-743.885,71** | **803.911.875,52** | **638.243.324,12** | **-165.668.551,40** | **Redução de 19,24% em energia e 20,61% em receita fantasma** |
| *Órfãos / Inválidos* | `id_usina=99`, `NULL`, `2052` | — | — | *900,02* | *0,00 (Quarentena)* | *-900,02* | *N/A (sem submercado)* | *0,00* | — | Isolados em `quarentena_registros` |
| **Total Geral Bruto** | — | — | — | **3.866.471,26** | **3.121.685,53** | **-744.785,73** | **803.911.875,52** | **638.243.324,12** | **-165.668.551,40** | — |

### Conclusão Executiva para a Diretoria
Sem o pipeline de qualidade de dados, um painel conectado diretamente às tabelas operacionais apresentaria à diretoria um faturamento anual de **R\$ 803,91 milhões** — superestimado em **R\$ 165,67 milhões (+25,96% acima do real)** quase inteiramente por causa de um único dia (`10/05/2025`) digitado em kWh na usina a biomassa `CRE-BIO-005`. Após o tratamento auditável no ETL, a geração real certificada é de **3.121,69 GWh** e a receita valorada ao PLD é de **R\$ 638,24 milhões**.

