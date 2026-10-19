> Historical project notes. Paths, operational status, badges and test claims below are retained as archival reference and have not all been revalidated. Use the root README and docs/REVIEW.md for this review snapshot.

# Wallet Forensics

Coletor local de leitura para a competição. Entrada única: ../result.json. O original é preservado; somente a vírgula ausente conhecida é reparada em memória. Não lê os demais arquivos antigos do Poker.

## Executar no PowerShell

No diretório C:\Users\PC\Desktop\Poker:

    python -m wallet_forensics audit
    python -m wallet_forensics --budget 100 probe
    python -m wallet_forensics --budget 100 discover
    python -m wallet_forensics --budget 3000 collect --messages 18330,18903 --tx-limit 100 --rounds 0
    python -m wallet_forensics report
    python -m wallet_forensics status
    python -m wallet_forensics stop
    python -m wallet_forensics verify
    python -m wallet_forensics --budget 400 seed-check --chains ethereum

Rodada ampla, retomável, incluindo controles e mensagens de DCA/saída:

    python -m wallet_forensics --budget 20000 run --chains all --windows main,control_before,control_after,edit --include-updates --tx-limit 100 --pages 20 --rounds 0

O orçamento vale por processo e inclui retries e testes de identidade. Um novo comando retoma cache e checkpoints. --rounds 0 termina quando as janelas selecionadas completam o escopo, o progresso estaciona por duas rodadas, chega o orçamento ou há pedido de parada. Não cria agendamento. O comando stop pede parada cooperativa e conserva os checkpoints; a próxima execução explícita permite retomar. Há trava para impedir dois coletores de rede no mesmo banco. Durante a coleta, os relatórios são atualizados ao concluir uma janela se passou ao menos um minuto desde o último snapshot.

Para processamento curto:

    python -m wallet_forensics --budget 500 collect --chains bsc --max-calls 2 --tx-limit 20 --pages 2

Testes sem rede:

    python -m unittest discover -s wallet_forensics/tests -v

Dependência do coletor: Python >= 3.11 e pycryptodome (Keccak-256). O ambiente usado já possuía a dependência instalada. Playwright é usado apenas na verificação visual opcional.

## Dados e evidências

output/evidence.sqlite contém registros normalizados, respostas JSON-RPC comprimidas, URLs, parâmetros, horários, erros e checkpoints. Cada negociação referencia as entradas de cache usadas como evidência. Quantidades financeiras brutas são strings inteiras; formatação decimal é exata. Não há conversão USD ou preço histórico inventado.

Os comandos rank/report funcionam localmente. --offline permite reproduzir leituras presentes no cache; cache ausente é uma lacuna, não uma resposta vazia.

O HTML contém busca, filtros por rede e downloads CSV/JSON. Pesquisa todos os registros e pagina a tabela em blocos de 50 linhas. A aba inicial Wallets e compras lista contratos/tickers por carteira e abre suas compras com quantidade, antecedência e hash. Tokens comprados permite navegar do token para as wallets. As exportações têm todos os registros. O JSON é a representação canônica exata; o CSV prefixa textos que poderiam virar fórmulas ao abrir em planilhas. A prévia HTTP, se iniciada, deve ficar limitada a 127.0.0.1.

## Semântica temporal

- O epoch da mensagem é canônico. Neste arquivo, date está em UTC-3 em todas as 1.846 mensagens.
- A janela de ranking é [T0-300, T0). A consulta inclui mais um segundo exclusivamente para registrar operações no segundo da publicação como ambíguas; elas não entram no ranking.
- Todos os textos editados têm timing_uncertain. Edições após 300 segundos ficam fora do score robusto. Isso é uma regra conservadora de triagem, não prova da disponibilidade do endereço no texto original.
- DCA, saída, resultado, cotação, observação sem posição e republicação são categorias distintas. Calls são classificadas por regras; texto e mensagem original continuam disponíveis para revisão.
- Os 70% iniciais dos tokens, arredondados para cima, são descoberta; o restante é validação global no tempo. O corte pode mudar até terminar a resolução de links para tokens. O relatório registra a regra e os totais.

## Como os compradores são determinados

EVM: Transfer ERC-20 + swap v2/v3/v4 + fluxo líquido do token. O comprador precisa coincidir com o remetente da transação ou com o sender de uma UserOperation bem-sucedida emitida por EntryPoint conhecido. Beneficiários de relayers sem vínculo resolvido ficam fora do ranking. Sender/recipient de router ou PoolManager não é assumido como usuário.

