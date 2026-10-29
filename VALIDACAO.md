# Validação da entrega

- Python 3.12.14, sem dependências externas.
- 43 testes unittest passaram com `IMVU_SAMPLE_PATH` definido, incluindo o arquivo fornecido pelo usuário. Sem esse arquivo externo, dois testes são explicitamente pulados; os demais usam fixtures independentes.
- Busca na pasta da amostra pelo PID 74667074 encontrou exatamente uma malha pelo SHA-256 informado no programa.
- Exportação por PID gerou OBJ/MTL/JSON; conversão direta e associações de outros PIDs estão cobertas por fixtures.
- Blender 4.3.2 importou o OBJ exportado: 1 objeto, 2.079 vértices, 3.662 triângulos, 1 canal UV, 1 material. `Mesh.validate()` retornou False (sem reparos). O arquivo `.blend` foi salvo e a prévia PNG renderizada a partir da importação.
- Geometria centralizada; eixos originais preservados; nenhuma textura foi inferida.
- Interface desktop compilada, mas não executada visualmente no Windows. `Iniciar.bat` exige Python 3.10+ com Tcl/Tk. O ambiente de validação não tem display gráfico.
- O cache completo do usuário não está disponível nesta máquina: sua estrutura e índices proprietários não foram validados. A associação de PIDs desconhecidos a arquivos hash continua dependendo de evidência/índice local fornecido pelo usuário. O programa informa quando essa associação falta.
- Versão 1.1: diagnóstico HttpCache testado com fixtures ASCII/UTF-16, assinaturas/offsets de malha, identificação de SQLite sem examinar seus registros, limites de varredura, preservação dos arquivos originais e proteção de saída. O comando de diagnóstico também foi executado na pasta da amostra real; gerar relatório não equivale a resolver PID→malha.

Reproduzir os testes na pasta do programa:

```sh
IMVU_SAMPLE_PATH=/caminho/da/amostra python3 -m unittest discover -s tests -v
```

O arquivo de exemplo é um asset externo e não é necessário para rodar os testes sintéticos nem para usar o programa com seus próprios arquivos.
