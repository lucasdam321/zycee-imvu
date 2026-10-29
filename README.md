# IMVU → OBJ (cache local)

Conversor em Python 3.10+ de malhas Cal3D para abrir no Blender. Sem bibliotecas externas, login ou download de produtos. Lê arquivos locais; não modifica o cache. Interface para Windows com IMVU Classic, e comandos de terminal para Windows/Linux/macOS.

## Começar no Windows

1. Instale **Python 3.10 ou superior** de python.org, com **Tcl/Tk** e o launcher `py` (opções do instalador).
2. Extraia o ZIP do programa inteiro. Não execute de dentro do ZIP.
3. Abra `Iniciar.bat`. Também é possível executar `py -3 -m imvu_obj gui` na pasta extraída.
4. Selecione `%APPDATA%\IMVU\HttpCache`, no seu caso `C:\Users\User\AppData\Roaming\IMVU\HttpCache`. A ferramenta percorre as subpastas; feche o IMVU durante a leitura para evitar arquivos mudando.
5. Informe o PID e clique **Buscar PID no cache**, depois **Exportar produto por PID**. Escolha saída fora do cache, por exemplo `C:\Users\User\IMVU-OBJ`.
6. Para um arquivo específico, selecione **Arquivo direto** e clique **Converter arquivo direto**. O arquivo pode não ter extensão.

Cada malha gera `.obj`, `.mtl` neutro e `.json` com origem, SHA-256, associação do PID, contagens, transformações e limitações. Saídas existentes são protegidas: use outra pasta para repetir. O terminal permite `--overwrite` explicitamente.

## O exemplo PID 74667074

O arquivo `639d4a659f55b4181683aa01e02e7ed0` fornecido é **CMF versão 919**, com **2.079 vértices, 3.662 triângulos, 1 submalha, normais e UVs**. A leitura consome os 177.040 bytes exatamente. Não está criptografado.

Ele **não contém o PID**. A associação ao PID 74667074 veio da informação do usuário; o programa reconhece **esse conteúdo exato** pelo SHA-256 `e435d146055e2dfe91a94b890abcda5717bddd14317e960d53ed38cedfeb042a`, mesmo se o arquivo for renomeado. O nome hash sozinho não permite descobrir a identidade de outros produtos. O programa inclui apenas esse hash/associação, sem incluir o asset no código-fonte.

**O OBJ representa somente a malha do arquivo fornecido.** Um produto pode depender de várias malhas, materiais, texturas, esqueleto e produtos ancestrais. A amostra não comprova que o produto inteiro esteja presente nela. Não há promessa de reconstrução integral a partir de um PID arbitrário.

## Como funciona a busca por PID

A associação usa, nesta ordem:

- SHA-256 da amostra acima ou associação cadastrada explicitamente por você;
- PID isolado em componente do caminho, por exemplo `Product_74667074/mesh`, `74667074/mesh` ou `74667074.cmf`. Isso é uma pista da organização local, não autenticação da origem. Confira o relatório;
- padrão real do HttpCache **`product<PID>_<hash32>`**, por exemplo `15/08/product74667074_1508083db59c9bb708c5bd532bb5b85f`. Os diretórios `15/08` são partições do hash e não são tratados como PIDs;
- sem nenhuma dessas evidências, a malha aparece como **sem PID** e não é exportada como se pertencesse ao produto solicitado.

Também procura malhas dentro de ZIPs, CFL3 e DFL3, descompactando em memória sem extrair caminhos do contêiner para o disco. Não interpreta bancos de dados/índices proprietários do Classic, não consulta servidores IMVU e não determina o PID de um arquivo hash desconhecido por tentativa. Para arquivos hash sem PID no nome e sem vínculo conhecido, a associação ainda depende de um índice real do cache ou de informação fornecida por você.