v4: PoolId tem 32 bytes. A chave de resolução inclui rede, PoolManager e PoolId. Moedas, fee, tick spacing e hooks são recuperados do evento Initialize e o hash Keccak-256 do PoolKey precisa reproduzir o PoolId. A data do par ajuda a localizar Initialize; se não houver data, a busca inicial cobre 2.000 blocos anteriores ao swap. Ausência nesse intervalo fica pendente, sem inventar moedas. Deltas v4 são normalizados para o sinal de fluxo da pool. Os logs por token também preservam negociação anterior ao pool atual; contratos sem decodificação suficiente ficam como transfer_only.

Solana: busca um bloco finalizado posterior à janela, verifica seu blockTime e usa uma assinatura real como cursor before, conforme a implementação Agave. Mantém a paginação normal como fallback. Um cursor só pula o intervalo posterior à janela; respostas vazias continuam sem comprovar cobertura. Assinaturas com erro on-chain não são compras.

Reconhece discriminadores de swaps em programas Pump, PumpSwap, Raydium AMM/CPMM/CLMM e Meteora DAMM v2, inclusive em instruções internas. Reconciliam-se saldos SPL/Token-2022 por proprietário e exige-se proprietário signer com conta de token presente na instrução do swap. Fee payer sozinho não identifica comprador. Dados insuficientes, outros programas ou metas sem proprietário não produzem evidência robusta.

A detecção de um programa/instrução não cobre todas as versões possíveis de seu ABI/IDL. Os registros brutos preservam a possibilidade de adicionar decodificadores. O program ID e o discriminador precisam corresponder; nomes em logs textuais sozinhos não são prova.

## Ranking e limites

Somente janelas completas no escopo, decodificadas na versão atual e sem edição tardia contribuem para o score robusto. Evidência de janelas parciais continua visível com score robusto zero para aquele token.

Score de descoberta: soma de 1/log2(2+número de compradores na call) por token independente. Quando controles utilizáveis existem, multiplica-se por (1-proporção de controles com atividade da wallet). Controles são janelas de cinco minutos começando em T0-30min e T0+15min; janelas sobrepostas a outras entradas são excluídas. Controles vazios ou incompletos não são baseline utilizável.

Validação temporal e dias são exibidos separadamente; não se fabrica probabilidade de identidade. A etiqueta de recorrência exige três tokens e dois dias. Recorrência na validação exige ao menos um token de descoberta e um de validação. A pista maker não concede bônus.

Volume é somado por ativo de cotação e base de cálculo. Fluxo líquido de tokens da wallet, valor de swap da pool e variação de SOL com taxas/rent são categorias distintas; esta última não entra no total de volume comparável. As unidades monetárias nunca são misturadas.

Transferências entre candidatos limitam-se às transações coletadas. Não representam varredura completa de funding; origem compartilhada ou endereço igual em redes EVM não prova controle comum.

## Cobertura e operação

complete_scope = eventos/contas descobertos foram processados no período; não significa que todas as pools, curvas, negociações ou protocolos da rede foram encontrados. Os campos pool_discovery_exhaustive e source_independently_verified permanecem false até existir prova adicional. Ethereum/BSC/Base/Robinhood/HyperEVM compartilham o coletor EVM; Solana tem paginação própria.

Exaurir uma página de histórico, obter null ou receber 403/429/timeout nunca é registrado como zero compras. Erros mantêm checkpoints para retomada. HTTP 403 desabilita o endpoint persistidamente. Reavaliação explícita:

    python -m wallet_forensics --budget 100 probe --retry-blocked

Limites: 1 requisição/segundo por endpoint RPC e serviço DEX Screener, até três requisições simultâneas. Batches de JSON-RPC não são usados. HyperEVM começa com 50 blocos por consulta; outros ranges são divididos ao exceder limites. O fallback BSC 1RPC usa https://public.1rpc.io/bnb com limite adicional local de 200 requisições por dia UTC, conforme o plano público. Responder a chainId não prova retenção histórica.

Não há assinatura, chave, broadcast, ordem ou wallet conectada. Os métodos de escrita são rejeitados pela allowlist.

## Fontes primárias consultadas

