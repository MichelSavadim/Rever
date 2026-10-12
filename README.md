# Rever

Aplicativo de desktop em Python para Windows. Importa PDFs com texto selecionável, exibe as páginas e gera narração em MP3 com Edge TTS.

Depois da instalação, abra **Iniciar Rever.cmd** com dois cliques para iniciar o aplicativo.

Se ocorrer uma falha, consulte o arquivo `logs/crash_DATA_HORA.log` mais recente na pasta do projeto. Cada abertura cria um arquivo próprio com a data e a hora no nome. Ele registra exceções do Python, mensagens do Qt e rastros de falhas nativas. O inicializador também salva a saída do console em `logs/console.log`. Envie os trechos finais desses arquivos ao relatar um problema; eles podem conter caminhos locais dos seus PDFs.

## Instalação

Requer Python 3.11 ou mais recente e acesso à internet para instalar as dependências e gerar a voz.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m pdf_reader
```

## Uso

1. Use **Arquivo → Adicionar PDF à biblioteca** (`Ctrl+O`). O arquivo é copiado para a biblioteca interna.
2. Selecione o PDF na barra esquerda.
3. Em **Configurações → Preferências de voz** (`Ctrl+,`), escolha idioma e voz. O padrão é português brasileiro.
4. Pressione **Play**. O áudio do parágrafo atual é gerado sob demanda e o próximo é preparado como buffer. O destaque amarelo acompanha o parágrafo reproduzido mesmo com a ferramenta de seleção desligada. Ative **Selecionar parágrafo** e clique no texto para começar de outro ponto.
   Os controles do player usam ícones para Play/Pause, saltos de 10 segundos, parágrafo e página. O ícone central alterna entre Play e Pause conforme a reprodução.
5. Use **Gerar MP3 completo** ou escolha um intervalo de páginas para criar um arquivo permanente. O botão **Cancelar geração**, o **Tempo decorrido** e o **Tempo estimado de conclusão** aparecem somente durante a geração. Ao cancelar, escolha entre continuar, cancelar e apagar os trechos criados nesta tentativa, ou cancelar e preservar esses trechos. Áudios anteriores são preservados e nenhum MP3 final incompleto é publicado. O caminho do arquivo concluído aparece no painel direito, com um botão para abri-lo.
6. Arraste as divisórias para redimensionar os painéis. O menu **Exibir** controla a visibilidade e oferece **Tema escuro**, lembrado na próxima abertura. As barras laterais recolhem e abrem com animação; as setas de reabertura ficam junto à margem, na altura do cabeçalho.

Na barra acima do PDF, **Ajustar largura** redefine o zoom para encaixar a página na largura da área de leitura. Digite uma página no campo superior e pressione **Enter** ou **Ir** para visualizá-la sem iniciar o áudio. A pesquisa nessa barra (`Ctrl+F`) encontra texto no PDF; use as setas da pesquisa para percorrer os resultados destacados.

O menu **Configurações → Atalhos de teclado** mostra as teclas disponíveis. **Ajuda → Sobre / README** abre este arquivo.

A biblioteca fica em `data/library` dentro da pasta do projeto. Cada PDF recebe uma pasta identificada pelo conteúdo do arquivo, com `document.pdf`, `thumbnail.png`, `paragraphs.json`, `audio` (trechos permanentes usados nas exportações), `exports` (MP3s completos ou por intervalo) e `temp_audio` durante a reprodução. Ao trocar de PDF ou fechar o aplicativo, os arquivos temporários são removidos. A biblioteca da primeira versão em `%LOCALAPPDATA%\PDFReaderTTS\library` é copiada automaticamente na primeira abertura, se existir.

O leitor guarda a última página vista e o parágrafo selecionado ou reproduzido em `reader_state.json`, dentro da pasta de cada PDF. Ao voltar ao arquivo ou reiniciar o aplicativo, abre a última página vista; o parágrafo guardado permanece como ponto de partida do Play, mesmo se estiver em outra página. O último PDF aberto também é lembrado.

Em **Arquivo → Excluir PDF selecionado da biblioteca**, escolha entre ocultá-lo da biblioteca mantendo o PDF importado, os áudios e o ponto de leitura, ou apagar toda a pasta desse PDF. Se ocultar, importar o mesmo PDF novamente recupera os arquivos preservados.

## Gerar um executável para Windows

No PowerShell, com as dependências do projeto já instaladas:

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
& '.\Gerar EXE.cmd'
```

O arquivo final fica em `Rever.exe`, na raiz do projeto. Ele inclui Python, bibliotecas, ícones e README; não é necessário instalar Python no computador que vai executá-lo. Ao ser executado na raiz, ele usa a pasta `data/library` existente e grava os logs em `logs`. PDFs e MP3s da biblioteca não são embutidos no EXE. Se você levar o executável para outra pasta, copie também a pasta `data` para preservar a biblioteca. A pasta do executável precisa permitir escrita. A geração de voz continua exigindo internet.

O ícone do aplicativo está em `pdf_reader/assets/icons/rever.svg`. Para atualizar as versões PNG e ICO após editar o SVG, execute `.\.venv\Scripts\python.exe tools\make_icon.py` antes de gerar o executável.

## Limitações desta versão

- PDF digitalizado sem texto selecionável precisa de OCR e não é importado.
- Os blocos de texto do PDF são tratados como parágrafos. Alguns layouts, especialmente colunas, cabeçalhos e tabelas, podem exigir ajuste manual em uma versão futura.
- Pontilhados longos de sumários são resumidos para o TTS e para a estimativa. Blocos compostos apenas por escrita não suportada pelas vozes disponíveis são ignorados na narração e continuam visíveis no PDF.
- Edge TTS usa um serviço online. A reprodução dos MP3 já gerados funciona sem internet.
- Se a geração mostrar HTTP 403, o serviço Edge TTS recusou a conexão de áudio. A biblioteca de PDFs e os MP3 já salvos continuam disponíveis; tente novamente mais tarde ou em outra rede. O detalhe técnico fica no crashlog da sessão.
- A velocidade de reprodução altera apenas o player; arquivos exportados usam a velocidade original da voz.
- A visualização mostra as páginas em sequência, com rolagem contínua. As setas da barra de ferramentas saltam para a página anterior ou seguinte.
- A barra do player representa o progresso no PDF inteiro. A duração total é estimada a partir do texto na importação e corrigida conforme os MP3s reais são gerados. O tempo da barra considera a velocidade de reprodução; o campo "Ouvido" soma o tempo da sessão entre parágrafos e pausa junto com o áudio. Por ser uma estimativa, o total pode mudar durante a leitura; a duração exata só é conhecida após gerar todos os trechos.