Desde a versão **1.2**, **Buscar PID no cache** encontra arquivos `product<PID>_<hash32>` mesmo quando seu conteúdo não é reconhecido, mostra caminho/tamanho/formato e seleciona o arquivo automaticamente no campo **Arquivo direto**. O resultado separa **arquivo de produto localizado** de **malha reconhecida**. **Exportar produto por PID** tenta abrir automaticamente as malhas do contêiner suportado. Não é preciso procurar o arquivo manualmente. Se o conteúdo continuar desconhecido ou não contiver geometria, a ferramenta informa essa limitação e não gera um OBJ vazio.

O diagnóstico enviado pelo usuário confirmou o nome e o tamanho do arquivo `product74667074_…`, mas não incluiu seus bytes. Portanto o suporte a CFL/DFL não comprova que esse arquivo específico seja um desses formatos. O arquivo `d31e8f…` selecionado no screenshot foi identificado como imagem pelo diagnóstico; imagens são recusadas como fonte direta de OBJ.

### Quando a busca não encontra o PID no HttpCache

Selecione `%APPDATA%\IMVU\HttpCache`, informe o PID e clique **Diagnosticar HttpCache**. O JSON será salvo na pasta de saída, que deve ficar fora do cache. O programa prefere `HttpCache` como pasta padrão quando ela existe.

O diagnóstico registra nomes relativos, tamanhos, assinaturas de formatos, possíveis arquivos de índice e quais arquivos têm uma ocorrência literal do PID nos trechos examinados. Também procura a assinatura CMF nos primeiros 64 KiB para identificar malhas que não começam no byte zero. Não contém conteúdo bruto de arquivos, cabeçalhos HTTP ou valores de cookies, e não executa/edita o cache. Os nomes dos arquivos ficam visíveis; revise o relatório antes de compartilhá-lo.

**Uma ocorrência do PID não prova que todas as malhas do mesmo arquivo pertençam ao produto.** O relatório serve para investigar o layout real do cache; não resolve o vínculo automaticamente. Limites de leitura/listagem e resultados incompletos são informados. Um PID não encontrado nos trechos lidos pode existir em outra região, em um índice compactado ou fora dessa pasta. Enviar apenas o JSON de diagnóstico permite iniciar a análise sem enviar o cache inteiro.

Se souber qual arquivo pertence a outro PID, use **Associar arquivo ao PID** na interface, ou o comando `associate`. O índice fica em `~/IMVU-OBJ/product-index.json`, separado do cache, e armazena apenas SHA-256→PID. Para ZIPs, a associação manual abrange todas as malhas do ZIP: use apenas um contêiner que você confirmou pertencer ao produto. Associações conflitantes são recusadas.

## Abrir no Blender

Em Blender 4.x: **Arquivo → Importar → Wavefront (.obj)**, selecione o OBJ e mantenha o MTL na mesma pasta. Use **Home** ou enquadrar seleção para localizar a malha. O OBJ mantém os eixos originais; para preservar coordenadas Z-up, ajuste os eixos do importador para Forward **Y**, Up **Z**. A opção **Centralizar** é útil: a amostra original fica perto de Z=1010. Escala `0.01` reduz o tamanho para 1% se necessário; o programa não presume uma unidade física.

UVs e normais são mantidos; **texturas não são recriadas** a partir de geometria. O MTL é cinza neutro. Para recuperar a aparência, são necessários os arquivos de materiais/imagens correspondentes e seu vínculo com a malha. Esqueleto, skinning, animações e morph targets não fazem parte do OBJ; exporta-se a geometria base estática, sem aplicar pose de avatar.

## Terminal

Abra o terminal na pasta do programa. No Linux/macOS, substitua `py -3` por `python3`.

```bat
py -3 -m imvu_obj inspect "C:\caminho\639d4a659f55b4181683aa01e02e7ed0"
py -3 -m imvu_obj convert "C:\caminho\639d4a659f55b4181683aa01e02e7ed0" --pid 74667074 --center -o "C:\Users\User\IMVU-OBJ"
py -3 -m imvu_obj scan "C:\Users\User\AppData\Roaming\IMVU" --pid 74667074
py -3 -m imvu_obj export "C:\Users\User\AppData\Roaming\IMVU" --pid 74667074 --center -o "C:\Users\User\IMVU-OBJ"
py -3 -m imvu_obj associate "C:\caminho\malha_confirmada" --pid 12345678
py -3 -m imvu_obj diagnose "C:\Users\User\AppData\Roaming\IMVU\HttpCache" --pid 74667074 -o "C:\Users\User\IMVU-OBJ\diagnostico_74667074.json"
```