- https://docs.robinhood.com/chain/connecting/
- https://docs.bnbchain.org/bnb-smart-chain/developers/json_rpc/json-rpc-endpoint/
- https://docs.base.org/base-chain/api-reference/rpc-overview
- https://solana.com/docs/rpc/http/getsignaturesforaddress
- https://solana.com/docs/rpc/http/gettransaction
- https://solana.com/docs/rpc/http/getblocktime
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/json-rpc
- https://drpc.org/docs/ethereum-api
- https://www.publicnode.com/
- https://docs.1rpc.io/using-the-web3-api/networks
- https://docs.1rpc.io/using-the-web3-api/errors
- https://raw.githubusercontent.com/anza-xyz/agave/master/ledger/src/blockstore.rs
- https://raw.githubusercontent.com/anza-xyz/agave/master/storage-bigtable/src/lib.rs
- https://docs.dexscreener.com/api/reference
- https://github.com/Uniswap/v4-core/blob/main/src/interfaces/IPoolManager.sol
- https://developers.uniswap.org/docs/protocols/v4/deployments
- https://raw.githubusercontent.com/raydium-io/raydium-sdk-V2/master/src/common/programId.ts
- https://raw.githubusercontent.com/MeteoraAg/damm-v2-sdk/main/src/constants.ts
- https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump.json
- https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump_amm.json

Consulta: 2026-09-10. Disponibilidade pública e retenção histórica dependem do provedor; os resultados reais ficam no banco.

## Arquivos entregues

- output/report.html: relatório local com busca, filtros, evidências e downloads.
- output/calls.csv e calls.json: mensagens, contratos, UTC/SP, classificação e edições.
- output/trades.csv e trades.json: compras/vendas e transferências ainda sem classificação, quantidades exatas e hashes.
- output/candidates.csv e candidates.json: recorrência por token, dias, descoberta, validação e controles.
- output/activity_ranking.csv e volume_ranking.csv: rankings auxiliares, com volume separado por ativo e base de cálculo.
- output/coverage.csv e rpc_coverage.csv: janelas processadas, lacunas e resultados por endpoint/método. As datas de rpc_coverage indicam quando a consulta ocorreu; os parâmetros preservam o período on-chain solicitado.
- output/v4_pools.json: PoolManagers, PoolKeys verificados e resoluções pendentes.
- output/seeds.json: pistas explícitas e seu contexto, sem bônus de identidade.
- output/evidence.sqlite: respostas brutas, logs de requisições e checkpoints.

Para repetir o smoke visual local, após gerar o relatório:

    python wallet_forensics/tests/ui_smoke.py

Os testes unitários usam fixtures sintéticas. As provas de rede são as respostas registradas no SQLite; resultados de fixtures não são apresentados como blockchain real.

## Endpoints configurados

| Rede | Identidade | RPCs públicos de leitura |
|---|---|---|
| Robinhood | 4663 | https://rpc.mainnet.chain.robinhood.com ; https://robinhood-rpc.publicnode.com |
| BSC | 56 | https://bsc.publicnode.com ; https://bsc.drpc.org ; https://public.1rpc.io/bnb |
| Base | 8453 | https://mainnet.base.org ; https://base-rpc.publicnode.com |
| Ethereum | 1 | https://eth.drpc.org ; https://ethereum.publicnode.com |
| HyperEVM | 999 | https://rpc.hyperliquid.xyz/evm ; https://hyperliquid-json-rpc.stakely.io |
| Solana | mainnet genesis validado | https://api.mainnet-beta.solana.com ; https://solana-rpc.publicnode.com |

São alternativas configuradas, não garantia de funcionamento de todos os métodos. Um worker por rede evita que uma rede longa impeça as outras de começar; o semáforo global limita HTTP a três consultas simultâneas e cada endpoint conserva o intervalo mínimo de um segundo.

seed-check pesquisa apenas o token resolvido na mensagem da pista, em [T0-1h,T0+5min). Exporta seed_activity e seed_checks. Esse período ampliado não entra no ranking de cinco minutos. Sem token resolvido, a pista fica pendente em vez de iniciar uma varredura indiscriminada de patrimônio.

verify compara o hash original, unicidade das calls primárias, split temporal, janelas das negociações, ausência de atribuição de propriedade, integridade do SQLite e a existência/decodificação das respostas usadas como evidência. Seu resultado fica em output/verification.json.

## Leitura direta dos resultados

Aba inicial Wallets e compras: cada linha corresponde a uma carteira em uma rede. Tokens comprados conta contratos distintos com compra reconciliada na janela principal; Quais tokens mostra nomes/tickers, com contrato no tooltip. Clique na carteira ou em Ver compras para inspecionar cada quantidade, call e transação. Clique em um ticker para listar seus compradores.

Tokens no score continua separado: só conta períodos completos na versão do decoder atual e sem edição tardia. Score zero não significa ausência de compras. Os CSVs wallets.csv, purchases.csv e tokens.csv exportam essas visões. O score original permanece na aba Candidatos.

A última rodada ampla encerrou por atingir 20.000 requisições. O relatório é um snapshot local; atualizar a página não reinicia a coleta.
