# Estado da implementação

Entrada autorizada: somente `../result.json`. Os outros arquivos antigos do Poker não foram lidos para esta implementação. Código, evidências e relatórios ficam nesta pasta nova.

## Entregue

- Parser com reparo sintático estrito em memória, hash do original, UTC/SP, edições, links ocultos, replies, contratos/mints e pistas explícitas.
- Coleta de leitura nas seis redes; cache SQLite, checkpoints, limites por endpoint, orçamento, fallback, bloqueio 403, Retry-After e parada cooperativa.
- Reconciliação EVM v2/v3/v4, incluindo PoolManager + Initialize + conferência do hash do PoolKey; routers e remetentes ERC-4337 tratados separadamente.
- Solana com âncora histórica real, paginação, instruções internas, signer/proprietário, Token-2022 e falhas.
- Rankings por recorrência, quantidade e volume separado por ativo; divisão temporal 70/30 e controles.
- Relatório HTML local, CSV/JSON e verificações automatizadas.

## Validação

50 testes unitários passaram. São fixtures sintéticas cobrindo limites temporais, deduplicação, edições, v4, routers, smart accounts, Token-2022, transações falhadas, paginação, retomada, RPC indisponível, precisão numérica, HTML/CSV e pista maker.

O smoke visual em Chromium headless passou em desktop e mobile: busca, filtros, sete abas, downloads e ausência de erro JavaScript/overflow. As capturas ficam em output/report-desktop.png e output/report-mobile.png.

A verificação de evidências reais fica em output/verification.json. O hash original é 4f3468ec127674c1feb9bd2be8e5be84251a551a7e3ad4252feb21999b801840.

## Achado da pista maker

A carteira 0x29e6b3a3b3f377a07ad23b41cf0f292bb86d3aee executou duas compras e duas vendas de NOMEME no período ampliado da mensagem 18903:

| UTC em 05/09/2026 | Movimento | Antecedência |
|---|---|---|
| 01:34:23 | Compra | 16m13s |
| 01:38:35 | Compra | 12m01s |
| 01:40:11 | Venda | 10m25s |
| 01:40:35 | Venda | 10m01s |

A mensagem é de 01:50:36 UTC. Essas operações estão fora dos cinco minutos principais e não dão bônus ao ranking. Hashes, recibos e transferências estão em output/seed_activity.json e no SQLite. Não demonstram pertencimento ao autor.

## Limites que continuam abertos

- A coleta completa das 418 calls primárias ainda não terminou; números de candidatos são preliminares.
- BSC: o bloco histórico da primeira call não ficou disponível nos fallbacks testados; 403/429/null são lacunas.
- Solana: a janela foi alcançada e há swaps reais, mas restam milhares de assinaturas; a criação de um pool dentro da janela exige validar o início de seu histórico antes de certificar cobertura completa.
- Curvas/protocolos ainda não reconhecidos permanecem como transfer_only ou pendentes. O coletor não afirma descobrir todas as pools encerradas/migrações históricas.
- Controles ainda precisam de mais períodos completos e não vazios. Não há candidato validado em três tokens e dois dias neste snapshot inicial.
- Token/pool e direção são evidências on-chain; identidade pessoal e controle comum não foram confirmados.

## Próximo trabalho operacional

Continuar a coleta ampla com o comando documentado no README. As chamadas terminam por orçamento, conclusão, estagnação ou stop; toda retomada usa o banco existente. Não há agendamento nem transações de escrita.

Snapshot documental em 2026-09-10T06:01:11.532552+00:00.

- Calls extraídas: 491.
- Linhas de negociações/transferências: 965.
- Cobertura por estado: {'complete_scope': 6, 'error': 1, 'excluded_overlapping_call': 1, 'partial': 5}.

## Coleta ampla iniciada

Em 2026-09-10T06:02:27Z foi iniciado o processo local 17384, com orçamento de 20.000 consultas e escopo main nas seis redes, incluindo atualizações de posição. Isso prioriza ampliar tokens independentes antes de gastar a rodada ampla com todos os controles. O arquivo output/active_run.json contém PID, logs e comando de parada. PID é um registro de início, não garantia de que continuará ativo ao ler este documento.

A próxima fase de análise são os controles dos candidatos recorrentes, seguidos da validação temporal e revisão manual das mensagens. O pipeline implementa esses controles, mas esta rodada ampla executa primeiro as janelas principais.

## Correção da apresentação dos resultados — 2026-09-10T11:25Z

A revisão confirmou dois defeitos: a tabela inicial não ligava a wallet aos tokens comprados, e o HTML carregava apenas 5.000 das 12.659 transações, tornando a busca incompleta. Corrigidos com visão Wallets e compras, Tokens comprados, detalhe de compras por carteira e paginação sobre todo o conjunto.

Estado real desta revisão: 2.963 compras em janelas primárias, 1.990 wallets por rede, 63 contratos com compra reconhecida; somente 25 das 418 calls primárias têm coleta completa no escopo. A rodada ampla terminou ao atingir 20.000 requisições; não foi reiniciada nesta correção. BSC teve 100 janelas com erro; o conjunto continua parcial.

51 testes unitários, smoke desktop/mobile e regressão de navegação wallet -> 42 compras -> hashes passaram. A verificação do SQLite e de 15.546 entradas de cache também passou. Houve conferência direta dos 42 recibos da carteira 0x9f46ff59dd7c885f30ab937456623e86323bb0f8: sucesso, remetente e fluxo líquido positivo do token coincidem com o banco. Evidência em output/wallet-review.json. Isso não prova propriedade pelo autor.

Próximo trabalho investigativo: completar janelas dos candidatos recorrentes, obter controles comparáveis e resolver o histórico BSC. A correção da interface utilizou os dados já coletados e preservou result.json.