`scan` e `export` aceitam `--index caminho.json`; `associate` também, para manter índices separados. Um PID fornecido a `convert` é somente um rótulo de saída, não cadastra associação automaticamente. Código de saída 0 indica sucesso, 1 falha e 130 interrupção. Um erro de conversão impede a escrita do lote antes de criar arquivos OBJ; o cache nunca é alterado.

## Formatos e limites

- CMF binário little endian versões 699–919; layout público Cal3D/IMVU, com cores/morph count desde 911. A amostra real foi validada em 919; outras versões são cobertas por fixtures sintéticas.
- XMF XML com HEADER/MESH irmãos ou MESH raiz, faces triangulares e IDs de vértice explícitos.
- ZIP comum e contêineres **CFL3/DFL3** contendo essas malhas, sem extrair caminhos para o disco. CFL/DFL aceita blocos brutos ou LZMA no formato do IMVU. Não trata outras variantes desconhecidas, criptografia ou fluxos compactados avulsos.
- Apenas primeiro canal UV no OBJ; cores, LOD progressivo, física e morph targets são omitidos. A leitura percorre esses campos do CMF para localizar as faces.
- Arquivo/entrada até 64 MiB; ZIP até 128 MiB descompactado e 10.000 entradas. Sem symlinks na busca. Não dá permissão de acesso a pastas que o seu usuário não consegue ler.
- Rejeita truncamento, índices de face inexistentes, números não finitos, versões desconhecidas e bytes finais inesperados. Avisos e arquivos inacessíveis são relatados por `scan`.

## Desenvolvimento e verificação

```sh
python3 -m unittest discover -s tests -v
IMVU_SAMPLE_PATH=/caminho/da/amostra python3 -m unittest discover -s tests -v
```

Sem `IMVU_SAMPLE_PATH`, os testes do asset externo são marcados como skipped. Os demais usam fixtures independentes, incluindo rejeição de arquivos malformados, offsets OBJ, UVs, associações e ZIPs. Não é preciso instalar dependências. Para interface no Linux, o Python precisa de Tk e um display gráfico; os comandos de terminal funcionam sem display.

A amostra real exportada foi importada no **Blender 4.3.2**, com 2.079 vértices, 3.662 polígonos triangulares, 1 canal UV e 1 material. `Mesh.validate()` não precisou reparar a malha. OBJ/MTL, `.blend` e prévia foram gerados separadamente para validação; **os assets 3D não estão incluídos neste repositório ou no ZIP do código**. A interface Tk/launcher foi verificada em código; a execução visual no Windows ainda precisa ser confirmada no seu computador.

Referência do formato: [loader oficial Cal3D/IMVU, commit 8522b45](https://github.com/imvu/cal3d/blob/8522b45cdc26960f779323cc9a0ef50678eda7de/src/cal3d/loader.cpp#L931) e [constantes de versão](https://github.com/imvu/cal3d/blob/8522b45cdc26960f779323cc9a0ef50678eda7de/src/cal3d/global.h#L52). Implementação independente em Python; nenhum executável ou código do cache é executado.

Referências CFL/DFL: [LibCFL, commit 8541594](https://github.com/Toyz/LibCFL/tree/8541594e34e6e99ece4c42096663ade112335f4b) e [cfl-converter, commit 25c408f](https://github.com/dhkatz/cfl-converter/tree/25c408ffd769410a44babdcc88f7397e8aea6b02), ambos MIT. Veja os créditos em `THIRD_PARTY_NOTICES.md`. O programa usa apenas o cache local; não implementa downloads de produtos.
